"""`zap forecast`: backtest, the next 14 days, anomalies, then save everything.

Results go to the `forecast` schema of the dataset's DuckDB file, for the dashboard, and to
`artifacts/forecast_backtest.json`, for METRICS.md.
"""

from __future__ import annotations

import json
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from zap.forecast import anomaly
from zap.forecast.backtest import (
    FOLDS,
    HORIZON,
    accuracy_table,
    noise_floors,
    run_backtest,
    summarize,
)
from zap.forecast.features import Panel, load_panel
from zap.forecast.models import forecast_all


def next_days(panel: Panel, horizon: int = HORIZON) -> pd.DataFrame:
    rows = []
    for metric, series in panel.items():
        for model, by_scope in forecast_all(series, horizon).items():
            for scope, forecast in by_scope.items():
                rows.append(
                    pd.DataFrame(
                        {
                            "metric": metric,
                            "scope": scope,
                            "model": model,
                            "target_date": forecast.index,
                            "forecast": forecast.to_numpy(),
                        }
                    )
                )
    return pd.concat(rows, ignore_index=True)


def anomaly_report(panel: Panel, real_flags: pd.DataFrame) -> dict[str, Any]:
    return {
        "labels": "SYNTHETIC: events injected into a copy of each series, never into the warehouse",
        "seeds": anomaly.SEEDS,
        "series": sum(len(series) for series in panel.values()),
        "detectors": {
            "seasonal_robust_z": anomaly.evaluate(
                panel, anomaly.seasonal_z, anomaly.MODIFIED_Z, anomaly.SEEDS
            ),
            "plain_rolling_z": anomaly.evaluate(
                panel, anomaly.plain_z, anomaly.PLAIN_Z, anomaly.SEEDS
            ),
        },
        "real_days_flagged": len(real_flags),
        "real_series_days": sum(len(y) for series in panel.values() for y in series.values()),
    }


def detector_tables(detectors: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """The detector scores as tables, so the dashboard needs no JSON file."""
    scores = [
        {
            "detector": name,
            "precision": result["precision"],
            "recall": result["recall"],
            "f1": result["f1"],
            "false_alarms_per_1000": result["false_alarms_per_1000_normal_days"],
        }
        for name, result in detectors.items()
    ]
    recalls = [
        {"detector": name, "event": event, "recall": recall}
        for name, result in detectors.items()
        for event, recall in result["recall_by_event"].items()
    ]
    return {"detector_scores": pd.DataFrame(scores), "event_recall": pd.DataFrame(recalls)}


def _save_tables(db_path: Path, tables: dict[str, pd.DataFrame]) -> None:
    con = duckdb.connect(str(db_path), config={"python_enable_replacements": False})
    try:
        con.execute("CREATE SCHEMA IF NOT EXISTS forecast")
        for name, frame in tables.items():
            con.register("frame_view", frame)
            con.execute(f"CREATE OR REPLACE TABLE forecast.{name} AS SELECT * FROM frame_view")  # noqa: S608 - fixed names
            con.unregister("frame_view")
    finally:
        con.close()


def run_all(db_path: Path, artifacts_dir: Path, top_cities: int = 10) -> dict[str, Any]:
    panel = load_panel(db_path, top_cities)
    from statsmodels.tools.sm_exceptions import ConvergenceWarning

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        predictions = run_backtest(panel)
        upcoming = next_days(panel)
    report: dict[str, Any] = {
        "dataset": db_path.stem,
        "folds": FOLDS,
        "horizon_days": HORIZON,
        "series": sum(len(series) for series in panel.values()),
        "scopes": sorted(next(iter(panel.values()))),
        "first_test_day": str(predictions["target_date"].min().date()),
        "last_test_day": str(predictions["target_date"].max().date()),
        "metrics": summarize(predictions),
        "poisson_floor_mape_orders": noise_floors(predictions),
        "ets_convergence_warnings": sum(issubclass(w.category, ConvergenceWarning) for w in caught),
    }
    real_flags = anomaly.flag_real_days(panel)
    report["anomalies"] = anomaly_report(panel, real_flags)
    _save_tables(
        db_path,
        {
            "backtest": predictions,
            "accuracy": accuracy_table(predictions),
            "next_days": upcoming,
            "anomalies": real_flags,
            "noise_floor": pd.DataFrame(
                report["poisson_floor_mape_orders"].items(), columns=["scope", "mape"]
            ),
            **detector_tables(report["anomalies"]["detectors"]),
        },
    )
    path = artifacts_dir / "forecast_backtest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report["ran_at"] = datetime.now(UTC).isoformat()
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    return report
