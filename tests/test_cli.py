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
