"""`zap bi-export`: the dbt marts and the ETS forecast as CSV files for Power BI.

Power BI Desktop reads CSV files without a driver, so the export is plain files: a star schema
(`dim_date` and `dim_city` around two daily facts) plus a forecast-vs-actual table. Nothing here
models anything new. The marts come straight from dbt. The forecast is the ETS model from
`zap forecast`: read from the warehouse's `forecast` schema when that run matches the marts, and
otherwise fitted here with the same function on whatever history the warehouse holds (the
SYNTHETIC fixture has only 60 days, so it gets fewer backtest folds).

The band around the forecast is empirical: the 10th and 90th percentiles of actual / forecast
from earlier folds only, so every band drawn over the backtest is out of sample.
"""

from __future__ import annotations

import json
import warnings
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from zap.forecast.backtest import FOLDS, HORIZON, errors, fold_origins
from zap.forecast.features import METRICS, NATIONAL, Panel, load_panel
from zap.forecast.models import SEASON, ets
from zap.sqlutil import fetch_one, quote_literal

MODEL = "ets"
COVERAGE = 0.8
LOW_Q, HIGH_Q = (1 - COVERAGE) / 2, (1 + COVERAGE) / 2
MIN_TRAIN_DAYS = 4 * SEASON  # the shortest history a fallback fold fits ETS on
AUTO_DATASETS = ("full", "fixture")  # `make bi-export` uses the first one that is built

MART_QUERIES = {
    "mart_city_daily": """
        select order_date, city, orders, delivered_orders, cancelled_orders, gmv, aov,
               cancel_rate, discount_rate, active_customers
        from marts.mart_city_daily order by order_date, city""",
    "mart_daily_kpis": """
        select order_date, iso_day_of_week, is_weekend, orders, delivered_orders, gmv, aov,
               cancel_rate, sla_breach_rate, active_customers
        from marts.mart_daily_kpis order by order_date""",
    "dim_city": """
        select city, localities, restaurants, avg_restaurant_rating, avg_cost_for_two
        from marts.dim_city order by city""",
}
DIM_DATE_QUERY = """
    select date_day as date, year, month, strftime(date_day, '%b') as month_name,
           strftime(date_day, '%b %Y') as month_label, strftime(date_day, '%Y-%m') as year_month,
           month_start, week_start, day_of_month, iso_day_of_week,
           strftime(date_day, '%a') as day_name, is_weekend
    from marts.dim_date where date_day between ? and ? order by date_day"""
FORECAST_QUERY = """
    select cast(date as date) as date, * exclude (date) from bi_forecast order by scope, date"""
FORECAST_COLUMNS = [
    "date",
    "scope",
    "period",
    "fold",
    "actual_gmv",
    "forecast_gmv",
    "forecast_gmv_lower",
    "forecast_gmv_upper",
    "actual_orders",
    "forecast_orders",
    "forecast_orders_lower",
    "forecast_orders_upper",
]
# Columns a Power BI relationship or slicer depends on: never empty.
KEY_COLUMNS = {
    "mart_city_daily": ["order_date", "city"],
    "mart_daily_kpis": ["order_date"],
    "dim_city": ["city"],
    "dim_date": ["date", "year_month", "month_start"],
    "forecast_vs_actual": ["date", "scope", "period"],
}


class BiExportError(RuntimeError):
    """The warehouse can't produce a complete export."""


def pick_dataset(warehouse_path: Callable[[str], Path], dataset: str | None) -> str:
    """The dataset asked for, or the first built one of `AUTO_DATASETS`.

    `warehouse_path` maps a dataset label to its DuckDB file (`Settings.duckdb_path`).
    """
    if dataset:
        return dataset
    for label in AUTO_DATASETS:
        if warehouse_path(label).exists():
            return label
    raise BiExportError(
        "no warehouse to export: run `make demo` (SYNTHETIC fixture) or `make local-full`"
    )


