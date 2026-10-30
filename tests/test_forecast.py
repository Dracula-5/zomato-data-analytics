"""Forecasters, backtest and anomaly detection on SYNTHETIC series with known answers."""

import json
from itertools import pairwise
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest
from typer.testing import CliRunner

from helpers import REPO_ROOT
from zap.cli import app
from zap.config import get_settings
from zap.forecast import anomaly, backtest, features, models
from zap.forecast.features import MIN_HISTORY, NATIONAL, direct_rows, future_index, load_panel
from zap.forecast.run import run_all

WEEKDAY_SHAPE = np.array([0.83, 0.83, 0.83, 0.87, 1.12, 1.33, 1.2])


def trending_series(
    days: int = 400, noise: float = 0.0, seed: int = 0, size: float = 5000
) -> pd.Series:
    """Steady growth times a weekly shape: the same structure as the real data."""
    index = pd.date_range("2024-01-01", periods=days, freq="D")
    rng = np.random.default_rng(seed)
    level = size * (1 + np.arange(days) * 0.003)
    y = level * WEEKDAY_SHAPE[index.dayofweek] * (1 + noise * rng.standard_normal(days))
    return pd.Series(y, index=index)


def small_panel(days: int = 300) -> features.Panel:
    series = {
        NATIONAL: trending_series(days, 0.01, 1, 9000),
        "Pune": trending_series(days, 0.03, 2, 900),
        "Goa": trending_series(days, 0.05, 3, 300),
    }
    return {"orders": series, "gmv": {k: v * 480 for k, v in series.items()}}


def test_panel_has_every_day_and_the_busiest_cities(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA marts")
    con.execute(
        "CREATE TABLE marts.mart_daily_kpis AS SELECT * FROM (VALUES "
        "(DATE '2026-01-01', 30, 3000), (DATE '2026-01-02', 40, 4000), "
        "(DATE '2026-01-03', 50, 5000)) t(order_date, orders, gmv)"
    )
    con.execute(
        "CREATE TABLE marts.mart_city_daily AS SELECT * FROM (VALUES "
        "(DATE '2026-01-01', 'Big', 20, 2000), (DATE '2026-01-03', 'Big', 30, 3000), "
        "(DATE '2026-01-01', 'Tiny', 1, 10)) t(order_date, city, orders, gmv)"
    )
    con.close()
    panel = load_panel(db, top_cities=1)
    assert list(panel) == ["orders", "gmv"]
    assert sorted(panel["orders"]) == ["Big", NATIONAL]
    assert panel["orders"]["Big"].tolist() == [20.0, 0.0, 30.0]  # a missing day had no orders
    assert panel["gmv"][NATIONAL].iloc[-1] == 5000.0


def test_every_row_needs_only_the_past() -> None:
    y = trending_series(200)
    rows = direct_rows(y, 14)
    changed = y.copy()
    origin = y.index[120]
    changed.iloc[121:] *= 7
    same = direct_rows(changed, 14)
    columns = [c for c in rows.columns if c != "actual"]
    left = rows[rows["origin"] == origin][columns].reset_index(drop=True)
    right = same[same["origin"] == origin][columns].reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right)


def test_rows_cover_each_step_and_mark_unknown_targets() -> None:
    y = trending_series(100)
    rows = direct_rows(y, 14)
    assert sorted(rows["step"].unique()) == list(range(1, 15))
    assert rows["origin"].min() == y.index[MIN_HISTORY - 1]
    assert (rows["target_date"] - rows["origin"]).dt.days.equals(rows["step"])
    last = rows[rows["origin"] == y.index[-1]]
    assert len(last) == 14 and last["actual"].isna().all()


@pytest.mark.parametrize("step", [1, 6, 7, 8, 14])
def test_same_weekday_feature_is_truly_the_same_weekday(step: int) -> None:
    # A series whose value IS its weekday makes the lag's weekday readable from the feature.
    index = pd.date_range("2024-01-01", periods=120, freq="D")
    y = pd.Series((index.dayofweek + 1).astype(float), index=index)
    rows = direct_rows(y, 14)
    row = rows[rows["step"] == step].iloc[-1]
    assert round(row["same_weekday_1w"] * row["level"]) == row["weekday"] + 1


def test_seasonal_naive_repeats_the_last_week() -> None:
    y = pd.Series(range(1, 22), index=pd.date_range("2024-01-01", periods=21), dtype=float)
    forecast = models.seasonal_naive(y, 10)
    assert forecast.tolist() == [15, 16, 17, 18, 19, 20, 21, 15, 16, 17]
    assert forecast.index[0] == pd.Timestamp("2024-01-22")


