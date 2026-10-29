"""Safe building blocks for SQL text that DuckDB can't parameterise (identifiers, file paths).

Values go through `?` parameters wherever DuckDB allows it. Table functions such as
`read_csv('path')` and `COPY ... TO 'path'` need literal text, so everything interpolated
into SQL must pass through one of these two quoting functions.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb


def quote_ident(name: str) -> str:
    """Quote an identifier, doubling any embedded double quote (SQL standard escaping)."""
    return '"' + name.replace('"', '""') + '"'


def quote_literal(value: str | Path) -> str:
    """Quote a string literal; paths use forward slashes so they work on Windows too."""
    text = value.as_posix() if isinstance(value, Path) else value
    return "'" + text.replace("'", "''") + "'"


def fetch_one(con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None) -> Any:
    """Run a query that must return exactly one row and give back that row."""
    row = con.execute(sql, params or []).fetchone()
    if row is None:
        raise RuntimeError(f"query returned no rows: {sql[:120]}")
    return row
