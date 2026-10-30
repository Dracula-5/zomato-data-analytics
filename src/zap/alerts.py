"""Failure and deadline (SLA) alerts for the Airflow DAG.

Plain Python with no Airflow import, so it is unit-tested like the rest of the package.
Every alert is appended to `<ARTIFACTS_DIR>/airflow/alerts.jsonl`. If `ALERT_WEBHOOK_URL`
is set (for example a Slack incoming webhook), the alert is also POSTed there as JSON.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def build_alert(
    kind: str, dag_id: str, run_id: str, task_id: str | None = None, detail: str = ""
) -> dict[str, Any]:
    return {
        "kind": kind,
        "dag_id": dag_id,
        "run_id": run_id,
        "task_id": task_id,
        "detail": detail,
        "raised_at": datetime.now(UTC).isoformat(),
    }


def _alerts_path() -> Path:
    return Path(os.environ.get("ARTIFACTS_DIR", "artifacts")) / "airflow" / "alerts.jsonl"


def emit_alert(alert: dict[str, Any], path: Path | None = None, webhook: str | None = None) -> None:
    """Record the alert locally, then best-effort POST it to the webhook (never raises)."""
    target = path or _alerts_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(alert) + "\n")
    log.error("ALERT %s", alert)
    url = webhook if webhook is not None else os.environ.get("ALERT_WEBHOOK_URL")
    if not url:
        return
    if not url.startswith("https://"):
        log.warning("ignoring ALERT_WEBHOOK_URL that is not https")
        return
    text = f"[{alert['kind']}] {alert['dag_id']} {alert['run_id']} {alert['task_id'] or ''}"
    body = json.dumps({"text": text, **alert}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})  # noqa: S310 - https enforced above
    try:
        urllib.request.urlopen(req, timeout=5)  # noqa: S310 - https enforced above
    except OSError as err:  # an alerting outage must not fail the pipeline
        log.warning("webhook delivery failed: %s", err)


def on_task_failure(context: dict[str, Any]) -> None:
    """Airflow `on_failure_callback`: fires once a task has used up its retries."""
    ti = context.get("task_instance")
    exc = context.get("exception")
    emit_alert(
        build_alert(
            "task_failed",
            dag_id=str(getattr(ti, "dag_id", "?")),
            run_id=str(getattr(ti, "run_id", "?")),
            task_id=getattr(ti, "task_id", None),
            detail=str(exc)[:500] if exc else "",
        )
    )


def _field(obj: Any, *names: str) -> Any:
    """First non-empty field of a dict (JSON context) or an object, else None."""
    for name in names:
        value = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
        if value is not None:
            return value
    return None


def on_deadline_missed(context: dict[str, Any] | None = None, **kwargs: Any) -> None:
    """Deadline-alert callback: the DAG run did not finish within its SLA window.

    Airflow 3.3 sends the context as JSON: {"dag_run": <REST API DAG run>, "deadline":
    {"id", "deadline_time"}}; the REST API calls the run id `dag_run_id`.
    """
    ctx = context or {}
    dag_run = ctx.get("dag_run") or {}
    deadline = ctx.get("deadline") or {}
    emit_alert(
        build_alert(
            "deadline_missed",
            dag_id=str(_field(dag_run, "dag_id") or kwargs.get("dag_id", "?")),
            run_id=str(_field(dag_run, "dag_run_id", "run_id") or "?"),
            detail=f"deadline {_field(deadline, 'deadline_time') or '?'}",
        )
    )