def test_ets_follows_growth_that_the_naive_model_misses() -> None:
    y = trending_series(300)
    truth = trending_series(314).iloc[-14:]
    naive = backtest.errors(truth, models.seasonal_naive(y, 14))
    smoothed = backtest.errors(truth, models.ets(y, 14))
    assert smoothed["mape"] < naive["mape"] / 3
    assert naive["bias_pct"] < -1  # repeating last week falls behind a growing series


def test_lightgbm_extrapolates_beyond_anything_in_its_training_data() -> None:
    panel = small_panel(300)["orders"]
    truth = trending_series(314, size=9000).iloc[-14:]
    forecast = models.lightgbm_global(panel, 14)[NATIONAL]
    assert forecast.max() > panel[NATIONAL].max()  # a raw-value tree could never exceed this
    assert backtest.errors(truth, forecast)["mape"] < 3


def test_lightgbm_is_repeatable() -> None:
    panel = small_panel(200)["orders"]
    first = models.lightgbm_global(panel, 14)[NATIONAL]
    pd.testing.assert_series_equal(first, models.lightgbm_global(panel, 14)[NATIONAL])


def test_lightgbm_refuses_too_little_history() -> None:
    with pytest.raises(ValueError, match="days of history"):
        models.lightgbm_global({"x": trending_series(MIN_HISTORY)}, 14)


def test_changing_the_future_changes_no_forecast() -> None:
    """The core guarantee: a fold's history is cut before any model runs."""
    panel = small_panel(260)["orders"]
    origin = panel[NATIONAL].index[230]
    honest = {s: y.loc[:origin] for s, y in panel.items()}
    tampered = {s: y.copy() for s, y in panel.items()}
    for y in tampered.values():
        y.loc[y.index > origin] *= 50
    cut = {s: y.loc[:origin] for s, y in tampered.items()}
    left, right = models.forecast_all(honest, 14), models.forecast_all(cut, 14)
    for model, by_scope in left.items():
        for scope, forecast in by_scope.items():
            pd.testing.assert_series_equal(forecast, right[model][scope])


def test_folds_never_overlap_and_the_last_ends_with_the_data() -> None:
    last = pd.Timestamp("2026-06-30")
    origins = backtest.fold_origins(last, 8, 14)
    assert len(origins) == 8
    assert origins[0] == pd.Timestamp("2026-03-10")
    assert origins[-1] + pd.Timedelta(days=14) == last
    assert all(b - a == pd.Timedelta(days=14) for a, b in pairwise(origins))


def test_errors_on_known_numbers() -> None:
    actual = pd.Series([100.0, 200.0])
    forecast = pd.Series([110.0, 180.0])
    assert backtest.errors(actual, forecast) == {
        "mape": 10.0,
        "smape": pytest.approx(10.025, abs=0.001),  # (20 / 210 + 40 / 380) / 2
        "bias_pct": 0.0,
    }


def test_a_day_with_no_actual_is_left_out_of_mape() -> None:
    scores = backtest.errors(pd.Series([0.0, 100.0]), pd.Series([5.0, 110.0]))
    assert scores["mape"] == 10.0
    assert np.isfinite(scores["smape"])


def test_poisson_floor_matches_a_simulation() -> None:
    rng = np.random.default_rng(1)
    mean = 900
    draws = rng.poisson(mean, 200_000)
    simulated = np.mean(np.abs(draws - mean) / mean) * 100
    floor = backtest.poisson_floor_mape(pd.Series([float(mean)] * 3))
    assert floor == pytest.approx(simulated, abs=0.05)


@pytest.fixture(scope="module")
def predictions() -> pd.DataFrame:
    return backtest.run_backtest(small_panel(300), folds=2, horizon=7)


def test_backtest_scores_each_model_on_days_it_never_saw(predictions: pd.DataFrame) -> None:
    assert set(predictions["model"]) == {"seasonal_naive", "ets", "lightgbm"}
    assert len(predictions) == 2 * 3 * 3 * 2 * 7  # metrics, scopes, models, folds, days
    assert predictions["actual"].notna().all() and predictions["forecast"].notna().all()
    assert predictions["target_date"].nunique() == 14


def test_summary_names_a_winner_and_counts_wins(predictions: pd.DataFrame) -> None:
    summary = backtest.summarize(predictions)
    orders = summary["orders"]
    assert orders["best_model"] in orders["models"]
    wins = orders["models"]["ets"]["beats_baseline"]
    assert wins["national_of"] == 2 and wins["cities_of"] == 4
    assert orders["models"]["seasonal_naive"]["mape_gain_vs_baseline_pct"] == 0.0
    assert orders["models"]["seasonal_naive"]["beats_baseline"]["cities"] == 0


