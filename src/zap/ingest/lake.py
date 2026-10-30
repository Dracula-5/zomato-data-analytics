"""Write source CSVs into the lake as Parquet, following the layout S3 will use:

    <lake>/raw/<table>/<table>.parquet                       dimensions (one file)
    <lake>/raw/<table>/<partition>=YYYY-MM-DD/data_0.parquet  facts (Hive-partitioned by day)

Each table is written to a temporary folder and swapped in only after its row counts
reconcile. Re-running is therefore idempotent: the old copy is replaced, never appended to.
Every row gets lineage columns `_batch_id`, `_source_file` and `_loaded_at`.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

import duckdb

from zap.ingest import sample
from zap.ingest.contracts import Contract
from zap.ingest.drift import DriftReport, append_drift_log, check_drift, csv_relation_sql
from zap.sqlutil import fetch_one, quote_ident, quote_literal

TableStatus = Literal[
    "written", "skipped_missing_file", "skipped_missing_dependency", "skipped_dimension"
]


class DriftError(RuntimeError):
    """Raised when any selected table has breaking schema drift. Nothing is written."""

    def __init__(self, reports: list[DriftReport]) -> None:
        self.reports = reports
        details = "; ".join(f"{r.table}: {r.summary()}" for r in reports)
        super().__init__(f"breaking schema drift, nothing loaded: {details}")


@dataclass
class TableResult:
    table: str
    status: TableStatus
    rows_source: int | None = None
    rows_written: int = 0
    files: int = 0
    bytes: int = 0
    partitions: int = 0
    seconds: float = 0.0


@dataclass
class IngestManifest:
    run_id: str
    started_at: str
    source_dir: str
    lake_dir: str
    sample_pct: float
    dataset: str
    date_filter: str | None = None
    tables: list[TableResult] = field(default_factory=list)
    drift: list[DriftReport] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def rows_written(self) -> int:
        return sum(t.rows_written for t in self.tables)

    @property
    def bytes_written(self) -> int:
        return sum(t.bytes for t in self.tables)

    def to_json(self) -> str:
        data = asdict(self)
        data["rows_written"] = self.rows_written
        data["bytes_written"] = self.bytes_written
        return json.dumps(data, indent=2)


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


def table_dir(lake_dir: Path, table: str) -> Path:
    return lake_dir / "raw" / table


def parquet_glob(lake_dir: Path, contract: Contract) -> Path:
    base = table_dir(lake_dir, contract.table)
    return base / ("*/*.parquet" if contract.partition_by else "*.parquet")


def _select_list(contract: Contract, run_id: str, loaded_at: str) -> str:
    cols = []
    for c in contract.stored_columns:
        ident = quote_ident(c.name)
        cols.append(ident if c.type == "VARCHAR" else f"CAST({ident} AS {c.type}) AS {ident}")
    cols += [
        f"{quote_literal(run_id)} AS _batch_id",
        f"{quote_literal(contract.file)} AS _source_file",
        f"TIMESTAMP {quote_literal(loaded_at)} AS _loaded_at",
    ]
    return ", ".join(cols)


DateFilter = tuple[Literal["=", "<="], date]


def _source_query(
    contract: Contract,
    source: Path,
    lake_dir: Path,
    contracts: dict[str, Contract],
    pct: float,
    run_id: str,
    loaded_at: str,
    date_filter: DateFilter | None = None,
) -> str:
    """SELECT over the source CSV with sampling, an optional date filter and partition lookup."""
    query = f"SELECT {_select_list(contract, run_id, loaded_at)} FROM {csv_relation_sql(source)}"  # noqa: S608 - quoted via sqlutil
    conds = []
    if pct < 100 and contract.sample_by == "user":
        conds.append(f"{sample.bucket_sql(quote_ident('user_id'))} < {sample.threshold(pct)}")
    elif pct < 100 and contract.sample_by == "order":
        # In the sample iff the order's USER is, read from the source orders file. Not from
        # the lake: a date-limited lake may not hold the order yet (some reviews are dated
        # before their order), and such a row would never be loaded on any later day.
        orders = csv_relation_sql(source.parent / contracts["orders"].file)
        user_in = f"{sample.bucket_sql(quote_ident('user_id'))} < {sample.threshold(pct)}"
        subquery = f"SELECT CAST(order_id AS BIGINT) FROM {orders} WHERE {user_in}"  # noqa: S608 - quoted via sqlutil
        conds.append(f"CAST(order_id AS BIGINT) IN ({subquery})")
    lookup = contract.partition_lookup
    part = quote_ident(contract.partition_by or "")
    date_sql = ""
    if date_filter is not None:
        op, day = date_filter
        date_sql = f"{op} DATE {quote_literal(day.isoformat())}"
        if contract.partition_by and not lookup:
            conds.append(f"CAST({part} AS DATE) {date_sql}")
    if conds:
        query += " WHERE " + " AND ".join(conds)
    if lookup:
        parent = quote_literal(parquet_glob(lake_dir, contracts[lookup.table]))
        key = quote_ident(lookup.key)
        parents = (
            f"SELECT {key}, min({part}) AS {part} FROM read_parquet({parent}, "  # noqa: S608 - quoted via sqlutil
            "hive_partitioning=false)"
        )
        if date_filter is not None:
            # Date-filtered loads keep only lines whose parent order falls in the window.
            parents += f" WHERE {part} {date_sql}"
            join = "INNER JOIN"
        else:
            # LEFT JOIN keeps lines whose parent is missing (partition value NULL), so orphans
            # are kept and counted by data-quality checks instead of silently disappearing.
            join = "LEFT JOIN"
        query = (
            f"SELECT s.*, p.{part} FROM ({query}) s {join} "  # noqa: S608 - quoted via sqlutil
            f"({parents} GROUP BY {key}) p USING ({key})"
        )
    return query


def _write_table(con: duckdb.DuckDBPyConnection, contract: Contract, query: str, tmp: Path) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    if contract.partition_by:
        partition = quote_ident(contract.partition_by)
        options = (
            f"FORMAT parquet, COMPRESSION zstd, PARTITION_BY ({partition}), "
            "WRITE_PARTITION_COLUMNS true, OVERWRITE true"
        )
        con.execute(f"COPY ({query}) TO {quote_literal(tmp)} ({options})")
    else:
        target = quote_literal(tmp / f"{contract.table}.parquet")
        con.execute(f"COPY ({query}) TO {target} (FORMAT parquet, COMPRESSION zstd)")


def _swap_in(tmp: Path, final: Path) -> None:
    if final.exists():
        shutil.rmtree(final)
    tmp.rename(final)


def _swap_in_partition(tmp: Path, final_table: Path, folder: str) -> None:
    """Replace one day's partition folder; a day with no rows leaves no folder behind."""
    target = final_table / folder
    if target.exists():
        shutil.rmtree(target)
    if (tmp / folder).exists():
        final_table.mkdir(parents=True, exist_ok=True)
        (tmp / folder).rename(target)


