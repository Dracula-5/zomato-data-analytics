"""Schema-drift detection: compare an incoming CSV against its contract before loading it.

Severity rules:

* **breaking**: a contract column is missing (removed or renamed), or a typed column holds
  values that can't be cast to its contract type. The load must stop.
* **warn**: the file has extra columns (they are ignored) or the columns are reordered.
  Reading by name, not position, makes both safe, unlike a positional `COPY INTO`.
* **ok**: exact match.
* **missing_file**: the file isn't there. Reported, not fatal, so partial datasets still load.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import duckdb

from zap.ingest.contracts import Contract
from zap.sqlutil import fetch_one, quote_ident, quote_literal

DriftStatus = Literal["ok", "warn", "breaking", "missing_file"]


@dataclass
class DriftReport:
    table: str
    file: str
    status: DriftStatus
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    reordered: bool = False
    index_column_mismatch: bool = False
    type_violations: dict[str, int] = field(default_factory=dict)
    rows: int | None = None
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def breaking(self) -> bool:
        return self.status == "breaking"

    def summary(self) -> str:
        if self.status == "missing_file":
            return "file not found in source folder"
        parts = []
        if self.removed:
            parts.append(f"missing columns {self.removed}")
        if self.type_violations:
            parts.append(f"uncastable values {self.type_violations}")
        if self.added:
            parts.append(f"extra columns {self.added} (ignored)")
        if self.reordered:
            parts.append("columns reordered (read by name)")
        if self.index_column_mismatch:
            parts.append("index column presence differs from contract")
        return "; ".join(parts) or "matches contract"


def read_header(path: Path) -> list[str]:
    """First CSV row; `utf-8-sig` strips a byte-order mark if one is present."""
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return next(csv.reader(fh), [])


def csv_relation_sql(path: Path) -> str:
    """`read_csv` call used by both drift checks and ingestion, so they parse identically."""
    return (
        f"read_csv({quote_literal(path)}, header=true, all_varchar=true, delim=',', "
        "quote='\"', escape='\"', strict_mode=true)"
    )


def check_drift(contract: Contract, data_dir: Path, con: duckdb.DuckDBPyConnection) -> DriftReport:
    path = data_dir / contract.file
    report = DriftReport(table=contract.table, file=contract.file, status="ok")
    if not path.exists():
        report.status = "missing_file"
        return report

    header = read_header(path)
    has_index = bool(header) and header[0] == ""
    actual = header[1:] if has_index else header
    expected = contract.column_names
    report.index_column_mismatch = has_index != contract.has_index_column
    report.added = [c for c in actual if c not in expected]
    report.removed = [c for c in expected if c not in actual]
    report.reordered = not report.added and not report.removed and actual != expected

    typed = [c for c in contract.columns if c.type != "VARCHAR" and c.name in actual]
    checks = [
        f"count(*) filter (where {quote_ident(c.name)} is not null and "
        f"try_cast({quote_ident(c.name)} as {c.type}) is null)"
        for c in typed
    ]
    sql = f"select {', '.join(['count(*)', *checks])} from {csv_relation_sql(path)}"  # noqa: S608 - quoted via sqlutil
    row = fetch_one(con, sql)
    report.rows = int(row[0])
    report.type_violations = {c.name: int(n) for c, n in zip(typed, row[1:], strict=True) if n}

    if report.removed or report.type_violations:
        report.status = "breaking"
    elif report.added or report.reordered or report.index_column_mismatch:
        report.status = "warn"
    return report


def append_drift_log(reports: list[DriftReport], log_path: Path, run_id: str) -> None:
    """Append one JSON line per report: the audit trail later loaded into governance tables."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        for r in reports:
            fh.write(json.dumps({"run_id": run_id, **asdict(r)}) + "\n")
