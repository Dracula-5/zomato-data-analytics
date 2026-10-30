import json
from datetime import date
from pathlib import Path

import duckdb
import pytest

from helpers import drop_column, parquet, rewrite_csv, scalar
from zap.fixtures import FixtureResult
from zap.ingest import lake, sample
from zap.ingest.contracts import Contract
from zap.ingest.lake import DriftError, ingest, write_manifest

LINEAGE = ["_batch_id", "_source_file", "_loaded_at"]


@pytest.fixture
def full_lake(
    contracts: dict[str, Contract], fixture_data: tuple[Path, FixtureResult], tmp_path: Path
) -> tuple[Path, lake.IngestManifest]:
    out = tmp_path / "lake"
    return out, ingest(contracts, fixture_data[0], out, pct=100, dataset="fixture")


def test_every_table_reconciles(full_lake: tuple[Path, lake.IngestManifest]) -> None:
    _, manifest = full_lake
    assert [t.status for t in manifest.tables] == ["written"] * 7
    for t in manifest.tables:
        assert t.rows_written == t.rows_source, t.table
        assert t.bytes > 0 and t.files > 0
    assert manifest.rows_written == sum(t.rows_written for t in manifest.tables)
    assert manifest.dataset == "fixture"


def test_layout_dimensions_single_file_facts_daily_partitions(
    full_lake: tuple[Path, lake.IngestManifest], fixture_data: tuple[Path, FixtureResult]
) -> None:
    out, manifest = full_lake
    assert (out / "raw" / "food" / "food.parquet").is_file()
    parts = sorted(p.name for p in (out / "raw" / "orders").iterdir())
    assert len(parts) == 60 and parts[0].startswith("order_date=")
    by_table = {t.table: t for t in manifest.tables}
    assert by_table["orders"].partitions == 60
    assert by_table["food"].partitions == 0


def test_columns_are_contract_plus_lineage_without_index(
    full_lake: tuple[Path, lake.IngestManifest], contracts: dict[str, Contract]
) -> None:
    import duckdb

    out, _ = full_lake
    for table, contract in contracts.items():
        cols = duckdb.connect().execute(f"select * from {parquet(out / 'raw' / table)} limit 0")
        names = [d[0] for d in cols.description]
        extra = [contract.partition_by] if contract.partition_lookup else []
        assert names == contract.column_names + LINEAGE + extra, table


def test_fact_types_are_enforced_and_partition_column_kept(
    full_lake: tuple[Path, lake.IngestManifest],
) -> None:
    out, _ = full_lake
    orders = parquet(out / "raw" / "orders")
    assert scalar(f"select typeof(order_id) from {orders} limit 1") == "BIGINT"
    assert scalar(f"select typeof(order_date) from {orders} limit 1") == "DATE"
    assert scalar(f"select typeof(price) from {parquet(out / 'raw' / 'order_items')} limit 1") == (
        "DECIMAL(12,2)"
    )
    assert scalar(f"select count(*) from {orders} where order_date is null") == 0


def test_lineage_columns_filled(full_lake: tuple[Path, lake.IngestManifest]) -> None:
    out, manifest = full_lake
    rel = parquet(out / "raw" / "reviews")
    assert scalar(f"select count(distinct _batch_id) from {rel}") == 1
    assert scalar(f"select min(_batch_id) from {rel}") == manifest.run_id
    assert scalar(f"select min(_source_file) from {rel}") == "reviews.csv"
    assert scalar(f"select count(*) from {rel} where _loaded_at is null") == 0


def test_rerun_is_idempotent(
    contracts: dict[str, Contract],
    fixture_data: tuple[Path, FixtureResult],
    full_lake: tuple[Path, lake.IngestManifest],
) -> None:
    out, first = full_lake
    second = ingest(contracts, fixture_data[0], out, pct=100)
    assert [t.rows_written for t in second.tables] == [t.rows_written for t in first.tables]
    orders = parquet(out / "raw" / "orders")
    assert scalar(f"select count(*) from {orders}") == fixture_data[1].rows["orders"]
    assert scalar(f"select min(_batch_id) from {orders}") == second.run_id
    assert not [p for p in (out / "raw").iterdir() if p.name.startswith(".tmp")]


