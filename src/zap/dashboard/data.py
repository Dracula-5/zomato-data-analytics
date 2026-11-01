"""Every query the dashboard runs. Pages only call these functions and draw the result.

Each call opens a short-lived READ-ONLY DuckDB connection: the dashboard never holds a
lock, so a dbt build can run at the same time (DuckDB allows one writer process).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


def available_datasets(warehouse_dir: Path) -> list[str]:
    """Warehouse files that have been built, best first (full, then samples, then fixture)."""
    names = [p.stem for p in warehouse_dir.glob("*.duckdb")]
    return sorted(names, key=lambda n: (n != "full", n == "fixture", n))


def query(db: Path, sql: str, params: list[Any] | None = None) -> pd.DataFrame:
    # Replacement scans off: otherwise a missing table name can silently resolve to a
    # Python variable of the same name in the calling code.
    con = duckdb.connect(str(db), read_only=True, config={"python_enable_replacements": False})
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()


def kpi_summary(db: Path) -> dict[str, Any]:
    row = query(
        db,
        """select min(order_date) as first_day, max(order_date) as last_day,
                  sum(orders) as orders, sum(delivered_orders) as delivered, sum(gmv) as gmv,
                  sum(gmv) * 1.0 / nullif(sum(delivered_orders), 0) as aov,
                  sum(cancel_rate * orders) / sum(orders) as cancel_rate,
                  sum(sla_breach_rate * delivered_orders) / sum(delivered_orders) as sla_breach_rate
           from marts.mart_daily_kpis""",
    ).iloc[0]
    customers = query(db, "select count(*) as n from marts.dim_customer where lifetime_orders > 0")
    summary: dict[str, Any] = {str(k): v for k, v in row.items()}
    summary["customers"] = int(customers["n"].iloc[0])
    return summary


def daily_kpis(db: Path) -> pd.DataFrame:
    return query(db, "select * from marts.mart_daily_kpis order by order_date")


def weekday_hour(db: Path) -> pd.DataFrame:
    """Orders by ISO weekday (1 = Monday) and hour of day."""
    return query(
        db,
        """select isodow(order_date) as weekday, order_hour as hour, count(*) as orders
           from marts.fct_orders group by 1, 2 order by 1, 2""",
    )


def city_summary(db: Path, start: date, end: date) -> pd.DataFrame:
    return query(
        db,
        """select city, sum(orders) as orders, sum(delivered_orders) as delivered_orders,
                  sum(gmv) as gmv,
                  round(sum(gmv) * 1.0 / nullif(sum(delivered_orders), 0), 1) as aov,
                  round(sum(cancelled_orders) * 100.0 / sum(orders), 2) as cancel_rate_pct
           from marts.mart_city_daily
           where order_date between ? and ?
           group by city order by gmv desc""",
        [start, end],
    )


def city_daily(db: Path, city: str) -> pd.DataFrame:
    return query(
        db,
        "select * from marts.mart_city_daily where city = ? order by order_date",
        [city],
    )


def top_restaurants(db: Path, city: str, n: int = 20) -> pd.DataFrame:
    return query(
        db,
        """select gmv_rank_in_city as rank, restaurant_name, locality, primary_cuisine,
                  listed_rating, cost_for_two, orders, gmv, aov,
                  round(cancel_rate * 100, 2) as cancel_rate_pct, avg_review_rating, reviews
           from marts.mart_restaurant_performance
           where city = ? and gmv_rank_in_city <= ?
           order by gmv_rank_in_city, restaurant_name""",
        [city, n],
    )


def delivery_sla(db: Path, top_cities: int = 12) -> pd.DataFrame:
    """SLA by hour for the busiest cities."""
    return query(
        db,
        """with busiest as (
               select city from marts.mart_delivery_sla
               group by city order by sum(delivered_orders) desc limit ?
           )
           select s.* from marts.mart_delivery_sla s
           inner join busiest b on s.city = b.city
           order by s.city, s.order_hour""",
        [top_cities],
    )


def dq_results(db: Path) -> pd.DataFrame:
    return query(
        db,
        """select rule_id, table_name, severity, action, rows_checked, rows_failed, failed_pct,
                  description
           from governance.dq_results order by rows_failed desc, rule_id""",
    )


def dbt_test_summary(db: Path) -> pd.DataFrame:
    try:
        return query(
            db,
            """select kind, status, count(*) as tests from governance.dbt_test_results
               group by kind, status order by kind, status""",
        )
    except duckdb.CatalogException:
        return pd.DataFrame(columns=["kind", "status", "tests"])


def gx_results(db: Path) -> pd.DataFrame:
    try:
        return query(
            db,
            """select "table", "column", expectation, severity, success, unexpected_count
               from governance.gx_results order by success, "table", "column", expectation""",
        )
    except duckdb.CatalogException:
        return pd.DataFrame(
            columns=["table", "column", "expectation", "severity", "success", "unexpected_count"]
        )


def has_forecasts(db: Path) -> bool:
    try:
        query(db, "select 1 from forecast.accuracy limit 1")
    except duckdb.CatalogException:
        return False
    return True


def forecast_scopes(db: Path) -> list[str]:
    """National first, then cities alphabetically."""
    frame = query(
        db,
        """select distinct scope from forecast.accuracy
           order by case when scope = 'National' then 0 else 1 end, scope""",
    )
    return [str(scope) for scope in frame["scope"]]


def forecast_accuracy(db: Path, metric: str, scope: str) -> pd.DataFrame:
    return query(
        db,
        """select model, mape, smape, bias_pct from forecast.accuracy
           where metric = ? and scope = ? order by mape""",
        [metric, scope],
    )


def best_forecast_model(db: Path, metric: str) -> str:
    """Lowest mean MAPE across all series, as in the backtest report."""
    frame = query(
        db,
        """select model from forecast.accuracy where metric = ?
           group by model order by avg(mape), model limit 1""",
        [metric],
    )
    return str(frame["model"].iloc[0])


def forecast_lines(db: Path, metric: str, scope: str, model: str) -> pd.DataFrame:
    """Backtest days with actuals, then the days ahead (no actual yet)."""
    return query(
        db,
        """select target_date, actual, forecast, 'backtest' as kind from forecast.backtest
           where metric = ? and scope = ? and model = ?
           union all
           select target_date, null, forecast, 'next' from forecast.next_days
           where metric = ? and scope = ? and model = ?
           order by target_date""",
        [metric, scope, model, metric, scope, model],
    )


def noise_floor(db: Path, scope: str) -> float | None:
    frame = query(db, "select mape from forecast.noise_floor where scope = ?", [scope])
    return float(frame["mape"].iloc[0]) if len(frame) else None


def detector_scores(db: Path) -> pd.DataFrame:
    return query(db, "select * from forecast.detector_scores order by recall desc")


def event_recall(db: Path) -> pd.DataFrame:
    return query(db, "select * from forecast.event_recall order by detector, event")


def real_anomalies(db: Path) -> pd.DataFrame:
    return query(db, "select * from forecast.anomalies order by order_date desc, scope, metric")


def has_graph(db: Path) -> bool:
    try:
        query(db, "select 1 from kg.signal limit 1")
    except duckdb.CatalogException:
        return False
    return True


def graph_warehouse(chosen: Path) -> Path | None:
    """The graph is built from one dataset, so fall back to whichever warehouse holds it."""
    others = sorted(p for p in chosen.parent.glob("*.duckdb") if p != chosen)
    return next((db for db in (chosen, *others) if has_graph(db)), None)


def graph_signal(db: Path) -> dict[str, float]:
    frame = query(db, "select measure, value from kg.signal")
    return {str(k): float(v) for k, v in zip(frame["measure"], frame["value"], strict=True)}


def graph_nodes(db: Path) -> pd.DataFrame:
    return query(db, "select * from kg.nodes order by nodes desc")


def graph_relationships(db: Path) -> pd.DataFrame:
    return query(db, "select * from kg.relationships order by relationships desc")


def graph_communities(db: Path) -> pd.DataFrame:
    return query(db, "select * from kg.communities order by restaurants desc, community")


def graph_pairs(db: Path) -> pd.DataFrame:
    return query(db, "select * from kg.co_ordered order by both_orders desc, q")


def graph_scores(db: Path) -> dict[str, float]:
    frame = query(db, "select measure, value from kg.scores")
    return {str(k): float(v) for k, v in zip(frame["measure"], frame["value"], strict=True)}


def graph_outlier_tests(db: Path) -> pd.DataFrame:
    return query(db, "select * from kg.outlier_tests")


def graph_outliers(db: Path) -> pd.DataFrame:
    return query(db, "select * from kg.cancel_outliers order by q")


def graph_recommendations(db: Path) -> pd.DataFrame:
    return query(db, "select * from kg.recommendations order by score desc")


def graph_top_restaurants(db: Path) -> pd.DataFrame:
    return query(db, "select * from kg.top_restaurants order by pagerank desc")


def has_review_labels(db: Path) -> bool:
    found = query(
        db,
        """select count(*) as n from information_schema.tables
           where table_schema = 'ai' and table_name = 'topic_sentiment'""",
    )
    return bool(found["n"].iloc[0])


def topic_sentiment(db: Path) -> pd.DataFrame:
    return query(db, "select topic, sentiment, reviews from ai.topic_sentiment order by topic")


def review_issues(db: Path, n: int = 12) -> pd.DataFrame:
    return query(
        db,
        """select key_issue, topic, sum(reviews) as reviews from ai.review_labels
           where key_issue is not null group by all order by reviews desc, key_issue limit ?""",
        [n],
    )


def latest_drift(log_path: Path) -> pd.DataFrame:
    """Most recent drift check per table, from the JSONL audit log."""
    if not log_path.exists():
        return pd.DataFrame(columns=["table", "status", "rows", "checked_at", "summary"])
    latest: dict[str, dict[str, Any]] = {}
    for line in log_path.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        latest[entry["table"]] = entry
    rows = [
        {
            "table": e["table"],
            "status": e["status"],
            "rows": e.get("rows"),
            "checked_at": e.get("checked_at"),
            "removed": ", ".join(e.get("removed") or []),
            "added": ", ".join(e.get("added") or []),
        }
        for e in latest.values()
    ]
    return pd.DataFrame(rows).sort_values("table").reset_index(drop=True)