def stored_forecast(con: duckdb.DuckDBPyConnection) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    """The ETS rows `zap forecast` saved, or None if it hasn't run on this warehouse."""
    tables = {
        row[0]
        for row in con.execute(
            "select table_name from information_schema.tables where table_schema = 'forecast'"
        ).fetchall()
    }
    if not {"backtest", "next_days"} <= tables:
        return None
    backtest = con.execute(
        """select metric, scope, fold, cast(target_date as date) as date, actual, forecast
           from forecast.backtest where model = ? order by metric, scope, date""",
        [MODEL],
    ).df()
    future = con.execute(
        """select metric, scope, cast(target_date as date) as date, forecast
           from forecast.next_days where model = ? order by metric, scope, date""",
        [MODEL],
    ).df()
    if backtest.empty or future.empty:
        return None
    return _dated(backtest), _dated(future)


def run_ets(panel: Panel) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Walk-forward ETS with the same model and fold layout as `zap forecast`.

    Only the number of folds adapts, to the history available.
    """
    days = len(next(iter(panel[METRICS[0]].values())))
    folds = min(FOLDS, (days - MIN_TRAIN_DAYS) // HORIZON)
    if folds < 1:
        raise BiExportError(
            f"{days} days of history; the forecast needs at least {MIN_TRAIN_DAYS + HORIZON}"
        )
    past, ahead = [], []
    for metric, series in panel.items():
        for scope, y in series.items():
            for fold, origin in enumerate(fold_origins(y.index[-1], folds, HORIZON), start=1):
                forecast = ets(y.loc[:origin], HORIZON)
                past.append(
                    pd.DataFrame(
                        {
                            "metric": metric,
                            "scope": scope,
                            "fold": fold,
                            "date": forecast.index,
                            "actual": y.reindex(forecast.index).to_numpy(),
                            "forecast": forecast.to_numpy(),
                        }
                    )
                )
            upcoming = ets(y, HORIZON)
            ahead.append(
                pd.DataFrame(
                    {
                        "metric": metric,
                        "scope": scope,
                        "date": upcoming.index,
                        "forecast": upcoming.to_numpy(),
                    }
                )
            )
    return (
        _dated(pd.concat(past, ignore_index=True)),
        _dated(pd.concat(ahead, ignore_index=True)),
        folds,
    )


def add_band(backtest: pd.DataFrame, future: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Lower and upper bounds from the actual / forecast ratios of EARLIER folds.

    Fold 1 has no earlier fold, so it gets no band. The future uses every fold.
    """
    backtest = backtest.assign(lower=np.nan, upper=np.nan)
    future = future.assign(lower=np.nan, upper=np.nan)
    ratio = (backtest["actual"] / backtest["forecast"]).where(backtest["forecast"] > 0)
    for (metric, scope), group in backtest.groupby(["metric", "scope"], sort=False):
        group_ratio = ratio.loc[group.index]
        for fold in sorted(group["fold"].unique()):
            _scale(backtest, group.index[group["fold"] == fold], group_ratio[group["fold"] < fold])
        ahead = future.index[(future["metric"] == metric) & (future["scope"] == scope)]
        _scale(future, ahead, group_ratio)
    return backtest, future


def _scale(frame: pd.DataFrame, rows: pd.Index, ratios: pd.Series) -> None:
    known = ratios.dropna()
    if len(known) < HORIZON:  # less than one fold of errors says too little
        return
    low, high = (float(q) for q in known.quantile([LOW_Q, HIGH_Q]))
    frame.loc[rows, "lower"] = frame.loc[rows, "forecast"] * low
    frame.loc[rows, "upper"] = frame.loc[rows, "forecast"] * high


def forecast_table(backtest: pd.DataFrame, future: pd.DataFrame) -> pd.DataFrame:
    """One row per day and scope; GMV and orders side by side, as Power BI likes it."""
    long = pd.concat(
        [backtest.assign(period="backtest"), future.assign(period="future", actual=np.nan)],
        ignore_index=True,
    )
    first, *others = (
        long[long["metric"] == metric]
        .drop(columns="metric")
        .rename(
            columns={
                "actual": f"actual_{metric}",
                "forecast": f"forecast_{metric}",
                "lower": f"forecast_{metric}_lower",
                "upper": f"forecast_{metric}_upper",
            }
        )
        for metric in METRICS
    )
    wide = first
    for part in others:  # the fold depends on the day alone, so one copy of it is enough
        wide = wide.merge(part.drop(columns="fold"), on=["date", "scope", "period"], how="outer")
    wide["fold"] = wide["fold"].astype("Int64")
    numbers = [c for c in FORECAST_COLUMNS if c.startswith(("actual_", "forecast_"))]
    wide[numbers] = wide[numbers].round(2)
    return wide[FORECAST_COLUMNS].sort_values(["scope", "date"], ignore_index=True)


