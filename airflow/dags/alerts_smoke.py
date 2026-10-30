"""
### alerts_smoke

Proves both alert paths on the real scheduler (manual trigger only, not part of the pipeline):

- `late_task` sleeps past the run's 20-second deadline, so the Deadline Alert callback fires;
- `failing_task` fails with no retries, so the task-failure callback fires.

Both append a line to `artifacts/airflow/alerts.jsonl` (and POST to `ALERT_WEBHOOK_URL` if set).
Run it with `make airflow-smoke`.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, DeadlineAlert, DeadlineReference, SyncCallback

from zap.alerts import on_deadline_missed, on_task_failure

with DAG(
    dag_id="alerts_smoke",
    description="Fires one deadline alert and one task-failure alert on purpose.",
    schedule=None,
    start_date=datetime(2026, 6, 24),
    catchup=False,
    default_args={"retries": 0, "on_failure_callback": on_task_failure},
    deadline=DeadlineAlert(
        reference=DeadlineReference.DAGRUN_QUEUED_AT,
        interval=timedelta(seconds=20),
        callback=SyncCallback(on_deadline_missed),
    ),
    tags=["smoke"],
    doc_md=__doc__,
):
    BashOperator(task_id="late_task", bash_command="sleep 60")
    BashOperator(task_id="failing_task", bash_command="echo 'simulated failure'; exit 1")
