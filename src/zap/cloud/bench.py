"""Snowflake benchmarks whose numbers feed METRICS.md. Each checks correctness before timing.

* `incremental`: delete the newest day from RAW, `dbt run --full-refresh`, load the day back
  from S3, `dbt run`. The facts must end up exactly as they were before the cycle.
* `clustering`: one date-range query on the same rows in three physical orders, with the
  result cache off. Reports partitions scanned as well as time, because the warehouse's own
  disk cache warms up between runs and pruning does not depend on it.
"""

from __future__ import annotations

import json
import statistics
import time
from collections.abc import Callable
from datetime import date
from typing import Any

from zap.bench import FACTS, dbt_elapsed, model_seconds
from zap.cloud.snowflake import load_raw
from zap.dbt_runner import DbtRun
from zap.ingest.contracts import Contract
from zap.sqlutil import quote_literal

DATE_RANGE_QUERY = """
    SELECT CITY, SUM(CASE WHEN IS_DELIVERED THEN SALES_AMOUNT ELSE 0 END) AS GMV, COUNT(*) AS ORDERS
    FROM {table}
    WHERE ORDER_DATE > DATEADD(DAY, -30, {end}::DATE)
    GROUP BY CITY ORDER BY GMV DESC, CITY LIMIT 10
"""
FACT_TOTALS = """
    SELECT (SELECT COUNT(*) FROM MARTS.FCT_ORDERS),
           (SELECT SUM(SALES_AMOUNT) FROM MARTS.FCT_ORDERS),
           (SELECT COUNT(*) FROM MARTS.FCT_ORDER_ITEMS),
           (SELECT COUNT(*) FROM MARTS.FCT_REVIEWS)
"""
LAYOUTS = {
    "as_built_by_dbt": ("MARTS.FCT_ORDERS", None),
    "shuffled": ("RAW._BENCH_SHUFFLED", "RANDOM()"),
    "sorted_by_date": ("RAW._BENCH_SORTED", "ORDER_DATE"),
}


def _last_day(cur: Any) -> date:
    value: date = cur.execute("SELECT MAX(ORDER_DATE) FROM RAW.ORDERS").fetchall()[0][0]
    return value


def _totals(cur: Any) -> list[str]:
    return [str(value) for value in cur.execute(FACT_TOTALS).fetchall()[0]]


def _incremental_cycle(
    con: Any,
    contracts: dict[str, Contract],
    prefix: str,
    dataset: str,
    day: date,
    dbt: Callable[[list[str]], DbtRun],
) -> dict[str, Any]:
    cur = con.cursor()
    held: dict[str, int] = {}
    try:
        for table, (column, _) in FACTS.items():
            where = f"{column.upper()} = {quote_literal(str(day))}"
            sql = f"DELETE FROM RAW.{table.upper()} WHERE {where}"  # noqa: S608 - fixed identifiers
            held[table] = int(cur.execute(sql).fetchall()[0][0])
        full = dbt(["run", "--full-refresh"])
    finally:
        load_raw(con, contracts, prefix, dataset, list(FACTS), day)
    inc = dbt(["run"])
    totals = _totals(cur)
    cur.close()
    if not (full.ok and inc.ok):
        raise RuntimeError("dbt run failed during the incremental benchmark")
    fact_ids = {uid for _, uid in FACTS.values()}
    return {
        "new_rows": held,
        "full_refresh_dbt_s": dbt_elapsed(full),
        "incremental_dbt_s": dbt_elapsed(inc),
        "facts_full_refresh_s": model_seconds(full, fact_ids),
        "facts_incremental_s": model_seconds(inc, fact_ids),
        "totals": totals,
    }


def bench_incremental(
    con: Any,
    contracts: dict[str, Contract],
    prefix: str,
    dataset: str,
    dbt: Callable[[list[str]], DbtRun],
    repeats: int = 3,
) -> dict[str, Any]:
    cur = con.cursor()
    day = _last_day(cur)
    before = _totals(cur)
    cur.close()
    runs = [_incremental_cycle(con, contracts, prefix, dataset, day, dbt) for _ in range(repeats)]
    keys = [
        "full_refresh_dbt_s",
        "incremental_dbt_s",
        "facts_full_refresh_s",
        "facts_incremental_s",
    ]
    med = {k: round(statistics.median(r[k] for r in runs), 3) for k in keys}
    return {
        "benchmark": "incremental_vs_full_refresh",
        "repeats": repeats,
        "day": str(day),
        "new_rows": runs[0]["new_rows"],
        **med,
        "saved_pct": round(100 * (1 - med["incremental_dbt_s"] / med["full_refresh_dbt_s"]), 1),
        "facts_saved_pct": round(
            100 * (1 - med["facts_incremental_s"] / med["facts_full_refresh_s"]), 1
        ),
        "result_correct": all(r["totals"] == before for r in runs),
        "raw_runs": runs,
    }


def _pruning(cur: Any, query_id: str) -> dict[str, int]:
    stats_of = f"GET_QUERY_OPERATOR_STATS({quote_literal(query_id)})"
    rows = cur.execute(
        f"SELECT OPERATOR_STATISTICS FROM TABLE({stats_of}) "  # noqa: S608 - id from the connector
        "WHERE OPERATOR_TYPE = 'TableScan'"
    ).fetchall()
    stats = json.loads(rows[0][0])["pruning"]
    return {"scanned": int(stats["partitions_scanned"]), "total": int(stats["partitions_total"])}


def _measure(cur: Any, table: str, end: date, repeats: int) -> dict[str, Any]:
    sql = DATE_RANGE_QUERY.format(table=table, end=quote_literal(str(end)))
    timings, rows = [], []
    query_id = ""
    for _ in range(repeats):
        started = time.perf_counter()
        rows = cur.execute(sql).fetchall()
        timings.append(round((time.perf_counter() - started) * 1000, 1))
        query_id = cur.sfqid
    info = json.loads(
        cur.execute(
            f"SELECT SYSTEM$CLUSTERING_INFORMATION({quote_literal(table)}, '(ORDER_DATE)')"
        ).fetchall()[0][0]
    )
    return {
        "median_ms": statistics.median(timings),
        "runs_ms": timings,
        "partitions": _pruning(cur, query_id),
        "average_depth": info["average_depth"],
        "answer": [[str(value) for value in row] for row in rows],
    }


def bench_clustering(con: Any, repeats: int = 5) -> dict[str, Any]:
    cur = con.cursor()
    temporary = [table for table, order in LAYOUTS.values() if order]
    try:
        cur.execute("ALTER SESSION SET USE_CACHED_RESULT = FALSE")
        end = _last_day(cur)
        for table, order in LAYOUTS.values():
            if order:
                cur.execute(
                    f"CREATE OR REPLACE TRANSIENT TABLE {table} AS "  # noqa: S608 - fixed identifiers
                    f"SELECT * FROM MARTS.FCT_ORDERS ORDER BY {order}"
                )
        measured = {
            label: _measure(cur, table, end, repeats) for label, (table, _) in LAYOUTS.items()
        }
    finally:
        for table in temporary:
            cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.close()
    answers = [m.pop("answer") for m in measured.values()]
    return {
        "benchmark": "clustering",
        "repeats": repeats,
        "result_cache": "off",
        "results_identical": all(a == answers[0] for a in answers),
        **measured,
    }
