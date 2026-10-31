"""METRICS.md must never show a number that no run produced."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from zap import cli
from zap.metrics.collect import (
    NOT_RUN,
    Sources,
    collect,
    dig,
    engineering_metrics,
    llm_cost_metrics,
)
from zap.metrics.render import render


def write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_every_metric_is_not_run_when_there_are_no_files(tmp_path: Path) -> None:
    metrics = collect(tmp_path / "a", tmp_path / "r", tmp_path / "spend.jsonl")
    assert len(metrics) > 60
    allowed = {NOT_RUN, "$0.0000", "0"}  # nothing has been spent, which is a true value
    assert {m.value for m in metrics} <= allowed


def test_a_value_comes_from_its_file(tmp_path: Path) -> None:
    artifacts = tmp_path / "a"
    write(
        artifacts / "forecast_backtest.json",
        {
            "metrics": {"orders": {"models": {"ets": {"national_mape": 0.639}}}},
            "ran_at": "2026-10-30T19:00:00",
        },
    )
    metrics = {m.name: m for m in collect(artifacts, tmp_path / "r", tmp_path / "s.jsonl")}
    metric = metrics["National orders MAPE, ETS"]
    assert metric.value == "0.64%" and metric.ran_at == "2026-10-30"
    assert metric.source == "artifacts/forecast_backtest.json"


def test_a_partly_written_file_gives_not_run_rather_than_a_crash(tmp_path: Path) -> None:
    artifacts = tmp_path / "a"
    write(artifacts / "graph_metrics.json", {"communities": {}})
    metrics = {m.name: m for m in collect(artifacts, tmp_path / "r", tmp_path / "s.jsonl")}
    assert metrics["Chains found"].value == NOT_RUN


def test_a_failed_fresh_clone_run_reports_no_time(tmp_path: Path) -> None:
    write(tmp_path / "a" / "fresh_clone.json", {"demo_seconds": 59, "exit_code": 1})
    metrics = {m.name: m for m in collect(tmp_path / "a", tmp_path / "r", tmp_path / "s.jsonl")}
    assert metrics["`make demo` on a fresh clone"].value == NOT_RUN


def test_dig_survives_missing_levels() -> None:
    assert dig({"a": {"b": 1}}, "a", "b") == 1
    assert dig({"a": {}}, "a", "b", "c") is None
    assert dig(None, "a") is None
    assert dig([{"x": 2}], 0, "x") == 2


def test_llm_cost_is_read_from_the_ledger(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    entries = [
        {"purpose": "enrich_reviews", "cost_usd": 0.03},
        {"purpose": "text2sql", "cost_usd": 0.01},
    ]
    ledger.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")
    write(tmp_path / "r" / "enrichment.json", {"reviews": 300000})
    rows = {m.name: m.value for m in llm_cost_metrics(Sources(tmp_path, tmp_path / "r"), ledger)}
    assert rows["Total spent"] == "$0.0400"
    assert rows["Paid calls"] == "2"
    assert rows["Cost per 1,000 enriched reviews"] == "$0.0001"


def test_passing_tests_are_read_from_the_junit_report(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        '<testsuite errors="0" failures="1" skipped="6" tests="100">', encoding="utf-8"
    )
    rows = {m.name: m.value for m in engineering_metrics(Sources(tmp_path, tmp_path), junit)}
    assert rows["Tests passing"] == "93"


def test_only_attacks_that_were_really_refused_are_counted(tmp_path: Path) -> None:
    junit = tmp_path / "pytest.xml"
    passed = '<testcase classname="t" name="test_attack_is_refused[drop]" time="0.1" />'
    failed = (
        '<testcase classname="t" name="test_attack_is_refused[copy]" time="0.1">'
        "<failure>it ran</failure></testcase>"
    )
    junit.write_text(
        f'<testsuite errors="0" failures="1" skipped="0" tests="2">{passed}{failed}',
        encoding="utf-8",
    )
    rows = {m.name: m.value for m in engineering_metrics(Sources(tmp_path, tmp_path), junit)}
    assert rows["SQL attacks refused"] == "1"
    junit.write_text('<testsuite errors="0" failures="0" skipped="0" tests="0">', encoding="utf-8")
    rows = {m.name: m.value for m in engineering_metrics(Sources(tmp_path, tmp_path), junit)}
    assert rows["SQL attacks refused"] == NOT_RUN


def test_the_report_says_how_much_is_measured() -> None:
    metrics = collect(Path("nowhere"), Path("nowhere"), Path("nowhere/s.jsonl"))
    text = render(metrics)
    assert "Nothing here is estimated" in text
    assert "synthetic-looking" in text
    assert "**not run**" in text


def test_the_command_writes_the_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("zap.config.REPO_ROOT", tmp_path)
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "a"))
    monkeypatch.setenv("LLM_REPLAY_DIR", str(tmp_path / "replay"))
    (tmp_path / "docs").mkdir()
    cli.get_settings.cache_clear()
    result = CliRunner().invoke(cli.app, ["metrics"])
    cli.get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert "not run:" in result.output
    assert (tmp_path / "docs" / "METRICS.md").exists()
