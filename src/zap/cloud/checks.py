"""Checks against the live warehouse: what the analyst role can reach, and whether Snowflake
and DuckDB built the same marts from the same data."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
from snowflake.connector.errors import ProgrammingError

from zap.warehouse.fingerprint import business_columns, table_columns

MASK = "***MASKED***"
ANALYST_ROLE = "ZAP_ANALYST"

# 002003: does not exist or not authorized. 003001 and 003003: insufficient privileges.
DENIED_ERRNOS = frozenset({2003, 3001, 3003})
# The delete matches no rows, so even a wrong grant would change nothing.
ACCESS_CHECKS: list[tuple[str, str, bool]] = [
    ("read a mart", "SELECT COUNT(*) FROM MARTS.MART_DAILY_KPIS", True),
    ("read customers", "SELECT COUNT(*) FROM MARTS.DIM_CUSTOMER", True),
    ("read governance", "SELECT COUNT(*) FROM GOVERNANCE.DQ_RESULTS", True),
    ("read raw users", "SELECT COUNT(*) FROM RAW.USERS", False),
    ("read staging users", "SELECT COUNT(*) FROM STAGING.STG_USERS", False),
    ("delete from a mart", "DELETE FROM MARTS.MART_DAILY_KPIS WHERE 1 = 0", False),
    ("list the S3 stage", "LIST @RAW.LAKE/raw/restaurant/", False),
]
UNMASKED_ROWS = (
    "SELECT COUNT(*) FROM MARTS.DIM_CUSTOMER "  # noqa: S608 - constant
    f"WHERE EMAIL_HASH <> '{MASK}' OR EMAIL_MASKED <> '{MASK}'"
)

NUMERIC_PREFIXES = ("BIGINT", "INTEGER", "HUGEINT", "SMALLINT", "TINYINT", "DECIMAL", "DOUBLE")
REL_TOLERANCE = 1e-9  # far above float noise, far below one rounded digit in a sum


def _allowed(cur: Any, sql: str) -> bool:
    try:
        cur.execute(sql)
    except ProgrammingError as err:
        if err.errno in DENIED_ERRNOS:
            return False
        raise
    return True


def run_access_checks(con: Any) -> dict[str, Any]:
    """Run every check as the deployer, then as the analyst, and report both."""
    cur = con.cursor()
    try:
        # Snowflake activates all of a user's roles by default, which would hide a missing
        # restriction: this session must hold exactly one role.
        cur.execute("USE SECONDARY ROLES NONE")
        unrunnable = [name for name, sql, _ in ACCESS_CHECKS if not _allowed(cur, sql)]
        if unrunnable:
            raise RuntimeError(f"fail for the deployer too, so they prove nothing: {unrunnable}")
        deployer_unmasked = int(cur.execute(UNMASKED_ROWS).fetchall()[0][0])

        cur.execute(f"USE ROLE {ANALYST_ROLE}")
        checks = [
            {"check": name, "expected_allowed": expected, "allowed": _allowed(cur, sql)}
            for name, sql, expected in ACCESS_CHECKS
        ]
        analyst_unmasked = int(cur.execute(UNMASKED_ROWS).fetchall()[0][0])
    finally:
        cur.close()
    return {
        "role": ANALYST_ROLE,
        "checks": checks,
        "deployer_unmasked_rows": deployer_unmasked,
        "analyst_unmasked_rows": analyst_unmasked,
        "passed": all(c["allowed"] == c["expected_allowed"] for c in checks)
        and deployer_unmasked > 0
        and analyst_unmasked == 0,
    }


def profile_exprs(columns: list[tuple[str, str]]) -> dict[str, str]:
    """Aggregates that mean the same on both engines, labelled `column:measure`."""
    exprs = {"rows": "COUNT(*)"}
    for name, dtype in business_columns(columns):
        exprs[f"{name}:non_null"] = f"COUNT({name})"
        if dtype.startswith(NUMERIC_PREFIXES):
            value = f"CAST({name} AS DOUBLE)"
            exprs[f"{name}:sum"] = f"SUM({value})"
            # Errors of opposite sign can cancel in a sum; they don't cancel here.
            exprs[f"{name}:sum_sq"] = f"SUM({value} * {value})"
        elif dtype == "BOOLEAN":
            exprs[f"{name}:true"] = f"SUM(CASE WHEN {name} THEN 1 ELSE 0 END)"
        else:
            exprs[f"{name}:distinct"] = f"COUNT(DISTINCT {name})"
    return exprs


def values_match(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, int | float | Decimal) and isinstance(b, int | float | Decimal):
        return math.isclose(float(a), float(b), rel_tol=REL_TOLERANCE, abs_tol=REL_TOLERANCE)
    return bool(a == b)


@dataclass
class TableCompare:
    table: str
    duckdb_rows: int
    snowflake_rows: int
    measures: int
    mismatches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def identical(self) -> bool:
        return not self.mismatches


def compare_engines(
    con: Any, db_path: Path, schemas: tuple[str, ...] = ("marts", "governance")
) -> list[TableCompare]:
    """Profile every table on both engines. Row hashes can't cross engines, aggregates can."""
    duck = duckdb.connect(str(db_path), read_only=True)
    cur = con.cursor()
    results: list[TableCompare] = []
    try:
        for (schema, table), columns in table_columns(duck, schemas).items():
            exprs = profile_exprs(columns)
            select = ", ".join(exprs.values())
            from_clause = f"FROM {schema}.{table}"
            left = duck.execute(f"SELECT {select} {from_clause}").fetchall()[0]
            right = cur.execute(f"SELECT {select} {from_clause}").fetchall()[0]
            results.append(
                TableCompare(
                    table=f"{schema}.{table}",
                    duckdb_rows=int(left[0]),
                    snowflake_rows=int(right[0]),
                    measures=len(exprs),
                    mismatches=[
                        {"measure": label, "duckdb": str(a), "snowflake": str(b)}
                        for label, a, b in zip(exprs, left, right, strict=True)
                        if not values_match(a, b)
                    ],
                )
            )
    finally:
        cur.close()
        duck.close()
    return results


def write_report(name: str, payload: dict[str, Any], artifacts_dir: Path) -> Path:
    path = artifacts_dir / "cloud" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {**payload, "ran_at": datetime.now(UTC).isoformat()}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def compare_summary(results: list[TableCompare], dataset: str) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "tables": len(results),
        "tables_identical": sum(r.identical for r in results),
        "measures": sum(r.measures for r in results),
        "results": [asdict(r) for r in results],
    }
