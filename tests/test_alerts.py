import json
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from zap import alerts


def _read(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_alert_is_appended_to_the_log(tmp_path: Path) -> None:
    log = tmp_path / "a" / "alerts.jsonl"
    alerts.emit_alert(alerts.build_alert("task_failed", "d", "r1", "t"), path=log, webhook="")
    alerts.emit_alert(alerts.build_alert("deadline_missed", "d", "r2"), path=log, webhook="")
    rows = _read(log)
    assert [r["kind"] for r in rows] == ["task_failed", "deadline_missed"]
    assert rows[0]["task_id"] == "t" and rows[1]["task_id"] is None


def test_failure_callback_reads_airflow_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    ti = SimpleNamespace(dag_id="zomato_daily", run_id="manual__1", task_id="load_raw")
    alerts.on_task_failure({"task_instance": ti, "exception": ValueError("boom")})
    (row,) = _read(tmp_path / "airflow" / "alerts.jsonl")
    assert (row["dag_id"], row["task_id"], row["detail"]) == ("zomato_daily", "load_raw", "boom")


def test_deadline_callback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    # The JSON shape Airflow 3.3.2 really sends (seen in airflow/models/deadline.py).
    real = {
        "dag_run": {
            "dag_id": "zomato_daily",
            "dag_run_id": "backfill__2026-06-24",
            "state": "running",
        },
        "deadline": {"id": "0199", "deadline_time": "2026-06-24T00:30:00+00:00"},
    }
    alerts.on_deadline_missed(context=real, dag_id="zomato_daily")
    run = SimpleNamespace(dag_id="zomato_daily", run_id="scheduled__2026-06-25")
    alerts.on_deadline_missed({"dag_run": run, "deadline": None})  # object-style context
    alerts.on_deadline_missed()  # no context at all: must not crash
    rows = _read(tmp_path / "airflow" / "alerts.jsonl")
    assert (rows[0]["kind"], rows[0]["dag_id"], rows[0]["run_id"]) == (
        "deadline_missed",
        "zomato_daily",
        "backfill__2026-06-24",
    )
    assert rows[0]["detail"] == "deadline 2026-06-24T00:30:00+00:00"
    assert rows[1]["run_id"] == "scheduled__2026-06-25" and rows[1]["detail"] == "deadline ?"
    assert rows[2]["dag_id"] == "?"


def test_webhook_gets_json_and_only_https(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[urllib.request.Request] = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: sent.append(req))
    alert = alerts.build_alert("task_failed", "d", "r", "t")
    alerts.emit_alert(alert, path=tmp_path / "a.jsonl", webhook="http://insecure.example")
    assert sent == []  # plain http is refused
    alerts.emit_alert(alert, path=tmp_path / "a.jsonl", webhook="https://hooks.example/x")
    assert len(sent) == 1
    payload = json.loads(sent[0].data)  # type: ignore[arg-type]
    assert payload["kind"] == "task_failed" and payload["text"].startswith("[task_failed]")


def test_webhook_outage_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def down(req: object, timeout: float) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", down)
    alerts.emit_alert(alerts.build_alert("k", "d", "r"), tmp_path / "a.jsonl", "https://x.example")
    assert len(_read(tmp_path / "a.jsonl")) == 1
