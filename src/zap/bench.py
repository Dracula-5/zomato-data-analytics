"""Benchmarks whose numbers feed METRICS.md. Each one checks correctness before timing.

* `incremental`: hold out the newest day(s) of facts, run `dbt run --full-refresh`, put the
  day back, run `dbt run` (incremental MERGE), then verify the fact table holds exactly the
  rows a full refresh would. Reports dbt's own elapsed time (excludes process start-up)
  for the whole project and for the fact models alone.
* `query`: the same business question answered from the 10M-row fact table and from the
  pre-aggregated mart. The answers must match; then each query is run several times on a
  read-only connection and the median is reported. DuckDB has no result cache, so repeated
  runs measure execution, not a cached lookup.
"""

from __future__ import annotations

import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from zap.dbt_runner import DbtRun, run_dbt
from zap.sqlutil import fetch_one

FACTS = {
    "orders": ("order_date", "model.zomato.fct_orders"),
    "order_items": ("order_date", "model.zomato.fct_order_items"),
    "reviews": ("review_date", "model.zomato.fct_reviews"),
}

QUERIES: dict[str, tuple[str, str]] = {
    "city_gmv_last_30_days": (
        """select city, sum(case when is_delivered then sales_amount else 0 end) as gmv,
                  count(*) as orders
           from marts.fct_orders
           where order_date > (select max(order_date) from marts.fct_orders) - interval 30 day
           group by city order by gmv desc, city limit 10""",
        """select city, sum(gmv) as gmv, sum(orders) as orders
           from marts.mart_city_daily
           where order_date > (select max(order_date) from marts.mart_city_daily) - interval 30 day
           group by city order by gmv desc, city limit 10""",
    ),
    "monthly_national_gmv": (
        """select date_trunc('month', order_date) as month,
                  sum(case when is_delivered then sales_amount else 0 end) as gmv
           from marts.fct_orders group by 1 order by 1""",
        """select date_trunc('month', order_date) as month, sum(gmv) as gmv
           from marts.mart_daily_kpis group by 1 order by 1""",
    ),
}


def _model_seconds(run: DbtRun, unique_ids: set[str]) -> float:
    return round(
        sum(
            float(r.get("execution_time") or 0)
            for r in run.run_results.get("results", [])
            if r.get("unique_id") in unique_ids
        ),
        3,
    )


def _dbt_elapsed(run: DbtRun) -> float:
    return round(float(run.run_results.get("elapsed_time") or 0.0), 3)


def bench_incremental(
    db_path: Path, project: Path, holdout_days: int = 1, repeats: int = 3
) -> dict[str, Any]:
    """Median of `repeats` full-refresh/incremental cycles; raw values are kept too."""
    runs = [_incremental_cycle(db_path, project, holdout_days) for _ in range(repeats)]
    keys = [
        "full_refresh_dbt_s",
        "incremental_dbt_s",
        "facts_full_refresh_s",
        "facts_incremental_s",
        "wall_full_refresh_s",
        "wall_incremental_s",
    ]
    med = {k: round(statistics.median(r[k] for r in runs), 3) for k in keys}
    return {
        "benchmark": "incremental_vs_full_refresh",
        "repeats": repeats,
        "holdout_days": holdout_days,
        "new_rows": runs[0]["new_rows"],
        **med,
        "saved_pct": round(100 * (1 - med["incremental_dbt_s"] / med["full_refresh_dbt_s"]), 1),
        "facts_saved_pct": round(
            100 * (1 - med["facts_incremental_s"] / med["facts_full_refresh_s"]), 1
        ),
        "result_correct": all(r["result_correct"] for r in runs),
        "fct_orders_rows": runs[-1]["fct_orders_rows"],
        "raw_runs": runs,
    }


def _incremental_cycle(db_path: Path, project: Path, holdout_days: int) -> dict[str, Any]:
    con = duckdb.connect(str(db_path))
    last = fetch_one(con, "select max(order_date) from raw.orders")[0]
    held: dict[str, int] = {}
    try:
        for table, (col, _) in FACTS.items():
            cond = f"{col} > cast(? as date) - interval {holdout_days} day"
            con.execute(
                f"create or replace table raw._holdout_{table} as "  # noqa: S608 - fixed identifiers
                f"select * from raw.{table} where {cond}",
                [last],
            )
            con.execute(f"delete from raw.{table} where {cond}", [last])  # noqa: S608 - fixed identifiers
            held[table] = int(fetch_one(con, f"select count(*) from raw._holdout_{table}")[0])  # noqa: S608
        con.close()
        full = run_dbt(["run", "--full-refresh"], db_path, project, capture=True)
        con = duckdb.connect(str(db_path))
    finally:
        for table in FACTS:
            con.execute(f"insert into raw.{table} select * from raw._holdout_{table}")  # noqa: S608
            con.execute(f"drop table raw._holdout_{table}")
        con.close()

    inc = run_dbt(["run"], db_path, project, capture=True)
    if not (full.ok and inc.ok):
        raise RuntimeError("dbt run failed during the incremental benchmark")

    con = duckdb.connect(str(db_path), read_only=True)
    expected = fetch_one(
        con,
        "select count(distinct order_id) - (select count(distinct record_key) "
        "from governance.dq_quarantine where table_name = 'orders') from raw.orders",
    )[0]
    actual = fetch_one(con, "select count(*) from marts.fct_orders")[0]
    con.close()

    fact_ids = {uid for _, uid in FACTS.values()}
    return {
        "new_rows": held,
        "full_refresh_dbt_s": _dbt_elapsed(full),
        "incremental_dbt_s": _dbt_elapsed(inc),
        "facts_full_refresh_s": _model_seconds(full, fact_ids),
        "facts_incremental_s": _model_seconds(inc, fact_ids),
        "wall_full_refresh_s": full.seconds,
        "wall_incremental_s": inc.seconds,
        "result_correct": int(expected) == int(actual),
        "fct_orders_rows": int(actual),
    }


def bench_queries(db_path: Path, repeats: int = 7) -> dict[str, Any]:
    con = duckdb.connect(str(db_path), read_only=True)
    out: dict[str, Any] = {"benchmark": "fact_scan_vs_aggregate_mart", "repeats": repeats}
    for name, (fact_sql, mart_sql) in QUERIES.items():
        same = con.execute(fact_sql).fetchall() == con.execute(mart_sql).fetchall()
        timings: dict[str, float] = {}
        for label, sql in (("fact", fact_sql), ("mart", mart_sql)):
            runs = []
            for _ in range(repeats):
                t0 = time.perf_counter()
                con.execute(sql).fetchall()
                runs.append((time.perf_counter() - t0) * 1000)
            timings[label] = round(statistics.median(runs), 2)
        out[name] = {
            "fact_median_ms": timings["fact"],
            "mart_median_ms": timings["mart"],
            "speedup_x": round(timings["fact"] / timings["mart"], 1) if timings["mart"] else None,
            "results_identical": same,
        }
    con.close()
    return out


def write_bench(result: dict[str, Any], artifacts_dir: Path, dataset: str) -> Path:
    folder = artifacts_dir / "bench" / dataset
    folder.mkdir(parents=True, exist_ok=True)
    result = {**result, "dataset": dataset, "ran_at": datetime.now(UTC).isoformat()}
    path = folder / f"{result['benchmark']}.json"
    path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return path
