from pathlib import Path, PureWindowsPath

import duckdb
import pytest

from zap.sqlutil import fetch_one, quote_ident, quote_literal


def test_quote_ident_escapes_double_quotes() -> None:
    assert quote_ident("order_id") == '"order_id"'
    assert quote_ident('we"ird') == '"we""ird"'


def test_quote_literal_escapes_single_quotes() -> None:
    assert quote_literal("O'Brien") == "'O''Brien'"


def test_quote_literal_uses_forward_slashes_for_paths() -> None:
    assert quote_literal(Path(PureWindowsPath(r"C:\data\x.csv").as_posix())) == "'C:/data/x.csv'"


def test_quoted_values_round_trip_through_duckdb() -> None:
    con = duckdb.connect()
    sql = f"select {quote_literal("it's")} as {quote_ident('a "b"')}"
    rel = con.execute(sql)
    assert rel.fetchone() == ("it's",)
    assert rel.description[0][0] == 'a "b"'


def test_fetch_one_raises_on_empty_result() -> None:
    con = duckdb.connect()
    assert fetch_one(con, "select ?", [7]) == (7,)
    with pytest.raises(RuntimeError, match="no rows"):
        fetch_one(con, "select 1 where false")
