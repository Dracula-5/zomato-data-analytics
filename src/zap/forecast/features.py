"""Daily series, and the features the tree model learns from.

Trees can't predict outside the range they saw, and these series grow steadily. So the model
never sees raw volumes: every feature is relative to the recent level, and it predicts
log(target / level). The level then carries the growth.

Each row is an (origin, step) pair: "standing at `origin`, predict `step` days ahead". All
features are computed at the origin, so they can't see the day being predicted.
"""

from __future__ import annotations

from math import ceil
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

NATIONAL = "National"
METRICS = ("orders", "gmv")
LEVEL_WINDOW = 28
# Growth compares two level windows, the longest look-back in `direct_rows`.
MIN_HISTORY = 2 * LEVEL_WINDOW

FEATURES = [
    "same_weekday_1w",
    "same_weekday_2w",
    "same_weekday_4w",
    "week_vs_level",
    "spread",
    "growth",
    "step",
    "weekday",
    "month",
    "scope",
]

Panel = dict[str, dict[str, pd.Series]]  # {metric: {scope: daily series}}


def _complete(frame: pd.DataFrame, column: str, days: pd.DatetimeIndex) -> pd.Series:
    """One value per calendar day. A day with no row had no orders."""
    daily = frame.set_index(pd.to_datetime(frame["order_date"]))[column].astype(float)
    return daily.reindex(days, fill_value=0.0).rename(column)


def load_panel(db_path: Path, top_cities: int = 10) -> Panel:
    """National totals and the busiest cities, for every metric."""
    con = duckdb.connect(str(db_path), read_only=True, config={"python_enable_replacements": False})
    try:
        national = con.execute(
            "select order_date, orders, gmv from marts.mart_daily_kpis order by order_date"
        ).df()
        cities = con.execute(
            """select order_date, city, orders, gmv from marts.mart_city_daily
               where city in (select city from marts.mart_city_daily
                              group by city order by sum(orders) desc, city limit ?)""",
            [top_cities],
        ).df()
    finally:
        con.close()
    days = pd.date_range(national["order_date"].min(), national["order_date"].max(), freq="D")
    panel: Panel = {}
    for metric in METRICS:
        series = {NATIONAL: _complete(national, metric, days)}
        for city, frame in cities.groupby("city", sort=True):
            series[str(city)] = _complete(frame, metric, days)
        panel[metric] = series
    return panel


def future_index(history: pd.Series, horizon: int) -> pd.DatetimeIndex:
    return pd.date_range(history.index[-1] + pd.Timedelta(days=1), periods=horizon, freq="D")


def direct_rows(y: pd.Series, horizon: int) -> pd.DataFrame:
    """One row per (origin, step). `actual` is empty when the target day hasn't happened."""
    days = pd.DatetimeIndex(y.index)
    level = y.rolling(LEVEL_WINDOW).mean()
    shared = pd.DataFrame(
        {
            "level": level,
            "week_vs_level": y.rolling(7).mean() / level,
            "spread": y.rolling(LEVEL_WINDOW).std() / level,
            "growth": level / level.shift(LEVEL_WINDOW),
        }
    )
    frames = []
    for step in range(1, horizon + 1):
        frame = shared.copy()
        frame["origin"] = days
        frame["step"] = step
        frame["target_date"] = days.shift(step, freq="D")
        frame["actual"] = y.shift(-step)
        # The same weekday as the target, newest first. Every shift here is >= 0, so it reads
        # the origin or earlier: this is what keeps the future out of the features.
        weeks = ceil(step / 7)
        for lag in (1, 2, 4):
            frame[f"same_weekday_{lag}w"] = y.shift(7 * (weeks + lag - 1) - step) / level
        frames.append(frame)
    rows = pd.concat(frames, ignore_index=True)
    target = pd.DatetimeIndex(rows["target_date"])
    rows["weekday"] = target.dayofweek
    rows["month"] = target.month
    return rows.dropna(subset=["level", "growth", "same_weekday_4w"])


def to_log_ratio(actual: pd.Series, level: pd.Series) -> pd.Series:
    return pd.Series(np.log1p(actual) - np.log1p(level), index=actual.index)


def from_log_ratio(ratio: np.ndarray, level: pd.Series) -> np.ndarray:
    return np.expm1(ratio + np.log1p(level.to_numpy()))
