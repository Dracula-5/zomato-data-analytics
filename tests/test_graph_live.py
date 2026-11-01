"""The graph against a real Neo4j with GDS, on the SYNTHETIC fixture.

THIS WIPES THE DATABASE, so it only runs when NEO4J_TEST_URI names a server to use:

    NEO4J_TEST_URI=bolt://localhost:7687 uv run pytest tests/test_graph_live.py
"""

import os
from pathlib import Path

import duckdb
import pytest
from pydantic import SecretStr
from streamlit.testing.v1 import AppTest

from helpers import REPO_ROOT
from zap.config import Settings, get_settings
from zap.fixtures import FixtureResult
from zap.kg.analysis import run_analysis, run_query
from zap.kg.gds import run_gds
from zap.kg.load_neo4j import STEPS, connect, load_graph
from zap.pipeline import PipelineRun

URI = os.environ.get("NEO4J_TEST_URI")
pytestmark = pytest.mark.skipif(not URI, reason="set NEO4J_TEST_URI to run; it wipes the database")

Pipeline = tuple[Settings, PipelineRun, FixtureResult]


@pytest.fixture(scope="module")
def driver():  # type: ignore[no-untyped-def]
    settings = Settings(
        neo4j_uri=URI or "",
        neo4j_password=SecretStr(os.environ.get("NEO4J_PASSWORD", "")),
    )
    with connect(settings) as live:
        live.verify_connectivity()
        yield live


@pytest.fixture
def db(pipeline: Pipeline) -> Path:
    return pipeline[0].duckdb_path("fixture")


def test_load_matches_the_warehouse_and_a_reload_changes_nothing(driver, db: Path) -> None:  # type: ignore[no-untyped-def]
    first = load_graph(driver, db, reset=True)
    assert [s.step for s in first.steps if not s.ok] == []
    second = load_graph(driver, db)
    assert [s.in_graph for s in second.steps] == [s.in_graph for s in first.steps]


def test_no_personal_properties_in_the_graph(driver, db: Path) -> None:  # type: ignore[no-untyped-def]
    load_graph(driver, db, reset=True)
    frame = run_query(driver, "nodes_by_label")
    assert set(frame["label"]) >= {"User", "Restaurant", "Order"}
    with driver.session() as session:
        keys = session.run("MATCH (u:User) UNWIND keys(u) AS key RETURN DISTINCT key").value()
    assert set(keys) <= {"id", "age_band", "occupation"}


def test_gds_can_run_twice_without_piling_up_relationships(driver, db: Path) -> None:  # type: ignore[no-untyped-def]
    load_graph(driver, db, reset=True)
    first = run_gds(driver)
    second = run_gds(driver)
    assert (
        first["similarity"]["relationshipsWritten"] == second["similarity"]["relationshipsWritten"]
    )
    with driver.session() as session:
        rows = session.run("MATCH ()-[r:SIMILAR_TO]->() RETURN count(r)").single(strict=True)[0]
    assert rows == first["similarity"]["relationshipsWritten"]


def test_every_query_runs_and_results_are_published(driver, db: Path, tmp_path: Path) -> None:  # type: ignore[no-untyped-def]
    load_graph(driver, db, reset=True)
    report = run_analysis(driver, db, tmp_path)
    assert (tmp_path / "graph_metrics.json").exists()
    assert report["nodes"]["Order"] > 0
    assert 0 <= report["signal"]["share_of_restaurants_with_a_menu"] <= 1
    con = duckdb.connect(str(db), read_only=True)
    tables = {
        row[0]
        for row in con.execute(
            "select table_name from duckdb_tables() where schema_name = 'kg'"
        ).fetchall()
    }
    con.close()
    assert {"nodes", "signal", "communities", "scores", "outlier_tests"} <= tables


def test_the_page_renders_from_the_published_tables(
    driver,  # type: ignore[no-untyped-def]
    pipeline: Pipeline,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = pipeline[0]
    load_graph(driver, settings.duckdb_path("fixture"), reset=True)
    run_analysis(driver, settings.duckdb_path("fixture"), tmp_path)
    monkeypatch.setenv("WAREHOUSE_DIR", str(settings.warehouse_dir))
    get_settings.cache_clear()
    page = AppTest.from_file(
        str(REPO_ROOT / "app/views/6_Knowledge_Graph.py"), default_timeout=120
    ).run()
    get_settings.cache_clear()
    assert not page.exception
    assert any("Orders are a SAMPLE" in c.value for c in page.caption)


def test_the_steps_cover_every_planned_relationship() -> None:
    names = " ".join(step.name for step in STEPS)
    for rel in ("ORDERED", "REVIEWED", "SERVES", "LOCATED_IN", "HAS_CUISINE"):
        assert rel in names
