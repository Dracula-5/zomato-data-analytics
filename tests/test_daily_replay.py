"""The Airflow DAG's daily path, without Airflow: bootstrap the history, then replay single
days the way the DAG tasks do (ingest --date -> load --date -> dbt build). Proves the two
claims the DAG relies on: re-running a day changes nothing, and day-by-day incremental runs
end in the same warehouse as one full rebuild. Runs on the SYNTHETIC fixture, with one
review moved to before its order and across the cutoff (a defect the real data has)."""

from datetime import date
from pathlib import Path

import duckdb

from helpers import make_settings, rewrite_csv
from zap.config import Settings
from zap.dbt_runner import run_dbt
from zap.ingest.contracts import Contract
from zap.ingest.lake import ingest
from zap.warehouse.duckdb_loader import load_raw
from zap.warehouse.fingerprint import diff, fingerprint

FACTS = ["orders", "order_items", "reviews"]
CUTOFF = date(2026, 6, 28)
DAYS = (date(2026, 6, 29), date(2026, 6, 30))


def _dbt(args: list[str], settings: Settings) -> None:
    run = run_dbt(args, settings.duckdb_path("replay"), settings.dbt_project_dir, capture=True)
    assert run.ok, f"dbt {' '.join(args)} failed"


def _replay(day: date, contracts: dict[str, Contract], source: Path, settings: Settings) -> None:
    lake_dir = settings.lake_dir / "replay"
    ingest(contracts, source, lake_dir, tables=FACTS, dataset="replay", day=day, skip_drift=True)
    load_raw(contracts, lake_dir, settings.duckdb_path("replay"), FACTS, partition_date=day)
    _dbt(["build"], settings)


def _review_before_its_order(source: Path) -> int:
    """Point the first review at a delivered order of the LAST day, dated before the cutoff.

    Bootstrap loads the review but must drop it from fct_reviews (no order yet); the run for
    the order's day must then pick it up, although its own date is long past."""
    orders = (source / "orders.csv").as_posix()
    row = (
        duckdb.connect()
        .execute(
            f"select order_id from read_csv('{orders}') where order_date = '{DAYS[-1]}' "
            "and order_status = 'Delivered' order by order_id limit 1"
        )
        .fetchone()
    )
    assert row is not None
    moved: list[int] = []

    def edit(rows: list[list[str]]) -> list[list[str]]:
        header, first = rows[0], rows[1]
        first[header.index("order_id")] = str(row[0])
        first[header.index("review_date")] = "2026-06-20"
        moved.append(int(first[header.index("review_id")]))
        return rows

    rewrite_csv(source / "reviews.csv", edit)
    return moved[0]


def test_daily_replay_is_idempotent_and_equals_a_full_rebuild(
    contracts: dict[str, Contract], source_copy: Path, tmp_path: Path
) -> None:
    source = source_copy
    early_review = _review_before_its_order(source)
    settings = make_settings(tmp_path)
    db = settings.duckdb_path("replay")
    if not (settings.dbt_project_dir / "dbt_packages").exists():
        _dbt(["deps"], settings)

    # make airflow-bootstrap: history up to the cutoff, one full build.
    ingest(contracts, source, settings.lake_dir / "replay", dataset="replay", until=CUTOFF)
    load_raw(contracts, settings.lake_dir / "replay", db)
    _dbt(["build", "--full-refresh"], settings)
    bootstrap = fingerprint(db)

    for day in DAYS:  # the scheduled runs
        _replay(day, contracts, source, settings)
    after_days = fingerprint(db)
    assert after_days["marts.fct_orders"]["rows"] > bootstrap["marts.fct_orders"]["rows"]
    con = duckdb.connect(str(db), read_only=True)
    found = con.execute(f"select count(*) from marts.fct_reviews where review_id = {early_review}")
    assert found.fetchall() == [(1,)]  # arrived with its order, not on its own (past) date
    con.close()

    _replay(DAYS[-1], contracts, source, settings)  # a retry or manual re-run of a day
    assert diff(after_days, fingerprint(db)) == []

    _dbt(["build", "--full-refresh"], settings)  # rebuild everything from the same raw data
    assert diff(after_days, fingerprint(db)) == []
