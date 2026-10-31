"""Load the S3 lake into Snowflake: versioned SQL, raw tables from the contracts, COPY INTO.

Signs in as the key-pair service user. COPY runs in one transaction with a delete of the
same rows, so a load can be repeated and a failed one leaves the table as it was.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import snowflake.connector

from zap.cloud.keys import private_key_der
from zap.config import Settings
from zap.ingest.contracts import Contract
from zap.sqlutil import quote_ident, quote_literal
from zap.warehouse.duckdb_loader import raw_columns

SQL_TYPES = {
    "VARCHAR": "VARCHAR",
    "BIGINT": "NUMBER(38,0)",
    "INTEGER": "NUMBER(38,0)",
    "DOUBLE": "DOUBLE",
    "DATE": "DATE",
    "TIMESTAMP": "TIMESTAMP_NTZ",
    "BOOLEAN": "BOOLEAN",
}
SIMPLE_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


def connect(settings: Settings) -> Any:
    if not settings.snowflake_account:
        raise ValueError("SNOWFLAKE_ACCOUNT is not set (see .env.example)")
    return snowflake.connector.connect(
        account=settings.snowflake_account,
        user=settings.snowflake_user,
        private_key=private_key_der(settings.snowflake_private_key_path),
        role=settings.snowflake_role,
        warehouse=settings.snowflake_warehouse,
        database=settings.snowflake_database,
        login_timeout=30,
        session_parameters={"QUERY_TAG": "zomato-ai-data-platform", "TIMEZONE": "UTC"},
    )


def render(path: Path, **values: str) -> str:
    """Fill `{{ name }}` markers; one left unfilled is an error, not a broken statement."""
    text = path.read_text(encoding="utf-8")
    for name, value in values.items():
        text = text.replace("{{ " + name + " }}", value)
    missing = re.findall(r"\{\{\s*(\w+)\s*\}\}", text)
    if missing:
        raise ValueError(f"{path.name}: no value for {sorted(set(missing))}")
    return text


def run_script(con: Any, sql: str) -> None:
    for cursor in con.execute_string(sql, remove_comments=True):
        cursor.close()


def column_ident(name: str) -> str:
    """lower_case names stay unquoted (Snowflake folds them to upper case, as dbt expects);
    source names with capitals or spaces, like "Marital Status", keep their exact spelling."""
    return name.upper() if SIMPLE_NAME.match(name) else quote_ident(name)


def snowflake_type(contract_type: str) -> str:
    if contract_type.startswith("DECIMAL"):
        return contract_type.replace("DECIMAL", "NUMBER")
    return SQL_TYPES[contract_type]


def raw_table_ddl(contract: Contract) -> str:
    cols = ", ".join(
        f"{column_ident(name)} {snowflake_type(dtype)}"
        for name, dtype in raw_columns(contract).items()
    )
    return f"CREATE TABLE IF NOT EXISTS RAW.{contract.table.upper()} ({cols})"


def stage_path(contract: Contract, prefix: str, day: date | None = None) -> str:
    path = f"@RAW.LAKE/{prefix}{contract.table}/"
    if day is not None:
        path += f"{contract.partition_by}={day.isoformat()}/"
    return path


def copy_sql(contract: Contract, prefix: str, day: date | None = None) -> str:
    return (
        f"COPY INTO RAW.{contract.table.upper()} FROM {stage_path(contract, prefix, day)} "
        r"PATTERN = '.*\.parquet' MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE "
        "FORCE = TRUE ON_ERROR = ABORT_STATEMENT"
    )


def _rows_loaded(cursor: Any) -> int:
    """Sum `rows_loaded` from COPY's per-file result. 'Copy executed with 0 files' has none."""
    names = [d[0].lower() for d in cursor.description]
    if "rows_loaded" not in names:
        return 0
    i = names.index("rows_loaded")
    return sum(int(row[i]) for row in cursor.fetchall())


@dataclass
class TableLoad:
    table: str
    rows: int
    seconds: float


@dataclass
class LoadRun:
    dataset: str
    partition_date: str | None
    tables: list[TableLoad] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def rows(self) -> int:
        return sum(t.rows for t in self.tables)


def load_table(con: Any, contract: Contract, prefix: str, day: date | None = None) -> TableLoad:
    """Replace a table (or one day of it) from the stage, atomically."""
    started = time.perf_counter()
    table = f"RAW.{contract.table.upper()}"
    cur = con.cursor()
    try:
        cur.execute("BEGIN")
        if day is None:
            cur.execute(f"DELETE FROM {table}")  # noqa: S608 - names come from the contracts
        else:
            column = column_ident(contract.partition_by or "")
            literal = quote_literal(day.isoformat())
            cur.execute(f"DELETE FROM {table} WHERE {column} = {literal}")  # noqa: S608 - names come from the contracts
        cur.execute(copy_sql(contract, prefix, day))
        rows = _rows_loaded(cur)
        cur.execute("COMMIT")
    except Exception:
        cur.execute("ROLLBACK")
        raise
    finally:
        cur.close()
    return TableLoad(contract.table, rows, round(time.perf_counter() - started, 3))


def load_raw(
    con: Any,
    contracts: dict[str, Contract],
    prefix: str,
    dataset: str,
    tables: Iterable[str] | None = None,
    day: date | None = None,
) -> LoadRun:
    started = time.perf_counter()
    run = LoadRun(dataset=dataset, partition_date=day.isoformat() if day else None)
    selected = list(tables or contracts)
    cur = con.cursor()
    for table in selected:
        cur.execute(raw_table_ddl(contracts[table]))
    cur.close()
    for table in selected:
        contract = contracts[table]
        if day is not None and not contract.partition_by:
            continue  # dimensions are full loads
        run.tables.append(load_table(con, contract, prefix, day))
    run.seconds = round(time.perf_counter() - started, 3)
    return run


def write_load_log(run: LoadRun, artifacts_dir: Path) -> Path:
    # One file per day, so a daily run can't overwrite the record of the full load.
    day = f"_{run.partition_date}" if run.partition_date else ""
    path = artifacts_dir / "cloud" / f"snowflake_load_{run.dataset}{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(run) | {"rows": run.rows}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def integration_trust(con: Any) -> dict[str, str]:
    """The two values the IAM role's trust policy needs (phase 2), as Terraform variables."""
    cur = con.cursor()
    try:
        cur.execute("DESC INTEGRATION ZAP_S3")
        props = {row[0]: row[2] for row in cur.fetchall()}
    finally:
        cur.close()
    return {
        "snowflake_iam_user_arn": props["STORAGE_AWS_IAM_USER_ARN"],
        "snowflake_external_id": props["STORAGE_AWS_EXTERNAL_ID"],
    }
