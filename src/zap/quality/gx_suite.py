"""Great Expectations suites that gate the lake BEFORE it is loaded into the warehouse.

Division of labour with dbt tests:

* **GX (here)** checks each incoming file on its own: keys present and unique, values in
  allowed sets and ranges. It runs before the load, so bad data never reaches the warehouse.
* **dbt tests** check the modelled warehouse: relationships between tables, reconciliation
  of totals, contracts on marts.

Each expectation has a severity. A failed `error` expectation fails the gate; a failed
`warn` is reported but lets the load continue. Only problems the warehouse cannot repair
are errors (missing keys, unknown statuses, negative money). Duplicates are warnings:
staging de-duplicates them and the governance layer counts them. DuckDB reads only the
columns the suite needs from Parquet, and GX validates them as a pandas DataFrame.
"""

from __future__ import annotations

import json
import logging
import time
import warnings
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from zap.ingest.contracts import Contract
from zap.ingest.lake import parquet_glob, table_dir
from zap.sqlutil import quote_ident, quote_literal

Spec = tuple[str, dict[str, Any], str]  # (expectation class name, kwargs, severity)

STATUSES = ["Delivered", "Cancelled", "Refunded"]
PAYMENTS = ["UPI", "Card", "Wallet", "COD", "NetBanking"]


def suite_specs() -> dict[str, list[Spec]]:
    """Expectations per lake table. Kept as data so they are easy to read and review."""
    money = ["subtotal", "discount", "delivery_fee", "gst", "sales_amount"]
    return {
        "orders": [
            ("ExpectTableRowCountToBeBetween", {"min_value": 1}, "error"),
            ("ExpectColumnValuesToNotBeNull", {"column": "order_id"}, "error"),
            ("ExpectColumnValuesToBeUnique", {"column": "order_id"}, "warn"),
            ("ExpectColumnValuesToNotBeNull", {"column": "order_date"}, "error"),
            (
                "ExpectColumnValuesToBeInSet",
                {"column": "order_status", "value_set": STATUSES},
                "error",
            ),
            (
                "ExpectColumnValuesToBeInSet",
                {"column": "payment_method", "value_set": PAYMENTS},
                "warn",
            ),
            *[
                ("ExpectColumnValuesToBeBetween", {"column": m, "min_value": 0}, "error")
                for m in money
            ],
            (
                "ExpectColumnValuesToBeBetween",
                {"column": "customer_rating", "min_value": 1, "max_value": 5},
                "warn",
            ),
            (
                "ExpectColumnValuesToBeBetween",
                {"column": "delivery_time_min", "min_value": 1, "max_value": 180},
                "warn",
            ),
        ],
        "order_items": [
            ("ExpectColumnValuesToNotBeNull", {"column": "order_item_id"}, "error"),
            ("ExpectColumnValuesToBeUnique", {"column": "order_item_id"}, "warn"),
            ("ExpectColumnValuesToBeBetween", {"column": "quantity", "min_value": 1}, "error"),
            ("ExpectColumnValuesToBeBetween", {"column": "price", "min_value": 0}, "error"),
        ],
        "reviews": [
            ("ExpectColumnValuesToNotBeNull", {"column": "review_id"}, "error"),
            ("ExpectColumnValuesToBeUnique", {"column": "review_id"}, "warn"),
            (
                "ExpectColumnValuesToBeBetween",
                {"column": "rating", "min_value": 1, "max_value": 5},
                "error",
            ),
            ("ExpectColumnValuesToNotBeNull", {"column": "comment", "mostly": 0.99}, "warn"),
        ],
        "restaurant": [
            ("ExpectColumnValuesToNotBeNull", {"column": "id"}, "error"),
            ("ExpectColumnValuesToBeUnique", {"column": "id"}, "warn"),
            ("ExpectColumnValuesToMatchRegex", {"column": "id", "regex": r"^\d+$"}, "error"),
            (
                "ExpectColumnValuesToMatchRegex",
                {"column": "rating", "regex": r"^(--|[0-5](\.\d)?)$", "mostly": 0.99},
                "warn",
            ),
        ],
        "food": [
            ("ExpectColumnValuesToBeUnique", {"column": "f_id"}, "warn"),
            ("ExpectColumnValuesToMatchRegex", {"column": "f_id", "regex": r"^fd\d+$"}, "error"),
            (
                "ExpectColumnValuesToBeInSet",
                {"column": "veg_or_non_veg", "value_set": ["Veg", "Non-veg"]},
                "warn",
            ),
        ],
        "menu": [
            ("ExpectCompoundColumnsToBeUnique", {"column_list": ["menu_id", "f_id"]}, "warn"),
            ("ExpectColumnValuesToNotBeNull", {"column": "r_id"}, "error"),
        ],
        "users": [
            ("ExpectColumnValuesToBeUnique", {"column": "user_id"}, "warn"),
            (
                "ExpectColumnValuesToMatchRegex",
                {"column": "email", "regex": r"^[^@\s]+@[^@\s]+\.[^@\s]+$", "mostly": 0.99},
                "warn",
            ),
        ],
    }


