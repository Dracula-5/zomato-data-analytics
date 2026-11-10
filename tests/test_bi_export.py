"""`zap bi-export`: the files Power BI reads, checked on the SYNTHETIC fixture warehouse."""

import json
import re
import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from helpers import REPO_ROOT
from zap.bi_export import (
    KEY_COLUMNS,
    BiExportError,
    add_band,
    band_coverage,
    check_tables,
    export,
    forecast_note,
    forecast_table,
    pick_dataset,
    run_ets,
    rupees,
)
from zap.cli import app
from zap.config import Settings, get_settings
from zap.fixtures import FixtureResult
from zap.pipeline import PipelineRun
from zap.sqlutil import save_frames

Pipeline = tuple[Settings, PipelineRun, FixtureResult]
BI = REPO_ROOT / "bi"
COLUMNS = {
    "mart_city_daily": [
        "order_date",
        "city",
        "orders",
        "delivered_orders",
        "cancelled_orders",
        "gmv",
        "aov",
        "cancel_rate",
        "discount_rate",
        "active_customers",
    ],
    "mart_daily_kpis": [
        "order_date",
        "iso_day_of_week",
        "is_weekend",
        "orders",
        "delivered_orders",
        "gmv",
        "aov",
        "cancel_rate",
        "sla_breach_rate",
        "active_customers",
    ],
    "dim_city": ["city", "localities", "restaurants", "avg_restaurant_rating", "avg_cost_for_two"],
    "dim_date": [
        "date",
        "year",
        "month",
        "month_name",
        "month_label",
        "year_month",
        "month_start",
        "week_start",
        "day_of_month",
        "iso_day_of_week",
        "day_name",
        "is_weekend",
    ],
    "forecast_vs_actual": [
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
    ],
}
MEASURES = {
    "Total GMV",
    "Orders",
    "Delivered Orders",
    "AOV",
    "Cancel Rate",
    "GMV MoM %",
    "GMV vs Forecast %",
}


@pytest.fixture
def warehouse(pipeline: Pipeline, tmp_path: Path) -> Path:
    """A private copy of the fixture warehouse, so a test can add a forecast schema to it."""
    folder = tmp_path / "warehouse"
    folder.mkdir()
    return Path(shutil.copy(pipeline[0].duckdb_path("fixture"), folder / "fixture.duckdb"))


def read(out: Path) -> dict[str, pd.DataFrame]:
    return {name: pd.read_csv(out / f"{name}.csv") for name in COLUMNS}