def band_coverage(backtest: pd.DataFrame) -> dict[str, float | None]:
    """Share of backtest days, nationally, whose actual fell inside its band."""
    shares: dict[str, float | None] = {}
    for metric in METRICS:
        rows = backtest[(backtest["metric"] == metric) & (backtest["scope"] == NATIONAL)]
        rows = rows.dropna(subset=["lower", "upper"])
        inside = (rows["actual"] >= rows["lower"]) & (rows["actual"] <= rows["upper"])
        shares[metric] = round(float(inside.mean()) * 100, 1) if len(rows) else None
    return shares


def forecast_note(table: pd.DataFrame, mape: dict[str, float], coverage: float | None) -> str:
    """Plain English for the report's text box, from the exported numbers only."""
    national = table[table["scope"] == NATIONAL]
    ahead = national[national["period"] == "future"]
    recent = national[national["period"] == "backtest"].tail(len(ahead))
    expected, before = float(ahead["forecast_gmv"].sum()), float(recent["actual_gmv"].sum())
    change = (expected / before - 1) * 100 if before else 0.0
    first, last = ahead["date"].min(), ahead["date"].max()
    folds = int(national["fold"].max())
    lines = [
        "What the forecast says (national GMV, ETS model)",
        f"Next {len(ahead)} days ({_day(first)} to {_day(last)}): about {rupees(expected)} "
        f"expected, {abs(change):.1f}% {'more' if change >= 0 else 'less'} than the "
        f"previous {len(recent)} days.",
        f"How far to trust it: tested on {folds} past {HORIZON}-day periods, its daily GMV "
        f"forecast was off by {mape['gmv']:.2f}% on average.",
    ]
    if coverage is not None:
        lines.append(
            f"The shaded band is where {COVERAGE:.0%} of earlier errors fell; in the test "
            f"periods, {coverage:.1f}% of actual days landed inside it."
        )
    lines.append("Orders are synthetic, so this describes the dataset, not a real business.")
    return "\n".join(lines) + "\n"


