from pathlib import Path

import pytest
from pydantic import ValidationError

from zap.config import Settings, get_settings


def test_defaults() -> None:
    s = Settings()
    assert s.target == "duckdb"
    assert s.data_dir == Path("data")
    assert s.sample_pct == 5.0


def test_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", "/somewhere/data")
    monkeypatch.setenv("TARGET", "snowflake")
    monkeypatch.setenv("SAMPLE_PCT", "10")
    s = get_settings()
    assert s.data_dir == Path("/somewhere/data")
    assert s.target == "snowflake"
    assert s.sample_pct == 10.0


def test_dotenv_file_is_read(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("SAMPLE_PCT=2.5\n", encoding="utf-8")
    assert Settings().sample_pct == 2.5


@pytest.mark.parametrize("bad", ["0", "101", "-1"])
def test_sample_pct_bounds(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    monkeypatch.setenv("SAMPLE_PCT", bad)
    with pytest.raises(ValidationError):
        Settings()


def test_unknown_target_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARGET", "bigquery")
    with pytest.raises(ValidationError):
        Settings()


def test_redacted_masks_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-not-leak")
    shown = Settings().redacted()
    assert shown["openai_api_key"] == "***set***"
    assert "sk-test" not in " ".join(shown.values())
    assert shown["s3_bucket"] == "(not set)"


def test_redacted_empty_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert Settings().redacted()["openai_api_key"] == "(empty)"
