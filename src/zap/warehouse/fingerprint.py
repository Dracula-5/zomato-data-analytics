"""Order-independent fingerprints of warehouse tables, to prove two builds hold the same data.

Each table gets its row count and the sum of a hash of every row. Only business columns are
hashed: lineage columns (`_loaded_at`, ...), dbt's snapshot columns (`dbt_*`) and the SCD2
validity window (`valid_from`, `valid_to`) record *when* something ran, so they differ
between two runs that hold identical data. Floating-point columns are rounded first, because
a parallel SUM can add the same numbers in a different order and change the last bit.

Used to show that re-running a day changes nothing, and that day-by-day incremental runs
end in the same warehouse as one full rebuild.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb

from zap.sqlutil import quote_ident

RUN_TIMING_COLUMNS = frozenset({"valid_from", "valid_to"})
FLOAT_TYPES = frozenset({"DOUBLE", "FLOAT", "REAL"})
FLOAT_DECIMALS = 6


def business_columns(columns: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Drop lineage, dbt snapshot and validity-window columns from (name, type) pairs."""
    return [
        (name, dtype)
        for name, dtype in columns
        if not name.startswith(("_", "dbt_")) and name not in RUN_TIMING_COLUMNS
    ]


def _row_hash(columns: list[tuple[str, str]]) -> str:
    if not columns:
        return "0"
    parts = [
        f"round({quote_ident(name)}, {FLOAT_DECIMALS})"
        if dtype in FLOAT_TYPES
        else quote_ident(name)
        for name, dtype in columns
    ]
    return f"hash({', '.join(parts)})"


def fingerprint(db_path: Path, schemas: tuple[str, ...] = ("marts",)) -> dict[str, dict[str, Any]]:
    """{"schema.table": {"rows", "checksum", "columns"}} for every table in `schemas`."""
    con = duckdb.connect(str(db_path), read_only=True, config={"python_enable_replacements": False})
    try:
        tables: dict[tuple[str, str], list[tuple[str, str]]] = {}
        for schema, table, column, dtype in con.execute(
            "SELECT table_schema, table_name, column_name, data_type "
            "FROM information_schema.columns WHERE list_contains(?, table_schema) "
            "ORDER BY table_schema, table_name, ordinal_position",
            [list(schemas)],
        ).fetchall():
            tables.setdefault((schema, table), []).append((column, dtype))
        result: dict[str, dict[str, Any]] = {}
        for (schema, table), columns in sorted(tables.items()):
            keep = business_columns(columns)
            rows, checksum = con.execute(
                f"SELECT count(*), coalesce(sum({_row_hash(keep)}::HUGEINT), 0) "  # noqa: S608 - quoted via sqlutil
                f"FROM {quote_ident(schema)}.{quote_ident(table)}"
            ).fetchall()[0]
            result[f"{schema}.{table}"] = {
                "rows": int(rows),
                "checksum": str(checksum),  # HUGEINT does not fit a JSON number
                "columns": len(keep),
            }
        return result
    finally:
        con.close()


def diff(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]]) -> list[str]:
    """Tables whose fingerprint differs (or exist on one side only)."""
    return sorted(name for name in a.keys() | b.keys() if a.get(name) != b.get(name))