def export(db_path: Path, out: Path) -> dict[str, Any]:
    """Write the CSV files and a manifest to `out`, and return the manifest."""
    if not db_path.exists():
        raise BiExportError(f"no warehouse at {db_path}")
    out.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path), read_only=True, config={"python_enable_replacements": False})
    try:
        last_day = fetch_one(con, "select max(order_date) from marts.mart_daily_kpis")[0]
        backtest, future, folds, source = _forecast(con, db_path, last_day)
        backtest, future = add_band(backtest, future)
        table = forecast_table(backtest, future)
        first_day = fetch_one(con, "select min(order_date) from marts.mart_daily_kpis")[0]
        start = first_day.replace(day=1)
        end = (pd.Timestamp(table["date"].max()) + pd.offsets.MonthEnd(0)).date()
        queries: dict[str, tuple[str, list[Any]]] = {
            name: (sql, []) for name, sql in MART_QUERIES.items()
        }
        queries["dim_date"] = (DIM_DATE_QUERY, [start, end])
        tables = {name: con.execute(sql, params).df() for name, (sql, params) in queries.items()}
        tables["forecast_vs_actual"] = table
        check_tables(tables, start, end)
        for name, (sql, params) in queries.items():  # straight from SQL: dates stay dates
            _copy(con, sql, params, out / f"{name}.csv")
        con.register("bi_forecast", table)
        try:
            _copy(con, FORECAST_QUERY, [], out / "forecast_vs_actual.csv")
        finally:
            con.unregister("bi_forecast")
    finally:
        con.close()
    national = backtest[backtest["scope"] == NATIONAL]
    mape = {
        str(metric): errors(rows["actual"], rows["forecast"])["mape"]
        for metric, rows in national.groupby("metric")
    }
    coverage = band_coverage(backtest)
    (out / "forecast_note.txt").write_text(
        forecast_note(table, mape, coverage["gmv"]), encoding="utf-8", newline="\n"
    )
    city = tables["mart_city_daily"]
    manifest: dict[str, Any] = {
        "dataset": db_path.stem,
        "data": "SYNTHETIC fixture"
        if db_path.stem == "fixture"
        else "Orders and reviews are synthetic; restaurant and menu data look scraped",
        "ran_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "tables": {
            name: {"rows": len(frame), "columns": list(frame.columns)}
            for name, frame in tables.items()
        },
        "totals_for_checking_the_report": {
            "gmv": int(city["gmv"].sum()),
            "orders": int(city["orders"].sum()),
            "delivered_orders": int(city["delivered_orders"].sum()),
            "cancelled_orders": int(city["cancelled_orders"].sum()),
        },
        "forecast": {
            "model": MODEL,
            "source": source,
            "folds": folds,
            "horizon_days": HORIZON,
            "backtest_days": [str(table["date"].min().date()), str(last_day)],
            "future_days": [str(future["date"].min().date()), str(future["date"].max().date())],
            "national_mape_pct": mape,
            "band": f"{COVERAGE:.0%} band: percentiles of actual/forecast from earlier folds only",
            "band_coverage_pct": coverage,
        },
    }
    (out / "export_manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return manifest


def _forecast(
    con: duckdb.DuckDBPyConnection, db_path: Path, last_day: date
) -> tuple[pd.DataFrame, pd.DataFrame, int, str]:
    stored = stored_forecast(con)
    if stored is not None and stored[0]["date"].max().date() == last_day:
        backtest, future = stored
        return backtest, future, int(backtest["fold"].max()), "forecast schema from `zap forecast`"
    from statsmodels.tools.sm_exceptions import ConvergenceWarning

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        backtest, future, folds = run_ets(load_panel(db_path))
    reason = "no `zap forecast` run" if stored is None else "`zap forecast` run is older"
    return backtest, future, folds, f"fitted by bi-export with the same ETS function ({reason})"


def check_tables(tables: dict[str, pd.DataFrame], start: date, end: date) -> None:
    """Fail loudly on anything that would break a Power BI relationship."""
    for name, columns in KEY_COLUMNS.items():
        empty = [c for c in columns if tables[name][c].isna().any()]
        if empty:
            raise BiExportError(f"{name} has empty keys: {', '.join(empty)}")
    dates = pd.to_datetime(tables["dim_date"]["date"])
    if len(dates) != (end - start).days + 1 or dates.duplicated().any():
        raise BiExportError(f"dim_date doesn't cover every day from {start} to {end} once")
    cities = tables["dim_city"]["city"].str.strip().str.casefold()
    if cities.duplicated().any():  # Power BI matches text ignoring case and trailing spaces
        raise BiExportError("dim_city has cities that differ only by case or spaces")
    unknown = set(tables["mart_city_daily"]["city"]) - set(tables["dim_city"]["city"])
    if unknown:
        raise BiExportError(f"{len(unknown)} cities in mart_city_daily are missing from dim_city")


def _copy(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any], path: Path) -> None:
    """CSV with a header, ISO dates, `.` decimals and empty cells for NULL."""
    con.execute(f"COPY ({sql}) TO {quote_literal(path)} (HEADER, DELIMITER ',')", params)


def _dated(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.assign(date=pd.to_datetime(frame["date"]).dt.normalize())


def _day(value: Any) -> str:
    stamp = pd.Timestamp(value)
    return f"{stamp.day} {stamp:%b %Y}"


def rupees(value: float) -> str:
    """Indian units: crore and lakh read better than millions to the people this is for."""
    if value >= 1e7:
        return f"₹{value / 1e7:,.2f} crore"
    if value >= 1e5:
        return f"₹{value / 1e5:,.2f} lakh"
    return f"₹{value:,.0f}"