def test_noise_floor_is_for_order_counts_only(predictions: pd.DataFrame) -> None:
    floors = backtest.noise_floors(predictions)
    assert set(floors) == {NATIONAL, "Pune", "Goa"}
    assert floors["Goa"] > floors[NATIONAL]


def test_a_quiet_series_raises_no_alarm_on_its_weekly_peaks() -> None:
    y = trending_series(500, noise=0.02)
    assert (anomaly.seasonal_z(y).abs() >= anomaly.MODIFIED_Z).mean() < 0.01


def test_plain_z_misses_a_spike_that_the_seasonal_detector_finds() -> None:
    y = trending_series(500, noise=0.02)
    tuesday = y.index[(y.index.dayofweek == 1) & (np.arange(500) > 300)][0]
    spiked = y.copy()
    spiked[tuesday] *= 1.3  # a real +30%, but still below the usual weekend peak
    assert abs(anomaly.seasonal_z(spiked)[tuesday]) >= anomaly.MODIFIED_Z
    assert abs(anomaly.plain_z(spiked)[tuesday]) < anomaly.PLAIN_Z


def test_detector_can_run_live() -> None:
    y = trending_series(400, noise=0.02)
    day = y.index[300]
    changed = y.copy()
    changed.iloc[301:] *= 9
    assert anomaly.score(y).loc[day].equals(anomaly.score(changed).loc[day])


def test_expected_value_is_close_on_a_quiet_series() -> None:
    y = trending_series(400)
    scored = anomaly.score(y).dropna()
    assert ((scored["expected"] - scored["actual"]).abs() / scored["actual"]).max() < 0.03


def test_an_earlier_anomaly_does_not_hide_a_later_one() -> None:
    y = trending_series(500, noise=0.02)
    first = y.index[300]
    second = first + pd.Timedelta(days=7)  # same weekday: the first sits in its baseline
    y[first] *= 2.0
    y[second] *= 1.4
    z = anomaly.seasonal_z(y)
    assert abs(z[first]) >= anomaly.MODIFIED_Z and abs(z[second]) >= anomaly.MODIFIED_Z


def test_injection_changes_only_labelled_days_by_the_labelled_amount() -> None:
    y = trending_series(800, noise=0.02)
    changed, labels = anomaly.inject(y, np.random.default_rng(3))
    touched = changed[changed != y].index
    assert set(touched) == set(labels[labels.notna()].index)
    assert (changed[labels == "drop_50pct"] / y[labels == "drop_50pct"]).round(6).eq(0.5).all()
    assert (labels == "outage_40pct_3days").sum() == 6
    assert labels.notna().sum() == 2 * 6 + 6
    assert labels.notna().to_numpy().nonzero()[0].min() >= anomaly.WARMUP


def test_injected_events_stay_apart() -> None:
    y = trending_series(800)
    for seed in range(30):
        _, labels = anomaly.inject(y, np.random.default_rng(seed))
        days = labels.notna().to_numpy().nonzero()[0]
        gaps = np.diff(days)
        assert set(gaps[gaps > 1]) == set() or gaps[gaps > 1].min() > anomaly.MIN_GAP_DAYS


def test_injection_refuses_a_short_series() -> None:
    with pytest.raises(ValueError, match="too short"):
        anomaly.inject(trending_series(200), np.random.default_rng(0))


def test_injection_is_repeatable() -> None:
    y = trending_series(800, noise=0.02)
    a, b = (anomaly.inject(y, np.random.default_rng(5)) for _ in range(2))
    pd.testing.assert_series_equal(a[0], b[0])


def test_scores_are_computed_from_the_confusion_counts() -> None:
    def perfect(changed: pd.Series) -> pd.Series:
        ratio = changed / trending_series(len(changed), noise=0.02)
        return (ratio - 1).abs().gt(1e-9).astype(float).mul(10)

    y = trending_series(800, noise=0.02)
    result = anomaly.evaluate({"orders": {"x": y}}, perfect, 3.5, seeds=2)
    assert result["precision"] == 1.0 and result["recall"] == 1.0 and result["f1"] == 1.0
    assert result["false_alarms_per_1000_normal_days"] == 0.0
    assert result["injected_days"] == 2 * 18
    assert set(result["recall_by_event"]) == {name for name, _, _ in anomaly.EVENTS}
    assert result["recall_by_scope"] == {"x": 1.0}


def test_seasonal_detector_beats_plain_z_on_injected_events() -> None:
    panel = {"orders": {"x": trending_series(800, noise=0.02)}}
    seasonal = anomaly.evaluate(panel, anomaly.seasonal_z, anomaly.MODIFIED_Z, seeds=3)
    plain = anomaly.evaluate(panel, anomaly.plain_z, anomaly.PLAIN_Z, seeds=3)
    assert seasonal["recall"] > 0.9 and seasonal["recall"] > plain["recall"]
    assert seasonal["precision"] > 0.9


