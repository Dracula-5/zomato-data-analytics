import json
from datetime import date
from pathlib import Path

import duckdb
import pytest

from helpers import CONTRACTS_DIR
from zap.fixtures import FixtureResult
from zap.ingest.contracts import Contract, load_contracts
from zap.ingest.lake import ingest
from zap.warehouse.duckdb_loader import load_raw, raw_columns, write_load_log


@pytest.fixture
def lake(fixture_data: tuple[Path, FixtureResult], tmp_path: Path) -> Path:
    out = tmp_path / "lake"
    ingest(load_contracts(CONTRACTS_DIR), fixture_data[0], out, pct=100)
    return out


def _count(db: Path, table: str, where: str = "true") -> int:
    con = duckdb.connect(str(db), read_only=True)
    row = con.execute(f"select count(*) from raw.{table} where {where}").fetchone()
    con.close()
    assert row is not None
    return int(row[0])


def test_full_load_matches_lake(
    contracts: dict[str, Contract],
    lake: Path,
    tmp_path: Path,
    fixture_data: tuple[Path, FixtureResult],
) -> None:
    db = tmp_path / "wh.duckdb"
    result = load_raw(contracts, lake, db)
    assert {t.mode for t in result.tables} == {"full"}
    for table, n in fixture_data[1].rows.items():
        assert _count(db, table) == n
    assert result.rows == sum(fixture_data[1].rows.values())


def test_missing_lake_table_becomes_empty_table_with_contract_schema(
    contracts: dict[str, Contract], lake: Path, tmp_path: Path
) -> None:
    import shutil

    shutil.rmtree(lake / "raw" / "users")
    db = tmp_path / "wh.duckdb"
    result = load_raw(contracts, lake, db)
    assert {t.table: t.mode for t in result.tables}["users"] == "empty"
    con = duckdb.connect(str(db), read_only=True)
    cols = [r[0] for r in con.execute("describe raw.users").fetchall()]
    assert cols == list(raw_columns(contracts["users"]))
    assert "password" not in cols  # a secret column stops at ingestion


def test_partition_load_is_idempotent(
    contracts: dict[str, Contract], lake: Path, tmp_path: Path
) -> None:
    db = tmp_path / "wh.duckdb"
    load_raw(contracts, lake, db)
    day = date(2026, 6, 30)
    before = _count(db, "orders", f"order_date = '{day}'")
    total = _count(db, "orders")
    for _ in range(2):  # loading the same day twice must not duplicate it
        result = load_raw(contracts, lake, db, partition_date=day)
    assert _count(db, "orders", f"order_date = '{day}'") == before
    assert _count(db, "orders") == total
    modes = {t.table: t.mode for t in result.tables}
    assert modes["orders"] == "partition"
    assert "food" not in modes  # dimensions are only reloaded by full loads


def test_partition_load_into_new_warehouse_creates_table(
    contracts: dict[str, Contract], lake: Path, tmp_path: Path
) -> None:
    db = tmp_path / "fresh.duckdb"
    load_raw(contracts, lake, db, tables=["orders"], partition_date=date(2026, 6, 30))
    assert _count(db, "orders") == _count(db, "orders", "order_date = '2026-06-30'") > 0


def test_load_log(contracts: dict[str, Contract], lake: Path, tmp_path: Path) -> None:
    result = load_raw(contracts, lake, tmp_path / "wh.duckdb", tables=["food"])
    path = write_load_log(result, tmp_path / "artifacts", "fixture")
    write_load_log(result, tmp_path / "artifacts", "fixture")
    assert json.loads(path.read_text(encoding="utf-8"))["rows"] == result.rows
    assert len((path.parent / "history.jsonl").read_text(encoding="utf-8").splitlines()) == 2
