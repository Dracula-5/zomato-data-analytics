from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from zap.cli import app
from zap.warehouse.fingerprint import diff, fingerprint

ROWS = "(1, 'a', 10.0, now(), now()), (2, 'b', 20.0, now(), now())"


def _warehouse(path: Path, rows: str = ROWS) -> Path:
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA marts")
    con.execute(
        "CREATE TABLE marts.t (id INT, name VARCHAR, amount DOUBLE, "
        "_loaded_at TIMESTAMPTZ, valid_from TIMESTAMPTZ)"
    )
    con.execute(f"INSERT INTO marts.t VALUES {rows}")
    con.close()
    return path


def _update(path: Path, sql: str) -> None:
    con = duckdb.connect(str(path))
    con.execute(sql)
    con.close()


def test_same_data_in_any_order_and_load_time_matches(tmp_path: Path) -> None:
    a = fingerprint(_warehouse(tmp_path / "a.duckdb"))
    b_path = _warehouse(
        tmp_path / "b.duckdb",
        "(2, 'b', 20.0000000001, now() + INTERVAL 1 DAY, now()), (1, 'a', 10.0, now(), now())",
    )
    assert diff(a, fingerprint(b_path)) == []  # order, lineage time, float noise ignored
    assert a["marts.t"] == {"rows": 2, "checksum": a["marts.t"]["checksum"], "columns": 3}


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE marts.t SET amount = 10.5 WHERE id = 1",
        "UPDATE marts.t SET name = NULL WHERE id = 2",
        "INSERT INTO marts.t VALUES (1, 'a', 10.0, now(), now())",  # a duplicate row
        "DELETE FROM marts.t WHERE id = 2",
        "CREATE TABLE marts.extra AS SELECT 1 AS x",
    ],
)
def test_any_business_change_is_detected(tmp_path: Path, change: str) -> None:
    path = _warehouse(tmp_path / "w.duckdb")
    before = fingerprint(path)
    _update(path, change)
    assert diff(before, fingerprint(path)) != []


def test_cli_compare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path))
    path = _warehouse(tmp_path / "demo.duckdb")
    saved = tmp_path / "fp" / "before.json"
    first = CliRunner().invoke(app, ["fingerprint", "--dataset", "demo", "--out", str(saved)])
    assert first.exit_code == 0 and saved.exists()
    same = CliRunner().invoke(app, ["fingerprint", "--dataset", "demo", "--compare", str(saved)])
    assert same.exit_code == 0 and "IDENTICAL: 1 tables" in same.output
    _update(path, "DELETE FROM marts.t WHERE id = 1")
    changed = CliRunner().invoke(app, ["fingerprint", "--dataset", "demo", "--compare", str(saved)])
    assert changed.exit_code == 1 and "DIFFERENT: marts.t" in changed.output