def ingest(
    contracts: dict[str, Contract],
    source_dir: Path,
    lake_dir: Path,
    pct: float = 100.0,
    tables: list[str] | None = None,
    drift_log: Path | None = None,
    dataset: str | None = None,
    day: date | None = None,
    until: date | None = None,
    skip_drift: bool = False,
) -> IngestManifest:
    """Check drift on every selected table, then write the lake. Raises `DriftError` early.

    `dataset` labels the run's artifacts (default: `full` or `sample_<pct>pct`); the
    synthetic fixture uses `fixture` so its numbers can never mix with real-data runs.

    `day` extracts ONE day: only that day's partition of each fact table is replaced
    (what the daily Airflow DAG does). `until` writes history up to and including a date
    (used to bootstrap a lake that the DAG then extends one day at a time).

    `skip_drift` is only for a date-filtered run whose drift check already ran as its own
    step (the DAG's drift_check task); full loads are always checked.
    """
    if day is not None and until is not None:
        raise ValueError("use either `day` or `until`, not both")
    date_filter: DateFilter | None = ("=", day) if day else (("<=", until) if until else None)
    if skip_drift and date_filter is None:
        raise ValueError("skip_drift is only allowed together with `day` or `until`")
    started = time.perf_counter()
    run_id = new_run_id()
    loaded_at = datetime.now(UTC).replace(tzinfo=None).isoformat(sep=" ")
    selected = [t for t in contracts if tables is None or t in tables]
    manifest = IngestManifest(
        run_id=run_id,
        started_at=loaded_at,
        source_dir=str(source_dir),
        lake_dir=str(lake_dir),
        sample_pct=pct,
        dataset=dataset or sample.label(pct),
        date_filter=f"{date_filter[0]} {date_filter[1]}" if date_filter else None,
    )
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")  # lets big COPYs stream with less memory

    if skip_drift:
        manifest.drift = [
            DriftReport(
                contracts[t].table,
                contracts[t].file,
                "ok" if (source_dir / contracts[t].file).exists() else "missing_file",
            )
            for t in selected
        ]
    else:
        manifest.drift = [check_drift(contracts[t], source_dir, con) for t in selected]
    if drift_log is not None:
        append_drift_log(manifest.drift, drift_log, run_id)
    broken = [r for r in manifest.drift if r.breaking]
    if broken:
        raise DriftError(broken)

    drift_by_table = {r.table: r for r in manifest.drift}
    for table in selected:
        contract, report = contracts[table], drift_by_table[table]
        t0 = time.perf_counter()
        if report.status == "missing_file":
            manifest.tables.append(TableResult(table, "skipped_missing_file"))
            continue
        if day is not None and not contract.partition_by:
            manifest.tables.append(TableResult(table, "skipped_dimension"))
            continue
        needed = {contract.partition_lookup.table} if contract.partition_lookup else set()
        no_orders_file = (
            pct < 100
            and contract.sample_by == "order"
            and not (source_dir / contracts["orders"].file).exists()
        )
        if no_orders_file or any(not table_dir(lake_dir, d).exists() for d in needed):
            manifest.tables.append(TableResult(table, "skipped_missing_dependency"))
            continue

        query = _source_query(
            contract,
            source_dir / contract.file,
            lake_dir,
            contracts,
            pct,
            run_id,
            loaded_at,
            date_filter,
        )
        tmp = table_dir(lake_dir, table).with_name(f".tmp_{table}_{run_id}")
        try:
            _write_table(con, contract, query, tmp)
            files = sorted(tmp.rglob("*.parquet"))
            # A partitioned COPY of zero rows writes no file at all (a quiet day in a sample).
            written = (
                int(
                    fetch_one(
                        con,
                        f"SELECT count(*) FROM read_parquet({quote_literal(tmp / '**/*.parquet')}, "  # noqa: S608 - quoted via sqlutil
                        "hive_partitioning=false)",
                    )[0]
                )
                if files
                else 0
            )
            if pct >= 100 and date_filter is None and written != report.rows:
                raise RuntimeError(f"{table}: wrote {written} rows but source has {report.rows}")
            result = TableResult(
                table=table,
                status="written",
                rows_source=report.rows,
                rows_written=written,
                files=len(files),
                bytes=sum(f.stat().st_size for f in files),
                partitions=len({f.parent for f in files}) if contract.partition_by else 0,
            )
            if day is not None:
                folder = f"{contract.partition_by}={day.isoformat()}"
                _swap_in_partition(tmp, table_dir(lake_dir, table), folder)
            else:
                _swap_in(tmp, table_dir(lake_dir, table))
        finally:
            if tmp.exists():
                shutil.rmtree(tmp)
        result.seconds = round(time.perf_counter() - t0, 3)
        manifest.tables.append(result)
    con.close()
    manifest.seconds = round(time.perf_counter() - started, 3)
    return manifest


def write_manifest(manifest: IngestManifest, artifacts_dir: Path) -> Path:
    """Latest manifest per dataset, plus an append-only history for metrics."""
    folder = artifacts_dir / "ingest" / manifest.dataset
    folder.mkdir(parents=True, exist_ok=True)
    latest = folder / "manifest.json"
    latest.write_text(manifest.to_json(), encoding="utf-8")
    with (folder / "history.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(json.loads(manifest.to_json())) + "\n")
    return latest
