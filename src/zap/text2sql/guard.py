"""Decide whether generated SQL may run, by parsing it instead of searching for bad words.

A keyword blacklist fails in both directions: it misses `COPY` and `read_csv`, and it blocks
`select 'drop'`. Here the SQL becomes a syntax tree, and it must be exactly one read-only
statement over allowed tables, using no function that can reach files, settings or the network.

The model's output is untrusted input, so this is the first of two layers. `run_readonly` is the
second: if something gets past the parser, the connection itself still can't write or read files.
Only the SQL rebuilt from the tree runs, with comments removed, so nothing the parser skipped can.

Personal data is kept out by allowing only tables that hold none. Blocking column names doesn't
work: `SELECT c FROM marts.dim_customer c` returns whole rows without naming a single column.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_ROWS = 1000
TIMEOUT_SECONDS = 15
MEMORY_LIMIT = "2GB"

ALLOWED_TABLES = frozenset(
    f"marts.{table}"
    for table in (
        "dim_city",
        "dim_customer_public",
        "dim_date",
        "dim_food",
        "dim_restaurant",
        "fct_order_items",
        "fct_orders",
        "fct_reviews",
        "mart_city_daily",
        "mart_daily_kpis",
        "mart_delivery_sla",
        "mart_restaurant_performance",
        "mart_review_insights",
    )
) | frozenset({"governance.dq_results", "forecast.accuracy", "forecast.next_days"})

# Any call whose name starts with one of these can reach outside the query.
BLOCKED_FUNCTION_PREFIXES = (
    "read_",
    "scan_",
    "write_",
    "parquet_",
    "sniff_",
    "duckdb_",
    "pragma_",
    "pg_",
    "sqlite_",
    "postgres_",
    "mysql_",
    "iceberg_",
    "delta_",
    "load_",
    "http",
)
BLOCKED_FUNCTIONS = frozenset(
    {"glob", "getenv", "current_setting", "query", "query_table", "which_secret", "getvariable"}
)


class GuardError(ValueError):
    """The SQL may not run. The message is sent back to the model to fix."""


def _parse(sql: str) -> Any:
    try:
        statements = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except ParseError as err:
        raise GuardError(f"not valid SQL: {str(err)[:200]}") from err
    if len(statements) != 1:
        raise GuardError(f"exactly one statement is allowed, got {len(statements)}")
    return statements[0]


def _check_is_read_only(tree: exp.Expression) -> None:
    if not isinstance(tree, exp.Query) or isinstance(tree, exp.Table):
        raise GuardError(f"only SELECT is allowed, got {type(tree).__name__}")
    if tree.find(exp.Into, exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Drop, exp.Create):
        raise GuardError("the statement changes data")
    if any(cte.args.get("recursive") for cte in tree.find_all(exp.With)):
        raise GuardError("recursive queries are not allowed")


def _check_functions(tree: exp.Expression) -> None:
    for call in tree.find_all(exp.Anonymous, exp.Func):
        name = (call.name if isinstance(call, exp.Anonymous) else call.sql_name()).lower()
        if name in BLOCKED_FUNCTIONS or name.startswith(BLOCKED_FUNCTION_PREFIXES):
            raise GuardError(f"function {name} is not allowed")


def _check_tables(tree: exp.Expression, allowed: frozenset[str]) -> None:
    cte_names = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise GuardError(f"only named tables can be read, got {table.sql(dialect='duckdb')}")
        if table.args.get("catalog"):
            raise GuardError("table names can't include a database")
        schema, name = table.db.lower(), table.name.lower()
        if not schema and name in cte_names:
            continue
        full = f"{schema}.{name}"
        if full not in allowed:
            raise GuardError(f"table {full.strip('.')} is not allowed")


def _with_row_limit(tree: exp.Expression, max_rows: int) -> str:
    limit = tree.args.get("limit")
    written = limit.expression if isinstance(limit, exp.Limit) else None
    if isinstance(written, exp.Literal) and written.is_int and int(written.this) <= max_rows:
        return tree.sql(dialect="duckdb", comments=False)
    if limit is None and isinstance(tree, exp.Select):
        return tree.limit(max_rows).sql(dialect="duckdb", comments=False)
    # Anything else (a set operation, a computed limit): cap it from the outside.
    return (
        f"SELECT * FROM ({tree.sql(dialect='duckdb', comments=False)}) AS guarded LIMIT {max_rows}"  # noqa: S608 - parsed and checked above
    )


def check_sql(sql: str, allowed: frozenset[str] = ALLOWED_TABLES, max_rows: int = MAX_ROWS) -> str:
    """Return SQL that is safe to run, with a row limit, or raise GuardError saying why."""
    tree = _parse(sql)
    _check_is_read_only(tree)
    _check_functions(tree)
    _check_tables(tree, allowed)
    return _with_row_limit(tree, max_rows)


def run_readonly(db_path: Path, sql: str, timeout: float = TIMEOUT_SECONDS) -> pd.DataFrame:
    """Run checked SQL with no file or network access, and stop it if it takes too long."""
    con = duckdb.connect(
        str(db_path),
        read_only=True,
        config={
            "enable_external_access": False,
            "python_enable_replacements": False,
            "memory_limit": MEMORY_LIMIT,
            "lock_configuration": True,
        },
    )
    timer = threading.Timer(timeout, con.interrupt)
    timer.start()
    try:
        return con.execute(sql).df()
    finally:
        timer.cancel()
        con.close()
