"""The recommender audit on SYNTHETIC orders with known answers."""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

from zap.cli import app
from zap.config import get_settings
from zap.recsys import baselines as rec
from zap.recsys import signal_audit as sig


def random_orders(
    users: int = 300, items: int = 400, n: int = 12000, seed: int = 1
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = pd.Timestamp("2026-01-01") + pd.to_timedelta(rng.integers(0, 180, n), unit="D")
    restaurant = rng.integers(0, items, n)
    return pd.DataFrame(
        {
            "customer_id": rng.integers(0, users, n),
            "restaurant_id": restaurant,
            "order_date": days,
            "city": [f"City{r % 5}" for r in restaurant],
            "cuisine": [f"Cuisine{r % 7}" for r in restaurant],
        }
    )


def tasteful_orders(users: int = 300, items: int = 400, n: int = 12000) -> pd.DataFrame:
    """Each customer buys mostly from one of 8 groups, in a stable way over time."""
    orders = random_orders(users, items, n)
    rng = np.random.default_rng(2)
    keep = rng.random(n) < 0.8
    taste = orders["customer_id"].to_numpy() % 8
    moved = taste * (items // 8) + rng.integers(0, items // 8, n)
    orders["restaurant_id"] = np.where(keep, moved, orders["restaurant_id"].to_numpy())
    return orders


def split_from(orders: pd.DataFrame, cutoff: str = "2026-05-01", seed: int = 3) -> rec.Split:
    return rec.make_split(orders, cutoff, np.random.default_rng(seed), eval_users=250)


def test_repeat_share_on_known_values() -> None:
    users = np.array([1, 1, 1, 2])
    items = np.array([5, 5, 6, 5])
    assert sig.repeat_share(users, items) == 0.25


def test_top_group_share_on_known_values() -> None:
    users = np.array([1, 1, 1, 1, 2, 2])
    groups = np.array(["a", "a", "a", "b", "c", "c"])
    assert sig.top_group_share(users, groups) == pytest.approx((0.75 + 1.0) / 2)


def test_gini_is_zero_when_equal_and_high_when_concentrated() -> None:
    assert sig.gini(np.array([5, 5, 5, 5])) == pytest.approx(0.0)
    assert sig.gini(np.array([0, 0, 0, 100])) == pytest.approx(0.75)


def test_top_share() -> None:
    assert sig.top_share(np.array([90] + [1] * 99 + [0] * 0)) == pytest.approx(90 / 189)


def test_audit_sees_no_taste_in_random_orders() -> None:
    report = sig.audit(random_orders())
    measures = report["measures"]
    assert measures["repeat_share"]["times_random"] == pytest.approx(1.0, abs=0.15)
    assert measures["top_city_share"]["times_random"] == pytest.approx(1.0, abs=0.1)


def test_audit_finds_taste_when_it_exists() -> None:
    measures = sig.audit(tasteful_orders())["measures"]
    assert measures["repeat_share"]["real"] > measures["repeat_share"]["if_random"] * 1.5


def test_a_shuffle_keeps_customers_and_restaurant_popularity() -> None:
    orders = random_orders()
    report = sig.audit(orders, shuffles=1)
    assert report["orders"] == len(orders) and report["shuffles"] == 1


def test_recall_and_ndcg_on_known_values() -> None:
    recommended = np.array([9, 3, 8])
    assert rec.recall_at(recommended, {3, 4}) == 0.5
    assert rec.ndcg_at(recommended, {9}) == 1.0
    second_place = (1 / np.log2(3)) / 1.0
    assert rec.ndcg_at(recommended, {3}) == pytest.approx(second_place)
    assert rec.ndcg_at(recommended, {1}) == 0.0


def test_the_split_never_trains_on_the_future() -> None:
    orders = random_orders()
    split = split_from(orders)
    trained = int(split.matrix.sum())
    expected = int((pd.to_datetime(orders["order_date"]) < "2026-05-01").sum())
    assert trained == expected


def test_nothing_already_used_counts_as_a_hit() -> None:
    split = split_from(random_orders())
    for row, truth in zip(split.users, split.truth, strict=True):
        assert not truth & set(split.matrix[row].indices.tolist())


def test_recommendations_are_unseen_and_ten_long() -> None:
    split = split_from(random_orders())
    for model in (rec.recommend_popular(split), rec.recommend_svd(split, 8)):
        for row, items in zip(split.users, model, strict=True):
            assert len(items) == rec.TOP_K
            assert not set(items.tolist()) & set(split.matrix[row].indices.tolist())


def test_city_popularity_stays_in_the_customers_city() -> None:
    split = split_from(random_orders())
    for row, items in zip(split.users, rec.recommend_city_popular(split), strict=True):
        assert set(split.item_city[items].tolist()) == {split.user_top_city[row]}


def test_svd_learns_a_taste_that_is_really_there() -> None:
    split = split_from(tasteful_orders())
    scores = rec.evaluate_split(split, np.random.default_rng(4))["models"]
    assert scores["svd"]["recall_at_10"] > 3 * scores["random"]["recall_at_10"]
    assert scores["svd"]["recall_at_10"] > scores["popularity"]["recall_at_10"]


def test_svd_finds_nothing_when_orders_are_random() -> None:
    split = split_from(random_orders())
    scores = rec.evaluate_split(split, np.random.default_rng(5))["models"]
    assert scores["svd"]["recall_at_10"] < 2 * scores["random"]["recall_at_10"]


def test_scores_include_a_confidence_interval() -> None:
    result = rec.score([np.array([1]), np.array([2])], [{1}, {3}])
    assert result["recall_at_10"] == 0.5 and result["users_with_a_hit"] == 1
    assert result["recall_ci95"][0] <= 0.5 <= result["recall_ci95"][1]


def test_the_shuffled_control_keeps_popularity_and_activity_on_every_day() -> None:
    orders = random_orders()
    shuffled = rec.shuffle_within_days(orders, np.random.default_rng(7))
    assert not (shuffled["restaurant_id"].to_numpy() == orders["restaurant_id"].to_numpy()).all()
    for frame in (orders, shuffled):
        frame["day"] = pd.to_datetime(frame["order_date"])
    keys = ["day", "restaurant_id"]
    left = orders.groupby(keys).size().sort_index()
    right = shuffled.groupby(keys).size().sort_index()
    pd.testing.assert_series_equal(left, right)
    assert orders.groupby("customer_id").size().equals(shuffled.groupby("customer_id").size())


def test_the_shuffled_control_erases_taste() -> None:
    taste = tasteful_orders()
    real = rec.evaluate_split(split_from(taste), np.random.default_rng(8))["models"]
    shuffled_orders = rec.shuffle_within_days(taste, np.random.default_rng(9))
    control = rec.evaluate_split(split_from(shuffled_orders), np.random.default_rng(10))["models"]
    assert real["svd"]["recall_at_10"] > 2 * control["svd"]["recall_at_10"]


def test_planting_taste_moves_about_the_right_share_of_orders() -> None:
    orders = random_orders()
    planted = rec.plant_taste(orders, np.random.default_rng(6), strength=0.3)
    changed = (planted["restaurant_id"].to_numpy() != orders["restaurant_id"].to_numpy()).mean()
    assert changed == pytest.approx(0.3, abs=0.05)
    assert len(planted) == len(orders)


def warehouse_with_orders(path: Path) -> None:
    orders = random_orders(users=150, items=150, n=4000)
    restaurants = pd.DataFrame(
        {
            "restaurant_id": range(150),
            "city": [f"City{i % 5}" for i in range(150)],
            "primary_cuisine": [f"Cuisine{i % 7}" for i in range(150)],
            "rating_count_floor": [i * 3 for i in range(150)],
        }
    )
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA marts")
    frame = orders.assign(order_id=range(len(orders)))[
        ["order_id", "customer_id", "restaurant_id", "order_date"]
    ]
    for name, table in (("fct_orders", frame), ("dim_restaurant", restaurants)):
        con.register("frame_view", table)
        con.execute(f"CREATE TABLE marts.{name} AS SELECT * FROM frame_view")
        con.unregister("frame_view")
    con.close()


def test_the_command_reports_real_and_planted_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warehouse_with_orders(tmp_path / "w.duckdb")
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    monkeypatch.setattr(rec, "CUTOFF", "2026-05-01")
    monkeypatch.setattr(rec, "CANDIDATE_POOL", 100)
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["recsys", "--dataset", "w"])
    get_settings.cache_clear()
    assert result.exit_code == 0, result.output
    assert "svd" in result.output and (tmp_path / "recsys_eval.json").exists()


def test_the_command_needs_a_warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["recsys", "--dataset", "nope"])
    get_settings.cache_clear()
    assert result.exit_code == 1 and "no warehouse" in result.output
