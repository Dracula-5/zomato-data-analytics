"""Shared test helpers (importable because pytest puts `tests/` on sys.path)."""

import csv
from collections.abc import Callable
from pathlib import Path

import duckdb

from zap.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = REPO_ROOT / "contracts"

Rows = list[list[str]]


def rewrite_csv(path: Path, edit: Callable[[Rows], Rows]) -> None:
    """Apply `edit` to all rows (header included) of a CSV, keeping its line endings."""
    crlf = path.read_bytes().split(b"\n", 1)[0].endswith(b"\r")
    with path.open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    with path.open("w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, lineterminator="\r\n" if crlf else "\n").writerows(edit(rows))


def drop_column(name: str) -> Callable[[Rows], Rows]:
    def edit(rows: Rows) -> Rows:
        i = rows[0].index(name)
        return [r[:i] + r[i + 1 :] for r in rows]

    return edit


def scalar(sql: str) -> object:
    row = duckdb.connect().execute(sql).fetchone()
    assert row is not None
    return row[0]


def parquet(path: Path) -> str:
    """`read_parquet(...)` SQL for a lake table folder (dimension or partitioned fact)."""
    pattern = "*/*.parquet" if any(p.is_dir() for p in path.iterdir()) else "*.parquet"
    return f"read_parquet('{(path / pattern).as_posix()}', hive_partitioning=false)"


def make_settings(root: Path) -> Settings:
    """Settings whose lake, warehouse and artifacts live under `root` (a temp folder)."""
    return Settings(
        lake_dir=root / "lake",
        warehouse_dir=root / "warehouse",
        artifacts_dir=root / "artifacts",
        contracts_dir=CONTRACTS_DIR,
    )