def test_real_day_flags_have_a_row_per_anomaly() -> None:
    y = trending_series(500, noise=0.02)
    y.iloc[400] *= 2
    flags = anomaly.flag_real_days({"orders": {"x": y}})
    assert y.index[400] in set(flags["order_date"])
    assert list(flags.columns) == ["metric", "scope", "order_date", "actual", "expected", "z"]


def warehouse_with_marts(path: Path, days: int = 420) -> None:
    index = pd.date_range("2024-01-01", periods=days, freq="D")
    national = pd.DataFrame(
        {
            "order_date": index,
            "orders": trending_series(days, 0.01, 1, 9000).round().to_numpy(),
            "gmv": trending_series(days, 0.01, 1, 4_300_000).round().to_numpy(),
        }
    )
    cities = pd.concat(
        pd.DataFrame(
            {
                "order_date": index,
                "city": city,
                "orders": trending_series(days, 0.04, seed, size).round().to_numpy(),
                "gmv": trending_series(days, 0.04, seed, size * 480).round().to_numpy(),
            }
        )
        for city, seed, size in (("Pune", 2, 900), ("Goa", 3, 300))
    )
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA marts")
    for name, frame in (("mart_daily_kpis", national), ("mart_city_daily", cities)):
        con.register("frame_view", frame)
        con.execute(f"CREATE TABLE marts.{name} AS SELECT * FROM frame_view")
        con.unregister("frame_view")
    con.close()


@pytest.fixture
def built(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(anomaly, "SEEDS", 1)
    db = tmp_path / "w.duckdb"
    warehouse_with_marts(db)
    return db


def test_run_all_saves_tables_and_a_report(built: Path, tmp_path: Path) -> None:
    report = run_all(built, tmp_path, top_cities=2)
    saved = json.loads((tmp_path / "forecast_backtest.json").read_text(encoding="utf-8"))
    assert saved["folds"] == 8 and saved["horizon_days"] == 14 and saved["series"] == 6
    assert saved["last_test_day"] == "2025-02-23"
    assert set(saved["metrics"]) == {"orders", "gmv"}
    assert "SYNTHETIC" in report["anomalies"]["labels"]
    con = duckdb.connect(str(built), read_only=True)
    rows = dict(
        con.execute(
            "select table_name, estimated_size from duckdb_tables() where schema_name = 'forecast'"
        ).fetchall()
    )
    con.close()
    assert set(rows) == {
        "backtest",
        "accuracy",
        "next_days",
        "anomalies",
        "detector_scores",
        "event_recall",
        "noise_floor",
    }
    assert rows["next_days"] == 2 * 3 * 3 * 14


def test_rerun_replaces_the_results(built: Path, tmp_path: Path) -> None:
    run_all(built, tmp_path, top_cities=2)
    run_all(built, tmp_path, top_cities=2)
    con = duckdb.connect(str(built), read_only=True)
    rows = con.execute("select count(*) from forecast.next_days").fetchone()
    con.close()
    assert rows == (2 * 3 * 3 * 14,)


def test_cli_prints_the_scores(
    built: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(built.parent))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["forecast", "--dataset", "w"])
    assert result.exit_code == 0, result.output
    assert "best model" in result.output and "national MAPE" in result.output

    page = AppTest.from_file(str(REPO_ROOT / "app/pages/5_Forecasts.py"), default_timeout=120).run()
    get_settings.cache_clear()
    assert not page.exception
    assert "Lowest error across all series" in page.caption[0].value
    assert {tile.label for tile in page.metric} >= {
        "Seasonal naive (baseline)",
        "ETS (Holt-Winters)",
        "LightGBM",
    }
    assert any("Days flagged in the real" in m.value for m in page.markdown)
    assert any("even a perfect forecast" in c.value for c in page.caption)

    page.radio[0].set_value("gmv").run()
    get_settings.cache_clear()
    assert not page.exception  # the noise floor is for order counts only
    assert not any("even a perfect forecast" in c.value for c in page.caption)


def test_cli_needs_a_warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["forecast", "--dataset", "nope"])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "no warehouse" in result.output


def test_page_names_every_event_the_detector_scores() -> None:
    source = (REPO_ROOT / "app/pages/5_Forecasts.py").read_text(encoding="utf-8")
    for name, _, _ in anomaly.EVENTS:
        assert f'"{name}":' in source


def test_future_index_starts_the_day_after_the_history() -> None:
    y = trending_series(30)
    days = future_index(y, 3)
    assert days[0] == y.index[-1] + pd.Timedelta(days=1) and len(days) == 3
