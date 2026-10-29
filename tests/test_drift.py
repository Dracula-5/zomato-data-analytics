import json
from pathlib import Path

import duckdb
import pytest

from helpers import Rows, drop_column, rewrite_csv
from zap.ingest.contracts import Contract
from zap.ingest.drift import DriftReport, append_drift_log, check_drift, read_header


@pytest.fixture
def con() -> duckdb.DuckDBPyConnection:
    return duckdb.connect()


def test_fixture_matches_every_contract(
    contracts: dict[str, Contract], source_copy: Path, con: duckdb.DuckDBPyConnection
) -> None:
    for contract in contracts.values():
        report = check_drift(contract, source_copy, con)
        assert report.status == "ok", (contract.table, report.summary())
        assert report.summary() == "matches contract"
        assert report.rows and report.rows > 0


def test_extra_column_is_a_warning(
    contracts: dict[str, Contract], source_copy: Path, con: duckdb.DuckDBPyConnection
) -> None:
    rewrite_csv(
        source_copy / "orders.csv",
        lambda rows: [[*rows[0], "coupon_code"]] + [[*r, "X"] for r in rows[1:]],
    )
    report = check_drift(contracts["orders"], source_copy, con)
    assert report.status == "warn"
    assert report.added == ["coupon_code"]
    assert "extra columns" in report.summary()


def test_removed_column_is_breaking(
    contracts: dict[str, Contract], source_copy: Path, con: duckdb.DuckDBPyConnection
) -> None:
    rewrite_csv(source_copy / "orders.csv", drop_column("gst"))
    report = check_drift(contracts["orders"], source_copy, con)
    assert report.breaking
    assert report.removed == ["gst"]


def test_renamed_column_is_breaking(
    contracts: dict[str, Contract], source_copy: Path, con: duckdb.DuckDBPyConnection
) -> None:
    def rename(rows: Rows) -> Rows:
        rows[0] = ["rating_value" if h == "rating" else h for h in rows[0]]
        return rows

    rewrite_csv(source_copy / "reviews.csv", rename)
    report = check_drift(contracts["reviews"], source_copy, con)
    assert report.breaking
    assert (report.removed, report.added) == (["rating"], ["rating_value"])


def test_reordered_columns_are_a_warning(
    contracts: dict[str, Contract], source_copy: Path, con: duckdb.DuckDBPyConnection
) -> None:
    rewrite_csv(source_copy / "food.csv", lambda rows: [[r[0], r[2], r[1], r[3]] for r in rows])
    report = check_drift(contracts["food"], source_copy, con)
    assert report.status == "warn"
    assert report.reordered
    assert "reordered" in report.summary()


def test_uncastable_values_are_breaking(
    contracts: dict[str, Contract], source_copy: Path, con: duckdb.DuckDBPyConnection
) -> None:
    def corrupt(rows: Rows) -> Rows:
        rows[1][0] = "abc"  # order_id
        rows[2][2] = "31/12/2026"  # order_date in the wrong format
        rows[3][17] = ""  # empty rating is allowed: it is just missing
        return rows

    rewrite_csv(source_copy / "orders.csv", corrupt)
    report = check_drift(contracts["orders"], source_copy, con)
    assert report.breaking
    assert report.type_violations == {"order_id": 1, "order_date": 1}
    assert "uncastable" in report.summary()


def test_missing_file(
    contracts: dict[str, Contract], source_copy: Path, con: duckdb.DuckDBPyConnection
) -> None:
    (source_copy / "users.csv").unlink()
    report = check_drift(contracts["users"], source_copy, con)
    assert report.status == "missing_file"
    assert report.summary() == "file not found in source folder"
    assert report.rows is None
    assert not report.breaking


def test_missing_index_column_is_flagged(
    contracts: dict[str, Contract], source_copy: Path, con: duckdb.DuckDBPyConnection
) -> None:
    rewrite_csv(source_copy / "food.csv", lambda rows: [r[1:] for r in rows])
    report = check_drift(contracts["food"], source_copy, con)
    assert report.status == "warn"
    assert report.index_column_mismatch
    assert "index column" in report.summary()


def test_byte_order_mark_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "bom.csv"
    path.write_bytes(b"\xef\xbb\xbfa,b\n1,2\n")
    assert read_header(path) == ["a", "b"]


def test_drift_log_appends_json_lines(tmp_path: Path) -> None:
    log = tmp_path / "gov" / "drift.jsonl"
    append_drift_log([DriftReport("t", "t.csv", "ok")], log, "run-1")
    append_drift_log([DriftReport("t", "t.csv", "breaking", removed=["x"])], log, "run-2")
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [(r["run_id"], r["status"]) for r in lines] == [("run-1", "ok"), ("run-2", "breaking")]
    assert lines[1]["removed"] == ["x"]