def test_sample_keeps_whole_users_and_their_children(
    contracts: dict[str, Contract], fixture_data: tuple[Path, FixtureResult], tmp_path: Path
) -> None:
    out = tmp_path / "lake5"
    manifest = ingest(contracts, fixture_data[0], out, pct=20)
    assert manifest.dataset == "sample_20pct"
    raw = out / "raw"
    orders, items, reviews = (parquet(raw / t) for t in ("orders", "order_items", "reviews"))
    limit = sample.threshold(20)
    outside = f"{sample.bucket_sql('user_id')} >= {limit}"
    assert scalar(f"select count(*) from {orders} where {outside}") == 0
    sampled_users = scalar(f"select count(distinct user_id) from {orders}")
    assert isinstance(sampled_users, int) and 0 < sampled_users < 300
    for child in (items, reviews):
        orphans = f"select count(*) from {child} c anti join {orders} o using (order_id)"
        assert scalar(orphans) == 0
        assert scalar(f"select count(*) from {child}") > 0
    by_table = {t.table: t for t in manifest.tables}
    assert by_table["food"].rows_written == fixture_data[1].rows["food"]  # dimensions stay whole
    assert by_table["orders"].rows_written < fixture_data[1].rows["orders"]


def test_order_item_without_parent_is_kept_with_null_partition(
    contracts: dict[str, Contract], source_copy: Path, tmp_path: Path
) -> None:
    orphan = ["999999", "777777", "1", "fd1", "10", "1", "10"]  # order 777777 doesn't exist
    rewrite_csv(source_copy / "order_items.csv", lambda rows: [*rows, orphan])
    out = tmp_path / "lake"
    ingest(contracts, source_copy, out, pct=100)
    items = parquet(out / "raw" / "order_items")
    kept = f"select count(*) from {items} where order_id = 777777 and order_date is null"
    assert scalar(kept) == 1


def test_breaking_drift_writes_nothing(
    contracts: dict[str, Contract], source_copy: Path, tmp_path: Path
) -> None:
    rewrite_csv(source_copy / "reviews.csv", drop_column("comment"))
    out = tmp_path / "lake"
    log = tmp_path / "drift.jsonl"
    with pytest.raises(DriftError, match="reviews: missing columns") as err:
        ingest(contracts, source_copy, out, pct=100, drift_log=log)
    assert [r.table for r in err.value.reports] == ["reviews"]
    assert not out.exists()
    assert len(log.read_text(encoding="utf-8").splitlines()) == 7  # every check still logged


def test_missing_files_are_skipped_not_fatal(
    contracts: dict[str, Contract], source_copy: Path, tmp_path: Path
) -> None:
    (source_copy / "users.csv").unlink()
    (source_copy / "orders.csv").unlink()
    manifest = ingest(contracts, source_copy, tmp_path / "lake", pct=100)
    status = {t.table: t.status for t in manifest.tables}
    assert status["users"] == "skipped_missing_file"
    assert status["orders"] == "skipped_missing_file"
    assert status["order_items"] == "skipped_missing_dependency"  # needs orders for its date
    assert status["reviews"] == "written"  # full load needs no parent
    assert status["food"] == "written"


def test_table_filter(
    contracts: dict[str, Contract], fixture_data: tuple[Path, FixtureResult], tmp_path: Path
) -> None:
    manifest = ingest(contracts, fixture_data[0], tmp_path / "lake", tables=["food"])
    assert [t.table for t in manifest.tables] == ["food"]


