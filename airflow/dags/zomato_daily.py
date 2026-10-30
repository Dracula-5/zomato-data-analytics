"""
### zomato_daily

Replays ONE day of the source data per run (`{{ ds }}` = the logical date) through the
whole pipeline. Every task is idempotent, so re-running a day gives the same warehouse.

| Task | What it does |
|---|---|
| drift_check | source CSV headers and types vs the schema contracts (stops on breaking drift) |
| extract_partition | that day's facts: CSV -> lake partition (replaced, never appended) |
| gx_validate | Great Expectations on that day's partitions (stops on error-level failures) |
| load_raw | delete that day from the warehouse raw tables, insert it again (one transaction) |
| dbt_snapshot | SCD2 history of restaurants |
| dbt_build | incremental MERGE of the facts, marts, all dbt tests |
| quality_report | rules + GX + dbt test results into the governance tables |

Reliability: 2 retries with exponential backoff, a timeout on every task, an alert when a
task finally fails, a Deadline Alert (Airflow 3's replacement for SLAs) when a run is not
finished 30 minutes after it was queued, and `max_active_runs=1` because DuckDB allows
one writer at a time.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow.providers.standard.operators.bash import BashOperator
from airflow.sdk import DAG, DeadlineAlert, DeadlineReference, SyncCallback

from zap.alerts import on_deadline_missed, on_task_failure

# The project's tools run from their own virtualenv, never from Airflow's Python, so dbt,
# DuckDB and Great Expectations versions can't clash with Airflow's own dependencies.
ZAP = "/opt/zap-venv/bin/python -m zap.cli"
ENV = {"PYTHONPATH": "/opt/project/src", "PYTHONUTF8": "1"}
DS = "--dataset {{ params.dataset }}"
FACTS = "-t orders -t order_items -t reviews"

default_args = {
    "owner": "data-platform",
    "retries": 2,
    "retry_delay": timedelta(minutes=1),
    "retry_exponential_backoff": True,
    "max_retry_delay": timedelta(minutes=10),
    "execution_timeout": timedelta(minutes=20),
    "on_failure_callback": on_task_failure,
}


def zap_task(task_id: str, args: str, timeout_min: int = 20) -> BashOperator:
    return BashOperator(
        task_id=task_id,
        bash_command=f"{ZAP} {args}",
        env=ENV,
        append_env=True,
        execution_timeout=timedelta(minutes=timeout_min),
    )


with DAG(
    dag_id="zomato_daily",
    description="Replay one day of Zomato-style orders through lake, warehouse and dbt.",
    schedule="@daily",
    start_date=datetime(2026, 6, 24),
    end_date=datetime(2026, 6, 30),
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    params={"dataset": "airflow_5pct", "sample_pct": 5},
    deadline=DeadlineAlert(
        reference=DeadlineReference.DAGRUN_QUEUED_AT,
        interval=timedelta(minutes=30),
        callback=SyncCallback(on_deadline_missed),
    ),
    tags=["zomato", "dbt", "duckdb"],
    doc_md=__doc__,
) as dag:
    drift_check = zap_task("drift_check", f"drift-check {FACTS}", timeout_min=10)
    extract = zap_task(
        "extract_partition",
        f"ingest {DS} --sample-pct {{{{ params.sample_pct }}}} --date {{{{ ds }}}} "
        f"--skip-drift-check {FACTS}",
    )
    gx_validate = zap_task("gx_validate", f"gx {DS} --date {{{{ ds }}}}", timeout_min=10)
    load_raw = zap_task("load_raw", f"load {DS} --date {{{{ ds }}}}", timeout_min=10)
    dbt_snapshot = zap_task("dbt_snapshot", f"dbt {DS} snapshot")
    dbt_build = zap_task("dbt_build", f"dbt {DS} build --exclude resource_type:snapshot", 30)
    quality_report = zap_task("quality_report", f"dq-report {DS}", timeout_min=5)

    (
        drift_check
        >> extract
        >> gx_validate
        >> load_raw
        >> dbt_snapshot
        >> dbt_build
        >> quality_report
    )
