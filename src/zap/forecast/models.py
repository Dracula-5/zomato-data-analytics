"""Three forecasters, simplest first. Each sees only the history it is given.

Settings are fixed in advance rather than tuned on the backtest, so its scores stay honest.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from zap.forecast.features import (
    FEATURES,
    MIN_HISTORY,
    direct_rows,
    from_log_ratio,
    future_index,
    to_log_ratio,
)

SEASON = 7
LIGHTGBM_ROUNDS = 300
LIGHTGBM_PARAMS: dict[str, Any] = {
    "objective": "regression_l1",
    "learning_rate": 0.03,
    "num_leaves": 15,
    "min_data_in_leaf": 20,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "feature_fraction": 0.8,
    "seed": 42,
    "deterministic": True,
    "force_row_wise": True,
    "verbose": -1,
}

Forecasts = dict[str, dict[str, pd.Series]]  # {model: {scope: forecast}}


def seasonal_naive(history: pd.Series, horizon: int, season: int = SEASON) -> pd.Series:
    """Repeat the last full week: the baseline every other model has to beat."""
    return pd.Series(
        np.resize(history.iloc[-season:].to_numpy(), horizon),
        index=future_index(history, horizon),
    )


def ets(history: pd.Series, horizon: int) -> pd.Series:
    """Holt-Winters: damped trend, with a weekly pattern that scales with the level."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    fit = ExponentialSmoothing(
        history.clip(lower=1.0).to_numpy(dtype=float),
        trend="add",
        damped_trend=True,
        seasonal="mul",
        seasonal_periods=SEASON,
        initialization_method="estimated",
    ).fit()
    return pd.Series(fit.forecast(horizon), index=future_index(history, horizon))


def lightgbm_global(histories: dict[str, pd.Series], horizon: int) -> dict[str, pd.Series]:
    """One model across all series: small cities learn from the shape of the big ones."""
    import lightgbm as lgb

    shortest = min(len(y) for y in histories.values())
    if shortest < MIN_HISTORY + horizon:
        raise ValueError(f"need {MIN_HISTORY + horizon} days of history, got {shortest}")
    rows = pd.concat(
        [direct_rows(y, horizon).assign(scope=scope) for scope, y in histories.items()],
        ignore_index=True,
    )
    rows["scope"] = rows["scope"].astype("category")
    train = rows[rows["actual"].notna()]
    data = lgb.Dataset(train[FEATURES], label=to_log_ratio(train["actual"], train["level"]))
    model = lgb.train(LIGHTGBM_PARAMS, data, num_boost_round=LIGHTGBM_ROUNDS)
    forecasts = {}
    for scope, y in histories.items():
        latest = rows[(rows["scope"] == scope) & (rows["origin"] == y.index[-1])]
        latest = latest.sort_values("step")
        ratio = np.asarray(model.predict(latest[FEATURES]), dtype=float)
        values = from_log_ratio(ratio, latest["level"])
        forecasts[scope] = pd.Series(values, index=future_index(y, horizon))
    return forecasts


def forecast_all(histories: dict[str, pd.Series], horizon: int) -> Forecasts:
    return {
        "seasonal_naive": {s: seasonal_naive(y, horizon) for s, y in histories.items()},
        "ets": {s: ets(y, horizon) for s, y in histories.items()},
        "lightgbm": lightgbm_global(histories, horizon),
    }
