"""Integration tests: the whole local pipeline (ingest -> GX -> load -> dbt -> report) on the
synthetic fixture, run once per session. Slow (dbt subprocesses), but this is the test that
proves the warehouse is correct end to end."""

import json
import shutil
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from helpers import CONTRACTS_DIR, make_settings, rewrite_csv
from zap.bench import bench_incremental, bench_queries
from zap.cli import app
from zap.config import Settings, get_settings
from zap.dbt_runner import dbt_target_dir, run_dbt
from zap.docs_dictionary import write_dictionary
from zap.fixtures import FixtureResult
from zap.ingest.contracts import load_contracts
from zap.pipeline import PipelineError, PipelineRun, run_local

# Which dq rule must catch each defect the fixture injects.
DEFECT_RULES = {
    "duplicate_order_id": "ORD_DUPLICATE_ID",
    "amount_mismatch": "ORD_AMOUNT_MISMATCH",
    "orphan_order_restaurant": "ORD_ORPHAN_RESTAURANT",
    "review_before_order": "REV_BEFORE_ORDER",
    "review_restaurant_mismatch": "REV_RESTAURANT_MISMATCH",
    "review_on_undelivered": "REV_ON_UNDELIVERED",
    "null_veg_flag": "FOOD_INVALID_VEG_FLAG",
}


