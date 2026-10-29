import csv
import hashlib
from pathlib import Path

import duckdb
import pytest

from zap.fixtures import DIMENSIONS, HEADERS, FixtureResult, FixtureSpec, generate_fixture


def _digest(folder: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob("*"))}


def _read(path: Path) -> duckdb.DuckDBPyRelation:
    return duckdb.read_csv(str(path), header=True, all_varchar=True)


def _count(rel: duckdb.DuckDBPyRelation) -> int:
    # DuckDB 2.0 removed Relation.fetchone(); relations re-run on every fetch.
    return int(rel.count("*").fetchall()[0][0])


def test_same_seed_is_byte_identical(tmp_path: Path) -> None:
    spec = FixtureSpec(n_orders=300)
    generate_fixture(tmp_path / "a", spec)
    generate_fixture(tmp_path / "b", spec)
    assert _digest(tmp_path / "a") == _digest(tmp_path / "b")


def test_different_seed_differs(tmp_path: Path) -> None:
    generate_fixture(tmp_path / "a", FixtureSpec(n_orders=300, seed=1))
    generate_fixture(tmp_path / "b", FixtureSpec(n_orders=300, seed=2))
    assert _digest(tmp_path / "a")["orders.csv"] != _digest(tmp_path / "b")["orders.csv"]


def test_all_files_and_headers(fixture_data: tuple[Path, FixtureResult]) -> None:
    folder, result = fixture_data
    assert set(result.rows) == set(HEADERS)
    for name, header in HEADERS.items():
        with (folder / f"{name}.csv").open(encoding="utf-8", newline="") as fh:
            assert next(csv.reader(fh)) == header


@pytest.mark.parametrize("name", sorted(HEADERS))
def test_line_endings_match_real_files(fixture_data: tuple[Path, FixtureResult], name: str) -> None:
    raw = (fixture_data[0] / f"{name}.csv").read_bytes()
    first_line = raw.split(b"\n", 1)[0]
    assert first_line.endswith(b"\r") == (name in DIMENSIONS)


def test_row_counts_reported_correctly(fixture_data: tuple[Path, FixtureResult]) -> None:
    folder, result = fixture_data
    for name, n in result.rows.items():
        assert _count(_read(folder / f"{name}.csv")) == n


def test_embedded_newline_survives_csv_parsing(fixture_data: tuple[Path, FixtureResult]) -> None:
    rel = _read(fixture_data[0] / "restaurant.csv")
    assert _count(rel.filter("address LIKE '%\n%'")) == 1


def test_order_arithmetic_holds_except_injected(fixture_data: tuple[Path, FixtureResult]) -> None:
    folder, result = fixture_data
    con = duckdb.connect()
    bad = con.execute(f"""
        select count(*) from read_csv('{folder / "orders.csv"}', header=true)
        where sales_amount <> subtotal - discount + delivery_fee + gst
    """).fetchone()
    assert bad == (result.defects.amount_mismatch,)


def test_order_items_reconcile_with_orders(fixture_data: tuple[Path, FixtureResult]) -> None:
    folder, _ = fixture_data
    con = duckdb.connect()
    mismatches = con.execute(f"""
        with o as (select distinct * from read_csv('{folder / "orders.csv"}', header=true)),
             i as (select order_id, sum(line_amount) s, sum(quantity) q, count(*) n
                   from read_csv('{folder / "order_items.csv"}', header=true) group by 1)
        select count(*) from o join i using (order_id)
        where o.subtotal <> i.s or o.sales_qty <> i.q or o.items_count <> i.n
    """).fetchone()
    assert mismatches == (0,)


def test_injected_defect_counts(fixture_data: tuple[Path, FixtureResult]) -> None:
    folder, result = fixture_data
    d = result.defects
    con = duckdb.connect()
    orders = f"read_csv('{folder / 'orders.csv'}', header=true)"
    reviews = f"read_csv('{folder / 'reviews.csv'}', header=true)"
    rest = f"read_csv('{folder / 'restaurant.csv'}', header=true)"

    def scalar(sql: str) -> int:
        row = con.execute(sql).fetchone()
        assert row is not None
        return int(row[0])

    assert (
        scalar(f"select count(*) - count(distinct order_id) from {orders}") == d.duplicate_order_id
    )
    assert (
        scalar(f"select count(*) from {orders} o anti join {rest} r on o.r_id = r.id")
        == d.orphan_order_restaurant
    )
    joined = f"(select distinct * from {orders}) o join {reviews} v using (order_id)"
    assert (
        scalar(f"select count(*) from {joined} where v.review_date < o.order_date")
        == d.review_before_order
    )
    assert (
        scalar(f"select count(*) from {joined} where v.restaurant_id <> o.r_id")
        == d.review_restaurant_mismatch
    )
    assert (
        scalar(f"select count(*) from {joined} where o.order_status <> 'Delivered'")
        == d.review_on_undelivered
    )
    assert (
        scalar(f"""select count(*) from read_csv('{folder / "food.csv"}', header=true,
                      all_varchar=true) where veg_or_non_veg is null""")
        == d.null_veg_flag
    )
    assert all(v > 0 for v in d.as_dict().values())


def test_no_defects_mode_is_clean(tmp_path: Path) -> None:
    result = generate_fixture(tmp_path, FixtureSpec(n_orders=500, defects=False))
    assert not any(result.defects.as_dict().values())
    con = duckdb.connect()
    orders = f"read_csv('{tmp_path / 'orders.csv'}', header=true)"
    assert con.execute(f"select count(*) - count(distinct order_id) from {orders}").fetchone() == (
        0,
    )


def test_undelivered_orders_have_no_rating_or_delivery_time(
    fixture_data: tuple[Path, FixtureResult],
) -> None:
    rel = _read(fixture_data[0] / "orders.csv")
    leaked = rel.filter(
        "order_status <> 'Delivered' and "
        "(customer_rating is not null or delivery_time_min is not null)"
    )
    assert _count(leaked) == 0
