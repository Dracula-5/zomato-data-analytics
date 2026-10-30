"""Walk-forward backtest: stand at a past date, forecast, compare with what happened.

Each fold cuts the history at its origin *before* any model runs, so a model can't see the
days it is scored on. Folds don't overlap, and the last one ends on the last day of data.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from zap.forecast.features import NATIONAL, Panel
from zap.forecast.models import forecast_all

FOLDS = 8
HORIZON = 14
BASELINE = "seasonal_naive"


def fold_origins(
    last: pd.Timestamp, folds: int = FOLDS, horizon: int = HORIZON
) -> list[pd.Timestamp]:
    return [last - pd.Timedelta(days=horizon * (folds - i)) for i in range(folds)]


def run_backtest(panel: Panel, folds: int = FOLDS, horizon: int = HORIZON) -> pd.DataFrame:
    """One row per (metric, scope, model, fold, day), with the actual and the forecast."""
    rows = []
    for metric, series in panel.items():
        last = next(iter(series.values())).index[-1]
        for fold, origin in enumerate(fold_origins(last, folds, horizon), start=1):
            histories = {scope: y.loc[:origin] for scope, y in series.items()}
            for model, by_scope in forecast_all(histories, horizon).items():
                for scope, forecast in by_scope.items():
                    rows.append(
                        pd.DataFrame(
                            {
                                "metric": metric,
                                "scope": scope,
                                "model": model,
                                "fold": fold,
                                "target_date": forecast.index,
                                "actual": series[scope].reindex(forecast.index).to_numpy(),
                                "forecast": forecast.to_numpy(),
                            }
                        )
                    )
    return pd.concat(rows, ignore_index=True)


def errors(actual: pd.Series, forecast: pd.Series) -> dict[str, float]:
    """Percentages. MAPE skips days with no actual, where a percentage has no meaning."""
    known = actual > 0
    missed = forecast[known] - actual[known]
    size = actual.abs() + forecast.abs()
    used = size > 0
    return {
        "mape": round(float((missed.abs() / actual[known]).mean() * 100), 3),
        "smape": round(float((2 * (forecast - actual).abs()[used] / size[used]).mean() * 100), 3),
        "bias_pct": round(float((missed / actual[known]).mean() * 100), 3),
    }


def poisson_floor_mape(actual: pd.Series) -> float:
    """The MAPE of a perfect forecast if daily counts are Poisson: E|x - m| / m is about
    sqrt(2 / (pi * m)). A model near this has nothing left to learn from the data."""
    return round(float(np.mean(np.sqrt(2 / (math.pi * actual[actual > 0]))) * 100), 3)


def accuracy_table(predictions: pd.DataFrame) -> pd.DataFrame:
    """Scores for each metric, scope and model over all folds."""
    keys = ["metric", "scope", "model"]
    records = [
        {**dict(zip(keys, key, strict=True)), **errors(group["actual"], group["forecast"])}
        for key, group in predictions.groupby(keys, sort=True)
    ]
    return pd.DataFrame(records)


def _fold_wins(predictions: pd.DataFrame, metric: str, model: str) -> dict[str, int]:
    """In how many (scope, fold) pairs did the model beat the baseline's MAPE?"""
    wins = {"national": 0, "national_of": 0, "cities": 0, "cities_of": 0}
    subset = predictions[predictions["metric"] == metric]
    for (scope, _), group in subset.groupby(["scope", "fold"]):
        scores = {
            name: errors(rows["actual"], rows["forecast"])["mape"]
            for name, rows in group.groupby("model")
        }
        place = "national" if scope == NATIONAL else "cities"
        wins[f"{place}_of"] += 1
        wins[place] += int(scores[model] < scores[BASELINE])
    return wins


def summarize(predictions: pd.DataFrame) -> dict[str, Any]:
    """Per metric: national score, mean city score, gain over the baseline, and the winner."""
    table = accuracy_table(predictions)
    summary: dict[str, Any] = {}
    for metric, by_metric in table.groupby("metric"):
        cities = by_metric[by_metric["scope"] != NATIONAL]
        base = by_metric[by_metric["model"] == BASELINE]["mape"].mean()
        models: dict[str, Any] = {}
        for model, rows in by_metric.groupby("model"):
            national = rows[rows["scope"] == NATIONAL].iloc[0]
            in_cities = cities[cities["model"] == model]
            mean_mape = float(rows["mape"].mean())
            models[str(model)] = {
                "national_mape": float(national["mape"]),
                "national_smape": float(national["smape"]),
                "national_bias_pct": float(national["bias_pct"]),
                "cities_mean_mape": round(float(in_cities["mape"].mean()), 3),
                "cities_mean_smape": round(float(in_cities["smape"].mean()), 3),
                "all_series_mean_mape": round(mean_mape, 3),
                "mape_gain_vs_baseline_pct": round(100 * (1 - mean_mape / base), 1),
                "beats_baseline": _fold_wins(predictions, str(metric), str(model)),
            }
        summary[str(metric)] = {
            "models": models,
            "best_model": min(models, key=lambda m: models[m]["all_series_mean_mape"]),
        }
    return summary


def noise_floors(predictions: pd.DataFrame) -> dict[str, float]:
    """Order counts only: GMV also carries basket-size noise, so the formula doesn't apply."""
    days = predictions[(predictions["metric"] == "orders") & (predictions["model"] == BASELINE)]
    return {str(s): poisson_floor_mape(g["actual"]) for s, g in days.groupby("scope")}
