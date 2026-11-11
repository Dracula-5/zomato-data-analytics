"""`zap insights`: the business-insight queries in docs/insights run, and say what they found."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from helpers import REPO_ROOT
from zap.cli import app
from zap.config import Settings, get_settings
from zap.fixtures import FixtureResult
from zap.insights import findings
from zap.pipeline import PipelineRun
from zap.sqlutil import save_frames

Pipeline = tuple[Settings, PipelineRun, FixtureResult]
QUERIES = REPO_ROOT / "docs" / "insights"
NEEDS_FORECAST = {
    "05_forecast_accuracy",
    "06_forecast_next_14_days",
    "07_anomaly_alerts",
    "08_anomalies_on_real_days",
}


@pytest.fixture
def warehouse(pipeline: Pipeline, tmp_path: Path) -> Path:
    """A private copy of the fixture warehouse, so a test can add forecast tables to it."""
    folder = tmp_path / "warehouse"
    folder.mkdir()
    return Path(shutil.copy(pipeline[0].duckdb_path("fixture"), folder / "fixture.duckdb"))


def forecast_tables() -> dict[str, pd.DataFrame]:
    """What `make forecast` saves, with known answers: ETS is 10% off, the baseline 20%."""
    days = pd.date_range("2026-06-17", periods=14)
    backtest = pd.concat(
        pd.DataFrame(
            {
                "metric": metric,
                "scope": "National",
                "model": model,
                "fold": 1,
                "target_date": days,
                "actual": 100.0,
                "forecast": value,
            }
        )
        for metric in ("orders", "gmv")
        for model, value in (("ets", 90.0), ("seasonal_naive", 120.0))
    )
    ahead = pd.date_range("2026-07-01", periods=14)
    next_days = pd.concat(
        pd.DataFrame({"metric": m, "scope": "National", "model": "ets", "target_date": ahead})
        for m in ("orders", "gmv")
    ).assign(forecast=1.0)
    detectors = ("seasonal_robust_z", "plain_rolling_z")
    events = ("drop_20pct", "drop_30pct", "drop_50pct", "outage_40pct_3days")
    return {
        "backtest": backtest,
        "next_days": next_days,
        "detector_scores": pd.DataFrame(
            {
                "detector": detectors,
                "precision": [0.9, 0.9],
                "recall": [0.8, 0.1],
                "f1": [0.85, 0.2],
                "false_alarms_per_1000": [2.0, 0.5],
            }
        ),
        "event_recall": pd.DataFrame(
            [(d, e, 0.5) for d in detectors for e in events],
            columns=["detector", "event", "recall"],
        ),
        "anomalies": pd.DataFrame(
            {
                "metric": ["orders"],
                "scope": ["National"],
                "order_date": [pd.Timestamp("2026-06-01")],
                "actual": [1.0],
                "expected": [2.0],
                "z": [-4.0],
            }
        ),
    }


def test_every_query_runs_on_the_fixture_and_forecast_ones_wait_for_make_forecast(
    warehouse: Path,
) -> None:
    results = {result.name: result for result in findings.run(warehouse, QUERIES)}
    assert len(results) >= 3 and all(result.title for result in results.values())
    assert {name for name, result in results.items() if result.skipped} == NEEDS_FORECAST
    for name, result in results.items():
        if name not in NEEDS_FORECAST:
            assert result.table is not None and not result.table.empty, name
    week = results["01_demand_by_weekday"].table
    assert week is not None and len(week) == 7
    assert week["orders_above_quietest_day_pct"].min() == 0


def test_with_forecast_tables_every_query_runs_and_gives_the_known_answers(
    warehouse: Path,
) -> None:
    save_frames(warehouse, "forecast", forecast_tables())
    results = {result.name: result for result in findings.run(warehouse, QUERIES)}
    assert not [name for name, result in results.items() if result.skipped]
    accuracy = results["05_forecast_accuracy"].table
    assert accuracy is not None
    mape = accuracy.set_index(["metric", "model"])["mape_pct"]
    assert mape["orders", "ets"] == 10.0 and mape["orders", "seasonal_naive"] == 20.0
    real = results["08_anomalies_on_real_days"].table
    assert real is not None
    con = duckdb.connect(str(warehouse), read_only=True)
    days = con.execute("select count(*) from marts.mart_daily_kpis").fetchone()
    con.close()
    assert days is not None
    assert real.iloc[0]["series_days_checked"] == 2 * days[0]  # orders and GMV, National
    assert real.iloc[0]["real_days_flagged"] == 1


def test_the_command_writes_the_results_page(
    warehouse: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(warehouse.parent))
    get_settings.cache_clear()
    out = tmp_path / "RESULTS.md"
    args = ["insights", "--dataset", "fixture", "--queries", str(QUERIES), "--out", str(out)]
    result = CliRunner().invoke(app, args)
    missing = CliRunner().invoke(app, ["insights", "--dataset", "nope", "--out", str(out)])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    text = out.read_text(encoding="utf-8")
    assert "(SYNTHETIC fixture)" in text and "## 01_demand_by_weekday:" in text
    assert text.count("Not run: needs the forecast tables") == len(NEEDS_FORECAST)
    assert missing.exit_code == 1 and "no warehouse" in missing.output


def test_tables_use_each_columns_precision_and_turn_one_row_on_its_side() -> None:
    table = pd.DataFrame({"city": ["Pune", "Goa"], "rate": [6.0, 5.9], "orders": [12000.0, 7.0]})
    assert findings.markdown_table(table) == [
        "| city | rate | orders |",
        "|---|---:|---:|",
        "| Pune | 6.0 | 12,000 |",
        "| Goa | 5.9 | 7 |",
    ]
    one = pd.DataFrame({"late_pct": [19.4], "day": [pd.Timestamp("2026-07-01")], "ok": [True]})
    assert findings.markdown_table(one) == [
        "| Measure | Value |",
        "|---|---:|",
        "| late_pct | 19.4 |",
        "| day | 2026-07-01 |",
        "| ok | true |",
    ]
    assert findings.cell(np.nan) == "" and findings.cell(None) == ""
    assert findings.decimals([0.64, 0.7, 1.0]) == 2


def test_the_page_reports_skipped_and_empty_queries() -> None:
    results = [
        findings.Result("a", "Skipped one", "", skipped="needs X"),
        findings.Result("b", "Empty one", "", table=pd.DataFrame({"x": []})),
    ]
    text = findings.to_markdown(results, "full", datetime(2026, 11, 11, tzinfo=UTC))
    assert "Not run: needs X." in text and "No rows." in text
    assert "orders and reviews are synthetic" in text


def test_titles_errors_and_missing_queries(warehouse: Path, tmp_path: Path) -> None:
    assert findings.title_of("select 1", "fallback") == "fallback"
    assert findings.title_of("-- Late deliveries\nselect 1", "x") == "Late deliveries"
    with pytest.raises(FileNotFoundError):
        findings.run(warehouse, tmp_path)
    (tmp_path / "bad.sql").write_text("select * from marts.no_such_table", encoding="utf-8")
    with pytest.raises(duckdb.CatalogException):  # only a missing forecast table is skipped
        findings.run(warehouse, tmp_path)
