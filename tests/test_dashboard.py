"""Dashboard: the data layer against the fixture warehouse, the theme, and a headless smoke
test of every Streamlit page (streamlit.testing AppTest runs the page script in-process)."""

import json
import tomllib
from datetime import date
from pathlib import Path

import duckdb
import pandas as pd
import plotly.graph_objects as go
import pytest
from streamlit.testing.v1 import AppTest

from helpers import REPO_ROOT
from zap.config import Settings, get_settings
from zap.dashboard import data
from zap.dashboard.charts import trend
from zap.dashboard.theme import (
    INK,
    SEQUENTIAL,
    SERIES,
    page_css,
    sequential_scale,
    series_color,
    style,
)
from zap.fixtures import FixtureResult
from zap.pipeline import PipelineRun

Pipeline = tuple[Settings, PipelineRun, FixtureResult]
PAGES = ["Home.py", *sorted(f"views/{p.name}" for p in (REPO_ROOT / "app" / "views").glob("*.py"))]


@pytest.fixture
def db(pipeline: Pipeline) -> Path:
    return pipeline[0].duckdb_path("fixture")


def _fct_orders(db: Path) -> int:
    return int(data.query(db, "select count(*) as n from marts.fct_orders")["n"].iloc[0])


def test_available_datasets_puts_full_first(tmp_path: Path) -> None:
    for name in ("fixture", "sample_5pct", "full"):
        (tmp_path / f"{name}.duckdb").touch()
    assert data.available_datasets(tmp_path) == ["full", "sample_5pct", "fixture"]


def test_kpis_agree_with_the_fact_table(db: Path) -> None:
    k = data.kpi_summary(db)
    assert k["orders"] == _fct_orders(db)
    assert 0 < k["cancel_rate"] < 1 and 0 <= k["sla_breach_rate"] < 1
    assert k["first_day"] <= k["last_day"] and k["customers"] > 0
    assert int(data.weekday_hour(db)["orders"].sum()) == _fct_orders(db)
    assert len(data.daily_kpis(db)) == 60


def test_city_and_restaurant_views(db: Path) -> None:
    k = data.kpi_summary(db)
    cities = data.city_summary(db, k["first_day"], k["last_day"])
    assert int(cities["gmv"].sum()) == int(k["gmv"])
    assert list(cities["gmv"]) == sorted(cities["gmv"], reverse=True)
    city = cities["city"].iloc[0]
    assert len(data.city_daily(db, city)) > 0
    top = data.top_restaurants(db, city, 3)
    assert 0 < len(top) <= 3 and list(top["rank"]) == sorted(top["rank"])
    assert data.city_summary(db, date(2000, 1, 1), date(2000, 1, 2)).empty


def test_sla_and_quality_views(db: Path) -> None:
    sla = data.delivery_sla(db, 3)
    assert sla["city"].nunique() <= 3 and sla["order_hour"].between(0, 23).all()
    assert len(data.dq_results(db)) == 26
    tests = data.dbt_test_summary(db)
    assert int(tests["tests"].sum()) > 50
    assert set(tests["status"]) == {"pass"}
    assert len(data.gx_results(db)) > 0


def test_quality_views_without_governance_tables(tmp_path: Path) -> None:
    empty = tmp_path / "empty.duckdb"
    duckdb.connect(str(empty)).close()
    assert data.dbt_test_summary(empty).empty
    assert data.gx_results(empty).empty


def test_latest_drift_keeps_the_newest_check(tmp_path: Path) -> None:
    log = tmp_path / "drift.jsonl"
    assert data.latest_drift(log).empty
    entries = [
        {"table": "orders", "status": "ok", "rows": 1, "removed": []},
        {"table": "orders", "status": "breaking", "rows": 2, "removed": ["gst"]},
        {"table": "food", "status": "ok", "rows": 3},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    frame = data.latest_drift(log)
    assert list(frame["table"]) == ["food", "orders"]
    assert frame.set_index("table").loc["orders", "removed"] == "gst"


def test_theme() -> None:
    assert series_color("light", 0) == SERIES["light"][0]
    assert series_color("dark", 8) == SERIES["dark"][0]  # fixed order, wraps only by design
    scale = sequential_scale("light")
    assert scale[0] == [0.0, SEQUENTIAL["light"][0]] and scale[-1][0] == 1.0
    fig = style(go.Figure(go.Scatter(x=[1, 2], y=[3, 4])), "dark", height=200)
    assert fig.layout.height == 200 and fig.layout.paper_bgcolor == "rgba(0,0,0,0)"


def test_page_css_comes_from_the_viewers_theme() -> None:
    assert INK["light"]["surface"] in page_css("light")
    assert INK["dark"]["surface"] in page_css("dark")
    assert INK["dark"]["surface"] not in page_css("light")


def test_trend_draws_a_weekly_average_over_the_daily_values() -> None:
    days = pd.date_range("2026-01-01", periods=10)
    frame = pd.DataFrame({"order_date": days[::-1], "orders": list(range(10))[::-1]})
    daily, average = trend(frame, "orders", "Orders", "light").data
    assert list(daily.y) == list(range(10))  # sorted by date, whatever order they arrive in
    assert pd.isna(average.y[5]) and average.y[6] == 3  # nothing until a full week exists


def test_pages_can_follow_the_viewers_theme() -> None:
    # An option directly under [theme] pins Streamlit to one theme, while the charts still draw
    # for the other. Separate light and dark sections leave the viewer's choice alone.
    config = tomllib.loads((REPO_ROOT / ".streamlit/config.toml").read_text(encoding="utf-8"))
    assert set(config["theme"]) == {"light", "dark"}


def _app(page: str, monkeypatch: pytest.MonkeyPatch, warehouse: Path, artifacts: Path) -> AppTest:
    monkeypatch.setenv("WAREHOUSE_DIR", str(warehouse))
    monkeypatch.setenv("ARTIFACTS_DIR", str(artifacts))
    get_settings.cache_clear()
    return AppTest.from_file(str(REPO_ROOT / "app" / page), default_timeout=120).run()


@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_without_errors(
    page: str, pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = pipeline[0]
    at = _app(page, monkeypatch, settings.warehouse_dir, settings.artifacts_dir)
    assert not at.exception, [e.value for e in at.exception]
    assert at.title  # every page sets a title
    assert at.sidebar.selectbox[0].value == "fixture"


def test_cities_waits_for_the_end_of_a_date_range(
    pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = pipeline[0]
    at = _app("views/1_Cities.py", monkeypatch, settings.warehouse_dir, settings.artifacts_dir)
    first_day = at.date_input[0].value[0]
    at.date_input[0].set_value((first_day,)).run()  # what the picker sends after one click
    assert not at.exception, [e.value for e in at.exception]
    assert "end date" in at.info[0].value


def test_the_chosen_dataset_is_kept_outside_the_widget(
    pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = pipeline[0]
    at = _app("views/0_Overview.py", monkeypatch, settings.warehouse_dir, settings.artifacts_dir)
    assert at.session_state["chosen_dataset"] == "fixture"


def test_page_without_a_warehouse_explains_what_to_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    at = _app("Home.py", monkeypatch, tmp_path, tmp_path)
    assert not at.exception
    assert "make local" in at.warning[0].value