def test_the_export_is_a_star_schema_power_bi_can_relate(
    warehouse: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(warehouse.parent))
    get_settings.cache_clear()
    out = tmp_path / "bi"
    result = CliRunner().invoke(app, ["bi-export", "--out", str(out)])  # no full: the fixture
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert "Exported dataset 'fixture' (SYNTHETIC fixture)" in result.output

    tables = read(out)
    for name, columns in COLUMNS.items():
        assert list(tables[name].columns) == columns, name
        assert len(tables[name]) > 0, name
        for key in KEY_COLUMNS[name]:
            assert tables[name][key].notna().all(), f"{name}.{key} has empty values"
    city, kpis, cities = tables["mart_city_daily"], tables["mart_daily_kpis"], tables["dim_city"]
    dates, fc = tables["dim_date"], tables["forecast_vs_actual"]
    # Every many-side key finds its one-side row, and each one-side key is unique.
    assert set(city["order_date"]) | set(kpis["order_date"]) | set(fc["date"]) <= set(dates["date"])
    assert set(city["city"]) <= set(cities["city"])
    assert cities["city"].str.casefold().is_unique and dates["date"].is_unique
    assert not city.duplicated(["order_date", "city"]).any()
    assert not fc.duplicated(["date", "scope"]).any()
    # "Mark as date table" needs one row for every day, with no gaps.
    days = pd.to_datetime(dates["date"])
    assert (days.diff().dropna() == pd.Timedelta(days=1)).all()
    # The city mart and the national mart describe the same orders.
    assert city["gmv"].sum() == kpis["gmv"].sum()
    assert city["orders"].sum() == kpis["orders"].sum()

    past, ahead = fc[fc["period"] == "backtest"], fc[fc["period"] == "future"]
    assert set(fc["period"]) == {"backtest", "future"}
    assert past["actual_gmv"].notna().all() and ahead["actual_gmv"].isna().all()
    assert ahead["date"].min() > past["date"].max()
    assert fc[["forecast_gmv", "forecast_orders"]].notna().all().all()
    banded = fc.dropna(subset=["forecast_gmv_lower"])
    assert len(banded) and (banded["forecast_gmv_lower"] <= banded["forecast_gmv_upper"]).all()
    assert {"National", *cities["city"]} >= set(fc["scope"]) >= {"National"}

    manifest = json.loads((out / "export_manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset"] == "fixture"
    assert "fitted by bi-export" in manifest["forecast"]["source"]
    assert manifest["totals_for_checking_the_report"]["gmv"] == city["gmv"].sum()
    assert {t: m["rows"] for t, m in manifest["tables"].items()} == {
        t: len(f) for t, f in tables.items()
    }
    assert "synthetic" in (out / "forecast_note.txt").read_text(encoding="utf-8")


def stored_rows(end: str) -> dict[str, pd.DataFrame]:
    """What `zap forecast` saves: every model, with ETS at 90 and LightGBM at 50."""
    days = pd.date_range(end=end, periods=14)
    ahead = pd.date_range(pd.Timestamp(end) + pd.Timedelta(days=1), periods=14)
    backtest, next_days = [], []
    for metric in ("orders", "gmv"):
        for model, value in (("ets", 90.0), ("lightgbm", 50.0)):
            common = {"metric": metric, "scope": "National", "model": model}
            backtest.append(
                pd.DataFrame(
                    {**common, "fold": 1, "target_date": days, "actual": 100.0, "forecast": value}
                )
            )
            next_days.append(pd.DataFrame({**common, "target_date": ahead, "forecast": value}))
    return {"backtest": pd.concat(backtest), "next_days": pd.concat(next_days)}


def test_a_matching_forecast_run_is_reused_and_a_stale_one_is_not(
    warehouse: Path, tmp_path: Path
) -> None:
    save_frames(warehouse, "forecast", stored_rows("2026-06-30"))  # the fixture's last day
    manifest = export(warehouse, tmp_path / "now")
    fc = read(tmp_path / "now")["forecast_vs_actual"]
    assert manifest["forecast"]["source"] == "forecast schema from `zap forecast`"
    assert set(fc["scope"]) == {"National"} and (fc["forecast_orders"] == 90.0).all()
    ahead = fc[fc["period"] == "future"]
    assert np.allclose(ahead["forecast_gmv_lower"], 100.0)  # 90 x the one fold's 100/90
    assert fc.loc[fc["period"] == "backtest", "forecast_gmv_lower"].isna().all()  # fold 1

    save_frames(warehouse, "forecast", stored_rows("2026-06-23"))
    manifest = export(warehouse, tmp_path / "stale")
    assert "`zap forecast` run is older" in manifest["forecast"]["source"]


def test_the_band_only_learns_from_earlier_folds() -> None:
    backtest = pd.DataFrame(
        {
            "metric": "orders",
            "scope": "National",
            "fold": np.repeat([1, 2, 3], 14),
            "date": pd.date_range("2026-01-01", periods=42),
            "actual": np.r_[np.full(14, 110.0), np.full(14, 90.0), np.full(14, 100.0)],
            "forecast": 100.0,
        }
    )
    future = pd.DataFrame(
        {
            "metric": "orders",
            "scope": "National",
            "date": pd.date_range("2026-02-12", periods=14),
            "forecast": 200.0,
        }
    )
    past, ahead = add_band(backtest, future)
    assert past.loc[past["fold"] == 1, ["lower", "upper"]].isna().all().all()
    fold2 = past[past["fold"] == 2]  # learnt from fold 1 alone: always 10% high
    assert np.allclose(fold2["lower"], 110.0) and np.allclose(fold2["upper"], 110.0)
    fold3 = past[past["fold"] == 3]
    assert np.allclose(fold3["lower"], 90.0) and np.allclose(fold3["upper"], 110.0)
    assert np.allclose(ahead["lower"], 180.0) and np.allclose(ahead["upper"], 220.0)
    # Fold 2 (90) falls outside its band of 110, fold 3 (100) inside 90..110.
    assert band_coverage(past) == {"orders": 50.0, "gmv": None}


def test_the_note_reads_a_falling_forecast_and_a_missing_band() -> None:
    days = pd.date_range("2026-06-17", periods=28)
    backtest = pd.DataFrame(
        {
            "metric": np.repeat(["orders", "gmv"], 14),
            "scope": "National",
            "fold": 1,
            "date": np.tile(days[:14], 2),
            "actual": np.r_[np.full(14, 100.0), np.full(14, 2e6)],
            "forecast": np.r_[np.full(14, 100.0), np.full(14, 2e6)],
        }
    ).assign(lower=np.nan, upper=np.nan)
    future = pd.DataFrame(
        {
            "metric": np.repeat(["orders", "gmv"], 14),
            "scope": "National",
            "date": np.tile(days[14:], 2),
            "forecast": np.r_[np.full(14, 90.0), np.full(14, 1.5e6)],
            "lower": np.nan,
            "upper": np.nan,
        }
    )
    note = forecast_note(forecast_table(backtest, future), {"gmv": 1.234, "orders": 1.0}, None)
    assert "₹2.10 crore expected, 25.0% less than the previous 14 days" in note
    assert "off by 1.23% on average" in note and "shaded band" not in note


@pytest.mark.parametrize(
    ("value", "text"), [(4.4e9, "₹440.00 crore"), (9.23e5, "₹9.23 lakh"), (5320.4, "₹5,320")]
)
def test_rupees_use_indian_units(value: float, text: str) -> None:
    assert rupees(value) == text


def test_too_little_history_for_a_forecast_is_an_error() -> None:
    y = pd.Series(100.0, index=pd.date_range("2026-01-01", periods=30))
    with pytest.raises(BiExportError, match="30 days of history"):
        run_ets({"orders": {"National": y}, "gmv": {"National": y}})


def test_full_data_wins_when_it_is_built(tmp_path: Path) -> None:
    def path(label: str) -> Path:
        return tmp_path / f"{label}.duckdb"

    with pytest.raises(BiExportError, match="make demo"):
        pick_dataset(path, None)
    path("fixture").touch()
    assert pick_dataset(path, None) == "fixture"
    path("full").touch()
    assert pick_dataset(path, None) == "full"
    assert pick_dataset(path, "sample_5pct") == "sample_5pct"


def test_the_command_explains_a_missing_warehouse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path))
    get_settings.cache_clear()
    nothing = CliRunner().invoke(app, ["bi-export", "--out", str(tmp_path / "bi")])
    named = CliRunner().invoke(app, ["bi-export", "--dataset", "nope", "--out", str(tmp_path)])
    get_settings.cache_clear()
    assert nothing.exit_code == 1 and "make demo" in nothing.output
    assert named.exit_code == 1 and "no warehouse at" in named.output


