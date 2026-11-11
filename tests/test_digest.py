"""The scheduled digest: changes get tested before they're called changes."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

from test_ai import ScriptedResponses
from zap.cli import app
from zap.config import Settings, get_settings
from zap.dashboard import data
from zap.fixtures import FixtureResult
from zap.insights import digest
from zap.llm.client import LlmClient
from zap.pipeline import PipelineRun

Pipeline = tuple[Settings, PipelineRun, FixtureResult]


def facts(z: float = 0.5, cities: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "this_week": {
            "orders": 1010.0,
            "gmv": 5.2e7,
            "cancel_rate": 0.06,
            "sla_breach_rate": 0.19,
        },
        "last_week": {
            "orders": 1000.0,
            "gmv": 5.0e7,
            "cancel_rate": 0.05,
            "sla_breach_rate": 0.18,
        },
        "orders_z": z,
        "cities": cities or [{"city": "Pune", "change": 0.01, "z": 0.2, "q": 0.9}],
        "forecast_next_week": 1020.0,
    }


def client(tmp_path: Path, text: str) -> LlmClient:
    api = SimpleNamespace(responses=ScriptedResponses([json.dumps({"text": text})]))
    return LlmClient(Settings(llm_mode="live", llm_replay_dir=tmp_path / "replay"), api=api)


def test_surprise_measures_a_gap_against_its_noise() -> None:
    assert digest.surprise(100, 100) == 0
    assert digest.surprise(0, 0) == 0
    assert digest.surprise(1200, 1000) > 3
    assert digest.surprise(1010, 1000) < 1


def test_a_small_change_is_called_noise() -> None:
    lines = digest.findings(facts())
    assert "Orders rose 1.0% to 1,010" in lines[0]
    assert "within normal variation" in lines[0]
    assert "None of the 1 largest cities moved beyond noise." in lines


def test_a_large_change_is_called_one() -> None:
    assert "beyond normal variation" in digest.findings(facts(z=4.0))[0]


def test_a_city_is_named_only_after_correction() -> None:
    moved = [{"city": "Pune", "change": -0.2, "z": -5.0, "q": 0.001}]
    assert any("Pune (-20.0%)" in line for line in digest.findings(facts(cities=moved)))


def test_a_missing_forecast_is_left_out() -> None:
    gone = facts()
    gone["forecast_next_week"] = None
    assert not any("forecast" in line for line in digest.findings(gone))


def test_the_brief_keeps_the_numbers_it_was_given(tmp_path: Path) -> None:
    lines = ["Orders rose 1.0% to 1,010."]
    text = "Orders rose 1.0% to 1,010, which is ordinary."
    assert digest.write_brief(client(tmp_path, text), lines) == text


def test_a_brief_with_an_invented_number_is_dropped(tmp_path: Path) -> None:
    lines = ["Orders rose 1.0% to 1,010."]
    assert digest.write_brief(client(tmp_path, "Orders reached 4,321."), lines) is None


def test_no_model_means_no_brief(tmp_path: Path) -> None:
    replay = LlmClient(Settings(llm_mode="replay", llm_replay_dir=tmp_path / "replay"))
    assert digest.write_brief(replay, ["Orders rose 1.0%."]) is None


def test_the_digest_runs_on_a_warehouse_and_is_saved(pipeline: Pipeline, tmp_path: Path) -> None:
    report = digest.analyse(pipeline[0].duckdb_path("fixture"))
    assert report["forecast_next_week"] is None  # no forecasts built for the fixture
    assert report["cities"] and report["findings"]
    assert all(0 <= city["q"] <= 1 for city in report["cities"])

    folder = tmp_path / "digest"
    assert digest.load_latest(folder) is None
    digest.save(report, folder)
    digest.save(report, folder)
    assert digest.load_latest(folder) == report
    assert len((folder / digest.HISTORY).read_text(encoding="utf-8").splitlines()) == 2


def test_the_command_writes_the_digest(
    pipeline: Pipeline, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(pipeline[0].warehouse_dir))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    get_settings.cache_clear()
    page = tmp_path / "docs" / "digest.md"
    result = CliRunner().invoke(
        app, ["digest", "--dataset", "fixture", "--no-brief", "--markdown", str(page)]
    )
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    saved = digest.load_latest(tmp_path / "digest")
    assert saved is not None
    text = page.read_text(encoding="utf-8")
    assert "(SYNTHETIC fixture)" in text and "## Findings" in text
    assert all(f"- {line}" in text for line in saved["findings"])
    assert text.count("\n| ") == len(saved["cities"]) + 1  # a header, then a row per city


def test_the_markdown_page_shows_the_brief_and_a_city_new_this_week() -> None:
    report = facts(cities=[{"city": "Goa", "this_week": 4, "last_week": 0, "change": None}])
    report["cities"][0].update(z=2.0, q=0.5)
    report.update(
        period={"first": "2026-06-24", "last": "2026-06-30"},
        previous={"first": "2026-06-17", "last": "2026-06-23"},
        dataset="full",
        brief="Orders held steady.",
        findings=["Orders rose 1.0%."],
    )
    text = digest.to_markdown(report)
    assert "Orders held steady." in text and "orders and reviews are synthetic" in text
    assert "| Goa | 4 | 0 | n/a | +2.00 | 0.500 |" in text
    report["brief"] = None
    assert "None. A brief is written only with `LLM_MODE=live`" in digest.to_markdown(report)


def test_review_helpers_need_the_ai_schema(pipeline: Pipeline) -> None:
    assert not data.has_review_labels(pipeline[0].duckdb_path("fixture"))
