"""`zap insights`: run the queries behind the README's business insights and save their results.

Each finding is one SQL file in `docs/insights/`, readable on its own. This runs every file against
a warehouse, read-only, and writes the tables to `docs/insights/RESULTS.md`, which is where the
README's numbers come from. A query that needs the `forecast` schema is skipped, with a note, on a
warehouse where `make forecast` hasn't run (the SYNTHETIC fixture, for example).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd


@dataclass
class Result:
    name: str
    title: str
    sql: str
    table: pd.DataFrame | None = None
    skipped: str | None = None


def title_of(sql: str, fallback: str) -> str:
    """The first comment line is the query's title."""
    for line in sql.splitlines():
        if line.startswith("--"):
            return line.lstrip("- ").strip() or fallback
    return fallback


def run(db_path: Path, query_dir: Path) -> list[Result]:
    files = sorted(query_dir.glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"no .sql files in {query_dir}")
    con = duckdb.connect(str(db_path), read_only=True, config={"python_enable_replacements": False})
    results = []
    try:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            result = Result(path.stem, title_of(sql, path.stem), sql)
            try:
                result.table = con.execute(sql).df()
            except duckdb.CatalogException as err:
                if "forecast" not in str(err):
                    raise
                result.skipped = "needs the forecast tables: run `make forecast` first"
            results.append(result)
    finally:
        con.close()
    return results


def _missing(value: Any) -> bool:
    return value is None or value is pd.NaT or (isinstance(value, float) and math.isnan(value))


def decimals(values: list[Any]) -> int:
    """How many decimals a column needs (two at most): 6.0 and 5.9 both show one."""
    places = [
        len(f"{value:.2f}".rstrip("0").split(".")[1])
        for value in values
        if isinstance(value, float) and not _missing(value)
    ]
    return max(places, default=0)


def cell(value: Any, places: int = 0) -> str:
    """Thousands separators, the column's decimals, and nothing for a missing value."""
    if _missing(value):
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return f"{value:,.{places}f}"
    if isinstance(value, pd.Timestamp):
        return str(value.date())
    return str(value)


def markdown_table(table: pd.DataFrame) -> list[str]:
    """A one-row result reads better turned on its side: one line per measure."""
    columns = [
        [value.item() if hasattr(value, "item") else value for value in table[name]]
        for name in table.columns
    ]
    places = [decimals(values) for values in columns]
    if len(table) == 1:
        rows = zip(table.columns, columns, places, strict=True)
        return ["| Measure | Value |", "|---|---:|"] + [
            f"| {name} | {cell(values[0], digits)} |" for name, values, digits in rows
        ]
    numeric = [pd.api.types.is_numeric_dtype(table[name]) for name in table.columns]
    lines = [
        "| " + " | ".join(table.columns) + " |",
        "|" + "".join("---:|" if right else "---|" for right in numeric),
    ]
    for i in range(len(table)):
        cells = (cell(values[i], digits) for values, digits in zip(columns, places, strict=True))
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def to_markdown(results: list[Result], dataset: str, ran_at: datetime) -> str:
    label = "SYNTHETIC fixture" if dataset == "fixture" else "orders and reviews are synthetic"
    lines = [
        "# Business insight queries: results",
        "",
        f"Written by `zap insights --dataset {dataset}` ({label}) at "
        f"{ran_at.isoformat(timespec='seconds')}. Don't edit this by hand: change a query in this "
        "folder and run `make insights`. The README's business insights quote these tables.",
    ]
    for result in results:
        lines += ["", f"## {result.name}: {result.title}", ""]
        if result.skipped or result.table is None:
            lines.append(f"Not run: {result.skipped or 'no result'}.")
        elif result.table.empty:
            lines.append("No rows.")
        else:
            lines += markdown_table(result.table)
        lines += ["", f"Query: [{result.name}.sql]({result.name}.sql)"]
    return "\n".join(lines) + "\n"


def write(db_path: Path, query_dir: Path, out: Path, dataset: str) -> list[Result]:
    results = run(db_path, query_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    text = to_markdown(results, dataset, datetime.now(UTC))
    out.write_text(text, encoding="utf-8", newline="\n")
    return results
