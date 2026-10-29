"""Load the Parquet lake into the DuckDB warehouse's `raw` schema.

This is the local stand-in for Snowflake's `COPY INTO`. Two modes:

* **full**: replace each raw table with the lake's current contents;
* **partition** (`partition_date=...`): delete that day's rows and insert that day's lake
  partition, inside one transaction. Re-running the same day is idempotent.

A source whose lake folder doesn't exist yet becomes an *empty* table with the contract's
schema, so every dbt model still builds and its tests still run.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import duckdb

from zap.ingest.contracts import Contract
from zap.ingest.lake import parquet_glob, table_dir
from zap.sqlutil import fetch_one, quote_ident, quote_literal

RAW_SCHEMA = "raw"
LINEAGE_TYPES = {"_batch_id": "VARCHAR", "_source_file": "VARCHAR", "_loaded_at": "TIMESTAMP"}


@dataclass
class TableLoad:
    table: str
    mode: str  # "full" | "partition" | "empty"
    rows: int
    seconds: float


@dataclass
class LoadResult:
    database: str
    loaded_at: str
    tables: list[TableLoad] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def rows(self) -> int:
        return sum(t.rows for t in self.tables)


def raw_columns(contract: Contract) -> dict[str, str]:
    """Column name to SQL type, in lake order: contract columns, lineage, lookup partition."""
    cols = {c.name: c.type for c in contract.columns} | LINEAGE_TYPES
    if contract.partition_lookup and contract.partition_by:
        cols[contract.partition_by] = "DATE"
    return cols


def _qualified(table: str) -> str:
    return f"{RAW_SCHEMA}.{quote_ident(table)}"


def _create_empty(con: duckdb.DuckDBPyConnection, contract: Contract) -> None:
    cols = ", ".join(f"{quote_ident(n)} {t}" for n, t in raw_columns(contract).items())
    con.execute(f"CREATE OR REPLACE TABLE {_qualified(contract.table)} ({cols})")


def _select_lake(pattern: Path) -> str:
    return f"SELECT * FROM read_parquet({quote_literal(pattern)}, hive_partitioning=false)"  # noqa: S608 - quoted via sqlutil


def load_raw(
    contracts: dict[str, Contract],
    lake_dir: Path,
    db_path: Path,
    tables: list[str] | None = None,
    partition_date: date | None = None,
) -> LoadResult:
    started = time.perf_counter()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    result = LoadResult(database=str(db_path), loaded_at=datetime.now(UTC).isoformat())
    con = duckdb.connect(str(db_path))
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {RAW_SCHEMA}")
    try:
        for name, contract in contracts.items():
            if tables is not None and name not in tables:
                continue
            t0 = time.perf_counter()
            target = _qualified(name)
            if not table_dir(lake_dir, name).exists():
                _create_empty(con, contract)
                mode = "empty"
            elif partition_date is not None and contract.partition_by:
                part = table_dir(lake_dir, name) / f"{contract.partition_by}={partition_date}"
                exists = fetch_one(
                    con,
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = ? AND table_name = ?",
                    [RAW_SCHEMA, name],
                )[0]
                if not exists:
                    _create_empty(con, contract)
                con.execute("BEGIN TRANSACTION")
                con.execute(
                    f"DELETE FROM {target} WHERE {quote_ident(contract.partition_by)} = ?",  # noqa: S608 - quoted via sqlutil
                    [partition_date],
                )
                if part.exists():
                    con.execute(f"INSERT INTO {target} BY NAME {_select_lake(part / '*.parquet')}")
                con.execute("COMMIT")
                mode = "partition"
            elif partition_date is not None:
                continue  # dimensions are reloaded only by full loads
            else:
                con.execute(
                    f"CREATE OR REPLACE TABLE {target} AS "
                    f"{_select_lake(parquet_glob(lake_dir, contract))}"
                )
                mode = "full"
            rows = int(fetch_one(con, f"SELECT count(*) FROM {target}")[0])  # noqa: S608 - quoted via sqlutil
            result.tables.append(TableLoad(name, mode, rows, round(time.perf_counter() - t0, 3)))
    finally:
        con.close()
    result.seconds = round(time.perf_counter() - started, 3)
    return result


def write_load_log(result: LoadResult, artifacts_dir: Path, dataset: str) -> Path:
    folder = artifacts_dir / "load" / dataset
    folder.mkdir(parents=True, exist_ok=True)
    payload = {**asdict(result), "rows": result.rows}
    path = folder / "latest.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (folder / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload) + "\n")
    return path