def good_tables() -> dict[str, pd.DataFrame]:
    days = pd.date_range("2026-01-01", "2026-01-31").date
    return {
        "mart_city_daily": pd.DataFrame({"order_date": days[:2], "city": ["Pune", "Goa"]}),
        "mart_daily_kpis": pd.DataFrame({"order_date": days[:2]}),
        "dim_city": pd.DataFrame({"city": ["Pune", "Goa"]}),
        "dim_date": pd.DataFrame({"date": days, "year_month": "2026-01", "month_start": days[0]}),
        "forecast_vs_actual": pd.DataFrame({"date": days[:1], "scope": "Pune", "period": "future"}),
    }


@pytest.mark.parametrize(
    ("table", "frame", "message"),
    [
        ("mart_city_daily", {"order_date": [None], "city": ["Pune"]}, "empty keys: order_date"),
        ("dim_city", {"city": ["Pune", "pune ", "Goa"]}, "differ only by case"),
        ("dim_city", {"city": ["Pune"]}, "missing from dim_city"),
        ("dim_date", {"date": [], "year_month": [], "month_start": []}, "every day"),
    ],
)
def test_anything_that_would_break_a_relationship_stops_the_export(
    table: str, frame: dict[str, list[object]], message: str
) -> None:
    tables = good_tables()
    start, end = tables["dim_date"]["date"].iloc[0], tables["dim_date"]["date"].iloc[-1]
    check_tables(tables, start, end)
    tables[table] = pd.DataFrame(frame)
    with pytest.raises(BiExportError, match=message):
        check_tables(tables, start, end)


def dax_measures(text: str, name_line: str) -> dict[str, str]:
    """{measure: its DAX with comments and spacing removed}, split on each definition line."""
    text = re.sub(r"//[^\n]*", "", text)
    parts = re.split(name_line, text, flags=re.MULTILINE)[1:]
    pairs = zip(parts[::2], parts[1::2], strict=True)
    return {name.strip(): " ".join(body.split()) for name, body in pairs}


def test_the_guide_and_measures_dax_define_the_same_measures() -> None:
    text = (BI / "measures.dax").read_text(encoding="utf-8")
    dax = re.split(r"^EVALUATE", text, flags=re.MULTILINE)[0]
    in_file = dax_measures(dax, r"^\s*MEASURE\s+\w+\[([^\]]+)\]\s*=")
    guide = (BI / "POWER_BI_GUIDE.md").read_text(encoding="utf-8")
    block = guide.split("```dax", 1)[1].split("```", 1)[0]
    in_guide = dax_measures(block, r"^(\S[^=\n]*?) =$")
    assert set(in_file) >= MEASURES
    assert in_guide == in_file  # the same names and the same DAX, so either can be pasted
    missing = [name for name in in_file if f"`{name}`" not in guide.split("```dax")[-1]]
    assert not missing, f"no format given in the guide for {missing}"
    for table in COLUMNS:
        assert f"{table}.csv" in guide


def test_the_theme_is_valid_and_the_screenshot_folder_exists() -> None:
    theme = json.loads((BI / "theme.json").read_text(encoding="utf-8"))
    assert theme["name"] and len(theme["dataColors"]) == 8
    assert all(re.fullmatch(r"#[0-9A-Fa-f]{6}", color) for color in theme["dataColors"])
    assert (BI / "screenshots" / ".gitkeep").exists()