def test_row_count_mismatch_aborts_and_cleans_up(
    contracts: dict[str, Contract],
    fixture_data: tuple[Path, FixtureResult],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(lake, "fetch_one", lambda con, sql: (0,))
    out = tmp_path / "lake"
    with pytest.raises(RuntimeError, match="wrote 0 rows"):
        ingest(contracts, fixture_data[0], out, tables=["food"])
    assert not list((out / "raw").iterdir())


def test_manifest_written_and_history_appended(
    full_lake: tuple[Path, lake.IngestManifest], tmp_path: Path
) -> None:
    _, manifest = full_lake
    artifacts = tmp_path / "artifacts"
    path = write_manifest(manifest, artifacts)
    write_manifest(manifest, artifacts)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["rows_written"] == manifest.rows_written
    assert data["dataset"] == "fixture"
    assert len(data["drift"]) == 7
    history = (path.parent / "history.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(history) == 2


def test_until_writes_only_history_up_to_the_cutoff(
    contracts: dict[str, Contract], fixture_data: tuple[Path, FixtureResult], tmp_path: Path
) -> None:
    out = tmp_path / "lake"
    manifest = ingest(contracts, fixture_data[0], out, pct=100, until=date(2026, 6, 20))
    assert manifest.date_filter == "<= 2026-06-20"
    for table, col in (
        ("orders", "order_date"),
        ("order_items", "order_date"),
        ("reviews", "review_date"),
    ):
        rel = parquet(out / "raw" / table)
        assert str(scalar(f"select max({col}) from {rel}")) <= "2026-06-20", table
        assert scalar(f"select count(*) from {rel} where {col} is null") == 0
    items, orders = parquet(out / "raw" / "order_items"), parquet(out / "raw" / "orders")
    assert scalar(f"select count(*) from {items} anti join {orders} using (order_id)") == 0


def test_day_mode_replaces_only_that_partition(
    contracts: dict[str, Contract], fixture_data: tuple[Path, FixtureResult], tmp_path: Path
) -> None:
    out = tmp_path / "lake"
    ingest(contracts, fixture_data[0], out, pct=100, until=date(2026, 6, 20))
    day = date(2026, 6, 21)
    first = ingest(contracts, fixture_data[0], out, pct=100, day=day, skip_drift=True)
    again = ingest(contracts, fixture_data[0], out, pct=100, day=day, skip_drift=True)
    status = {t.table: t.status for t in again.tables}
    assert status["food"] == "skipped_dimension" and status["orders"] == "written"
    assert [t.rows_written for t in first.tables] == [t.rows_written for t in again.tables]
    orders = parquet(out / "raw" / "orders")
    assert str(scalar(f"select max(order_date) from {orders}")) == "2026-06-21"
    expected = scalar(
        f"select count(*) from read_csv('{(fixture_data[0] / 'orders.csv').as_posix()}') "
        "where order_date <= '2026-06-21'"
    )
    assert scalar(f"select count(*) from {orders}") == expected  # nothing duplicated


def test_date_modes_validate_arguments(
    contracts: dict[str, Contract], fixture_data: tuple[Path, FixtureResult], tmp_path: Path
) -> None:
    with pytest.raises(ValueError, match="either"):
        ingest(contracts, fixture_data[0], tmp_path, day=date(2026, 6, 1), until=date(2026, 6, 2))
    with pytest.raises(ValueError, match="skip_drift"):
        ingest(contracts, fixture_data[0], tmp_path, skip_drift=True)


def test_sampled_date_split_equals_one_full_ingest(
    contracts: dict[str, Contract], source_copy: Path, tmp_path: Path
) -> None:
    """Bootstrap + one run per day must load exactly what one ingest loads, for a SAMPLE too.

    Regression: a review dated *before* its order (a defect in the real data) whose order
    lands after the cutoff was lost, because sample membership was read from the
    date-limited lake, where that order did not exist yet.
    """
    pct, cutoff = 50.0, date(2026, 6, 20)
    orders_csv = (source_copy / "orders.csv").as_posix()
    order_id = next(
        oid
        for oid, uid in duckdb.connect()
        .execute(
            f"select order_id, user_id from read_csv('{orders_csv}') "
            "where order_date = '2026-06-25' order by order_id"
        )
        .fetchall()
        if sample.in_sample(uid, pct)
    )

    def early_review(rows: list[list[str]]) -> list[list[str]]:
        header, first = rows[0], rows[1]
        first[header.index("order_id")] = str(order_id)
        first[header.index("review_date")] = "2026-06-18"  # before the cutoff and the order
        moved.append(int(first[header.index("review_id")]))
        return rows

    moved: list[int] = []
    rewrite_csv(source_copy / "reviews.csv", early_review)
    facts = ["orders", "order_items", "reviews"]
    split, whole = tmp_path / "split", tmp_path / "whole"
    reviews_csv = (source_copy / "reviews.csv").as_posix()
    last = scalar(f"select max(review_date) from read_csv('{reviews_csv}')")  # after the orders
    assert isinstance(last, date)
    ingest(contracts, source_copy, split, pct=pct, until=cutoff)
    for ordinal in range(cutoff.toordinal() + 1, last.toordinal() + 1):  # one run per day
        day = date.fromordinal(ordinal)
        ingest(contracts, source_copy, split, pct=pct, tables=facts, day=day, skip_drift=True)
    ingest(contracts, source_copy, whole, pct=pct)

    reviews = parquet(split / "raw" / "reviews")
    assert scalar(f"select count(*) from {reviews} where review_id = {moved[0]}") == 1
    for table in facts:
        key = contracts[table].primary_key[0]
        ids = f"select {key} from {{}} order by all"
        a = duckdb.connect().execute(ids.format(parquet(split / "raw" / table))).fetchall()
        b = duckdb.connect().execute(ids.format(parquet(whole / "raw" / table))).fetchall()
        assert a == b, table
