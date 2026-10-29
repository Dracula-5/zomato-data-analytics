"""One data-quality report from three sources:

1. Great Expectations results (the pre-load gate on the lake);
2. dbt test results from `run_results.json` (generic, singular and unit tests);
3. the `governance.dq_results` table (per-record business rules).

The summary goes to `artifacts/quality/<dataset>/dq_report.json`, and the detail rows are
written into the warehouse (`governance.gx_results`, `governance.dbt_test_results`) so
the dashboard can show them next to the rule results.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from zap.quality.gx_suite import GxReport
from zap.sqlutil import quote_ident


def dbt_test_rows(run_results: dict[str, Any]) -> list[dict[str, Any]]:
    """Test outcomes from dbt's run_results.json (data tests and unit tests)."""
    rows = []
    for r in run_results.get("results", []):
        uid = str(r.get("unique_id", ""))
        if not uid.startswith(("test.", "unit_test.")):
            continue
        rows.append(
            {
                "unique_id": uid,
                "kind": "unit" if uid.startswith("unit_test.") else "data",
                "status": r.get("status"),
                "failures": r.get("failures") or 0,
                "seconds": round(float(r.get("execution_time") or 0.0), 3),
            }
        )
    return rows


def summarize_dbt(rows: list[dict[str, Any]]) -> dict[str, Any]:
    passed = sum(r["status"] == "pass" for r in rows)
    return {
        "tests": len(rows),
        "data_tests": sum(r["kind"] == "data" for r in rows),
        "unit_tests": sum(r["kind"] == "unit" for r in rows),
        "passed": passed,
        "failed": sum(r["status"] in ("fail", "error") for r in rows),
        "warned": sum(r["status"] == "warn" for r in rows),
        "pass_rate_pct": round(100.0 * passed / len(rows), 2) if rows else None,
    }


def rule_results(con: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cur = con.execute(
        "SELECT rule_id, table_name, severity, action, rows_checked, rows_failed "
        "FROM governance.dq_results ORDER BY rows_failed DESC, rule_id"
    )
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def summarize_rules(rules: list[dict[str, Any]], quarantined: int) -> dict[str, Any]:
    return {
        "rules": len(rules),
        "rules_fired": sum(r["rows_failed"] > 0 for r in rules),
        "records_flagged": sum(int(r["rows_failed"]) for r in rules),
        "records_quarantined": quarantined,
    }


def _write_table(con: duckdb.DuckDBPyConnection, name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    con.execute("CREATE SCHEMA IF NOT EXISTS governance")
    con.register("incoming_rows", pd.DataFrame(rows))
    target = f"governance.{quote_ident(name)}"
    con.execute(f"CREATE OR REPLACE TABLE {target} AS SELECT * FROM incoming_rows")  # noqa: S608
    con.unregister("incoming_rows")


def build_report(
    db_path: Path,
    dataset: str,
    run_results: dict[str, Any],
    gx: GxReport | None,
    artifacts_dir: Path,
) -> dict[str, Any]:
    tests = dbt_test_rows(run_results)
    con = duckdb.connect(str(db_path))
    try:
        rules = rule_results(con)
        quarantined = int(
            (con.execute("SELECT count(*) FROM governance.dq_quarantine").fetchone() or (0,))[0]
        )
        stamp = datetime.now(UTC).isoformat()
        _write_table(con, "dbt_test_results", [{**t, "reported_at": stamp} for t in tests])
        if gx is not None:
            gx_rows = [{**asdict(r), "reported_at": stamp} for r in gx.results]
            _write_table(con, "gx_results", gx_rows)
    finally:
        con.close()

    report = {
        "dataset": dataset,
        "generated_at": datetime.now(UTC).isoformat(),
        "dbt": summarize_dbt(tests),
        "gx": None
        if gx is None
        else {
            "expectations": len(gx.results),
            "failed": len(gx.failed),
            "failed_errors": sum(r.severity == "error" for r in gx.failed),
            "gate_passed": gx.gate_passed,
            "rows_validated": gx.rows_validated,
        },
        "rules": summarize_rules(rules, quarantined),
        "rule_results": rules,
    }
    folder = artifacts_dir / "quality" / dataset
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dq_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
