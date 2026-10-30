from pathlib import Path

import pytest
from typer.testing import CliRunner

from helpers import CONTRACTS_DIR, drop_column, rewrite_csv
from zap.cli import app

runner = CliRunner()


def test_info_masks_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-never-print-me")
    result = runner.invoke(app, ["info"])
    assert result.exit_code == 0
    assert "target" in result.output
    assert "sk-never-print-me" not in result.output


def test_fixtures_command(tmp_path: Path) -> None:
    out = tmp_path / "fx"
    result = runner.invoke(app, ["fixtures", "--out", str(out), "--orders", "200"])
    assert result.exit_code == 0, result.output
    assert (out / "orders.csv").exists()
    assert "injected defects" in result.output


def test_fixtures_rejects_tiny_order_count(tmp_path: Path) -> None:
    result = runner.invoke(app, ["fixtures", "--out", str(tmp_path), "--orders", "5"])
    assert result.exit_code != 0


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch, source_copy: Path, tmp_path: Path) -> Path:
    monkeypatch.setenv("CONTRACTS_DIR", str(CONTRACTS_DIR))
    monkeypatch.setenv("DATA_DIR", str(source_copy))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("LAKE_DIR", str(tmp_path / "lake"))
    return source_copy


def test_drift_check_ok_and_logged(cli_env: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["drift-check"])
    assert result.exit_code == 0, result.output
    assert "matches contract" in result.output
    log = tmp_path / "artifacts" / "governance" / "schema_drift_log.jsonl"
    assert len(log.read_text(encoding="utf-8").splitlines()) == 7


def test_drift_check_fails_on_breaking_drift(cli_env: Path) -> None:
    rewrite_csv(cli_env / "orders.csv", drop_column("gst"))
    result = runner.invoke(app, ["drift-check", "--table", "orders"])
    assert result.exit_code == 1
    assert "breaking" in result.output


def test_drift_check_shows_missing_file(cli_env: Path) -> None:
    (cli_env / "menu.csv").unlink()
    result = runner.invoke(app, ["drift-check", "--table", "menu"])
    assert result.exit_code == 0
    assert "missing_file" in result.output


def test_ingest_command_writes_lake_and_manifest(cli_env: Path, tmp_path: Path) -> None:
    result = runner.invoke(app, ["ingest", "--sample-pct", "100", "--dataset", "fixture"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "lake" / "fixture" / "raw" / "orders").is_dir()
    assert (tmp_path / "artifacts" / "ingest" / "fixture" / "manifest.json").is_file()
    assert "total" in result.output


def test_ingest_command_fails_on_drift(cli_env: Path) -> None:
    rewrite_csv(cli_env / "reviews.csv", drop_column("rating"))
    result = runner.invoke(app, ["ingest", "--sample-pct", "100"])
    assert result.exit_code == 1


def test_gen_dbt_sources_is_unchanged() -> None:
    result = runner.invoke(app, ["gen-dbt-sources"])
    assert result.exit_code == 0
    assert "Unchanged" in result.output


def test_load_and_gx_commands(cli_env: Path, tmp_path: Path) -> None:
    assert runner.invoke(app, ["ingest", "--sample-pct", "100", "--dataset", "fx"]).exit_code == 0
    loaded = runner.invoke(app, ["load", "--dataset", "fx"])
    assert loaded.exit_code == 0, loaded.output
    assert (tmp_path / "artifacts" / "load" / "fx" / "latest.json").is_file()
    day = runner.invoke(app, ["load", "--dataset", "fx", "--date", "2026-06-30", "-t", "orders"])
    assert "partition" in day.output
    gx = runner.invoke(app, ["gx", "--dataset", "fx", "--date", "2026-06-30"])
    assert gx.exit_code == 0, gx.output
    assert "gate PASSED" in gx.output


def test_gx_command_fails_on_bad_status(cli_env: Path) -> None:
    def corrupt(rows: list[list[str]]) -> list[list[str]]:
        rows[1][16] = "Lost"
        return rows

    rewrite_csv(cli_env / "orders.csv", corrupt)
    assert runner.invoke(app, ["ingest", "--sample-pct", "100", "--dataset", "bad"]).exit_code == 0
    result = runner.invoke(app, ["gx", "--dataset", "bad"])
    assert result.exit_code == 1
    assert "[error] orders.order_status" in result.output


def test_local_command_reports_failure(cli_env: Path) -> None:
    rewrite_csv(cli_env / "reviews.csv", drop_column("rating"))
    result = runner.invoke(app, ["local", "--sample-pct", "100", "--dataset", "x", "--no-docs"])
    assert result.exit_code == 1
    assert "PIPELINE FAILED" in result.output


def test_dq_report_needs_a_dbt_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path))
    result = runner.invoke(app, ["dq-report", "--dataset", "never_built"])
    assert result.exit_code == 1
    assert "no dbt run for dataset 'never_built'" in result.output