@dataclass
class ExpectationResult:
    table: str
    expectation: str
    column: str | None
    severity: str
    success: bool
    element_count: int | None
    unexpected_count: int | None


@dataclass
class GxReport:
    dataset: str
    partition_date: str | None
    validated_at: str
    results: list[ExpectationResult] = field(default_factory=list)
    rows_validated: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def gate_passed(self) -> bool:
        return not any(r.severity == "error" and not r.success for r in self.results)

    @property
    def failed(self) -> list[ExpectationResult]:
        return [r for r in self.results if not r.success]

    @classmethod
    def from_json(cls, path: Path) -> GxReport:
        data = json.loads(path.read_text(encoding="utf-8"))
        results = [ExpectationResult(**r) for r in data.pop("results")]
        for derived in ("gate_passed", "expectations", "expectations_failed"):
            data.pop(derived, None)
        return cls(**data, results=results)

    def to_json(self) -> str:
        data = asdict(self)
        data["gate_passed"] = self.gate_passed
        data["expectations"] = len(self.results)
        data["expectations_failed"] = len(self.failed)
        return json.dumps(data, indent=2)


def _columns_needed(specs: list[Spec]) -> list[str]:
    cols = {str(kw["column"]) for _, kw, _ in specs if "column" in kw}
    cols |= {str(c) for _, kw, _ in specs for c in kw.get("column_list", [])}
    return sorted(cols)


def read_frame(
    contract: Contract, lake_dir: Path, columns: list[str], partition_date: date | None
) -> pd.DataFrame:
    """Only the needed columns, from the whole table or from one daily partition."""
    if partition_date is not None and contract.partition_by:
        pattern = table_dir(lake_dir, contract.table) / (
            f"{contract.partition_by}={partition_date}/*.parquet"
        )
    else:
        pattern = parquet_glob(lake_dir, contract)
    cols = ", ".join(quote_ident(c) for c in columns) or "1 AS _one"
    sql = f"SELECT {cols} FROM read_parquet({quote_literal(pattern)}, hive_partitioning=false)"  # noqa: S608 - quoted via sqlutil
    return duckdb.connect().execute(sql).df()


def validate_frame(table: str, frame: pd.DataFrame, specs: list[Spec]) -> list[ExpectationResult]:
    import great_expectations as gx  # heavy import, deferred until needed

    context = gx.get_context(mode="ephemeral")
    batch = (
        context.data_sources.add_pandas(name=f"lake_{table}")
        .add_dataframe_asset(name=table)
        .add_batch_definition_whole_dataframe("whole")
        .get_batch(batch_parameters={"dataframe": frame})
    )
    results = []
    for class_name, kwargs, severity in specs:
        expectation = getattr(gx.expectations, class_name)(**kwargs)
        outcome = batch.validate(expectation)
        res = outcome.result or {}
        results.append(
            ExpectationResult(
                table=table,
                expectation=class_name,
                column=kwargs.get("column") or ",".join(kwargs.get("column_list", [])) or None,
                severity=severity,
                success=bool(outcome.success),
                element_count=res.get("element_count", res.get("observed_value")),
                unexpected_count=res.get("unexpected_count"),
            )
        )
    return results


def validate_lake(
    contracts: dict[str, Contract],
    lake_dir: Path,
    dataset: str,
    tables: list[str] | None = None,
    partition_date: date | None = None,
) -> GxReport:
    t0 = time.perf_counter()
    logging.getLogger("great_expectations").setLevel(logging.ERROR)
    report = GxReport(
        dataset=dataset,
        partition_date=str(partition_date) if partition_date else None,
        validated_at=datetime.now(UTC).isoformat(),
    )
    for table, specs in suite_specs().items():
        if (tables is not None and table not in tables) or table not in contracts:
            continue
        contract = contracts[table]
        if not table_dir(lake_dir, table).exists() or (
            partition_date is not None and not contract.partition_by
        ):
            report.skipped.append(table)
            continue
        frame = read_frame(contract, lake_dir, _columns_needed(specs), partition_date)
        if frame.empty and partition_date is not None:
            report.skipped.append(table)  # no rows arrived for that day
            continue
        report.rows_validated[table] = len(frame)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            report.results += validate_frame(table, frame, specs)
    report.seconds = round(time.perf_counter() - t0, 3)
    return report


def write_gx_report(report: GxReport, artifacts_dir: Path) -> Path:
    folder = artifacts_dir / "quality" / report.dataset
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "gx_results.json"
    path.write_text(report.to_json(), encoding="utf-8")
    return path
