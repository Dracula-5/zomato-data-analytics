from pathlib import Path

import pytest
from typer.testing import CliRunner

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