def _sql(settings: Settings, sql: str) -> list[tuple[object, ...]]:
    con = duckdb.connect(str(settings.duckdb_path("fixture")), read_only=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_pipeline_runs_every_step(pipeline: tuple[Settings, PipelineRun, FixtureResult]) -> None:
    settings, run, _ = pipeline
    assert run.ok
    # dbt_deps only runs when dbt_packages/ is missing (a fresh clone, as in CI).
    assert [s.name for s in run.steps if s.name != "dbt_deps"] == [
        "ingest",
        "gx_gate",
        "load_raw",
        "dbt_build",
        "quality_report",
        "dbt_docs",
    ]
    summary = json.loads(
        (settings.artifacts_dir / "pipeline" / "fixture" / "latest.json").read_text()
    )
    assert summary["ok"] and summary["seconds"] > 0


def test_every_injected_defect_is_caught_exactly(
    pipeline: tuple[Settings, PipelineRun, FixtureResult],
) -> None:
    settings, _, fixture = pipeline
    report = json.loads(
        (settings.artifacts_dir / "quality" / "fixture" / "dq_report.json").read_text()
    )
    caught = {r["rule_id"]: r["rows_failed"] for r in report["rule_results"]}
    for defect, injected in fixture.defects.as_dict().items():
        assert caught[DEFECT_RULES[defect]] == injected, defect
    assert report["dbt"]["failed"] == 0 and report["dbt"]["pass_rate_pct"] == 100.0
    assert report["rules"]["records_quarantined"] == 2  # amount mismatch + orphan restaurant
    assert report["gx"]["gate_passed"] is True


def test_facts_reconcile_with_source(pipeline: tuple[Settings, PipelineRun, FixtureResult]) -> None:
    settings, _, fixture = pipeline
    ((distinct_orders,),) = _sql(settings, "select count(distinct order_id) from raw.orders")
    ((fact_orders,),) = _sql(settings, "select count(*) from marts.fct_orders")
    assert distinct_orders == fixture.rows["orders"] - 1  # one exact duplicate
    assert fact_orders == distinct_orders - 2  # two quarantined


def test_no_raw_email_or_password_reaches_marts(
    pipeline: tuple[Settings, PipelineRun, FixtureResult],
) -> None:
    settings, _, _ = pipeline
    text_cols = _sql(
        settings,
        "select table_name, column_name from information_schema.columns "
        "where table_schema = 'marts' and data_type = 'VARCHAR'",
    )
    for table, column in text_cols:
        ((hits,),) = _sql(
            settings,
            f'select count(*) from marts."{table}" where "{column}" like \'fixture.user%\'',
        )
        assert hits == 0, f"raw email in marts.{table}.{column}"
    ((pw,),) = _sql(
        settings,
        "select count(*) from information_schema.columns "
        "where column_name = 'password' and table_schema <> 'raw'",
    )
    assert pw == 0


def test_gx_report_flags_duplicate_as_warning(
    pipeline: tuple[Settings, PipelineRun, FixtureResult],
) -> None:
    settings, _, _ = pipeline
    gx = json.loads(
        (settings.artifacts_dir / "quality" / "fixture" / "gx_results.json").read_text()
    )
    failed = [r for r in gx["results"] if not r["success"]]
    assert [(r["table"], r["column"], r["severity"]) for r in failed] == [
        ("orders", "order_id", "warn")
    ]
    ((rows,),) = _sql(settings, "select count(*) from governance.gx_results")
    assert rows == gx["expectations"]


def test_scd2_snapshot_records_a_change(
    pipeline: tuple[Settings, PipelineRun, FixtureResult], tmp_path: Path
) -> None:
    settings, _, _ = pipeline
    db = tmp_path / "copy.duckdb"
    shutil.copy(settings.duckdb_path("fixture"), db)
    con = duckdb.connect(str(db))
    ids = [
        r[0] for r in con.execute("select id from raw.restaurant order by id limit 2").fetchall()
    ]
    con.execute(
        "update raw.restaurant set rating = '4.9' where id in (?, ?)", ids
    )  # SIMULATED change
    con.close()
    assert run_dbt(["snapshot"], db, settings.dbt_project_dir, capture=True).ok
    con = duckdb.connect(str(db), read_only=True)
    closed = con.execute(
        "select count(*) from snapshots.snap_restaurants where dbt_valid_to is not null"
    ).fetchone()
    current = con.execute(
        "select count(*), count(*) filter (where rating = 4.9) "
        "from snapshots.snap_restaurants where dbt_valid_to is null"
    ).fetchone()
    con.close()
    assert closed == (2,)
    assert current is not None and current[1] >= 2 and current[0] == 60


def test_query_benchmark_answers_match(
    pipeline: tuple[Settings, PipelineRun, FixtureResult],
) -> None:
    settings, _, _ = pipeline
    result = bench_queries(settings.duckdb_path("fixture"), repeats=2)
    for name in ("city_gmv_last_30_days", "monthly_national_gmv"):
        assert result[name]["results_identical"], name
        assert result[name]["fact_median_ms"] > 0


def test_incremental_benchmark_is_correct_and_restores_raw(
    pipeline: tuple[Settings, PipelineRun, FixtureResult], tmp_path: Path
) -> None:
    settings, _, _ = pipeline
    db = tmp_path / "bench.duckdb"
    shutil.copy(settings.duckdb_path("fixture"), db)
    count = "select count(*) from raw.orders"
    before = duckdb.connect(str(db), read_only=True).execute(count).fetchone()
    result = bench_incremental(db, settings.dbt_project_dir, repeats=1)
    after = duckdb.connect(str(db), read_only=True).execute(count).fetchone()
    assert result["result_correct"] and before == after
    assert result["new_rows"]["orders"] > 0
    assert len(result["raw_runs"]) == 1


def test_data_dictionary_covers_models_and_classification(
    pipeline: tuple[Settings, PipelineRun, FixtureResult], tmp_path: Path
) -> None:
    settings, _, _ = pipeline
    target = dbt_target_dir(settings.duckdb_path("fixture"), settings.dbt_project_dir)
    out = write_dictionary(target, load_contracts(CONTRACTS_DIR), tmp_path / "dd.md")
    text = out.read_text(encoding="utf-8")
    assert "### marts.fct_orders" in text and "### raw.users" in text
    assert "| email_masked | VARCHAR | pii |" in text
    assert "| password | VARCHAR | secret |" in text


def test_dq_report_cli_rebuilds_the_same_report(
    pipeline: tuple[Settings, PipelineRun, FixtureResult], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`zap dq-report` (the DAG's last task) must agree with the report `run_local` wrote;
    the GX part goes through GxReport.from_json, so this also checks the JSON round trip."""
    settings, _, _ = pipeline
    for var, path in (
        ("LAKE_DIR", settings.lake_dir),
        ("WAREHOUSE_DIR", settings.warehouse_dir),
        ("ARTIFACTS_DIR", settings.artifacts_dir),
    ):
        monkeypatch.setenv(var, str(path))
    get_settings.cache_clear()
    report_path = settings.artifacts_dir / "quality" / "fixture" / "dq_report.json"
    before = json.loads(report_path.read_text(encoding="utf-8"))
    saved = settings.artifacts_dir / "pipeline" / "fixture" / "dbt_run_results.json"
    result = CliRunner().invoke(
        app, ["dq-report", "--dataset", "fixture", "--run-results", str(saved)]
    )
    assert result.exit_code == 0, result.output
    after = json.loads(report_path.read_text(encoding="utf-8"))
    for key in ("dbt", "gx", "rules", "rule_results"):
        assert after[key] == before[key], key
    assert after["gx"]["expectations"] > 0


def test_gate_failure_stops_before_load(
    fixture_data: tuple[Path, FixtureResult], tmp_path: Path
) -> None:
    source = tmp_path / "bad"
    shutil.copytree(fixture_data[0], source)

    def corrupt(rows: list[list[str]]) -> list[list[str]]:
        rows[1][16] = "Lost"  # unknown order_status: an error-level expectation
        return rows

    rewrite_csv(source / "orders.csv", corrupt)
    settings = make_settings(tmp_path)
    with pytest.raises(PipelineError, match="order_status"):
        run_local(settings, source, "bad", 100.0, docs=False, echo=lambda _: None)
    assert not settings.duckdb_path("bad").exists()
    summary = json.loads((settings.artifacts_dir / "pipeline" / "bad" / "latest.json").read_text())
    assert summary["ok"] is False
