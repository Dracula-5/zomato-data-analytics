"""Is there personal taste in the orders? Compare each statistic with random assignment.

Shuffling the restaurant column keeps how often each customer orders and how popular each
restaurant is, and destroys any link between the two. If a statistic on the real orders looks
like the shuffled ones, customers have no taste that this data can show.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

SHUFFLES = 5
SEED = 21


def load_orders(db_path: Path) -> pd.DataFrame:
    con = duckdb.connect(str(db_path), read_only=True, config={"python_enable_replacements": False})
    try:
        return con.execute(
            """select o.customer_id, o.restaurant_id, o.order_date, r.city,
                      coalesce(r.primary_cuisine, 'unknown') as cuisine
               from marts.fct_orders o
               join marts.dim_restaurant r on r.restaurant_id = o.restaurant_id
               order by o.order_id"""
        ).df()
    finally:
        con.close()


def repeat_share(users: np.ndarray, items: np.ndarray) -> float:
    """Share of orders that go back to a restaurant this customer has already used."""
    width = int(items.max()) + 1
    pairs = np.unique(users.astype(np.int64) * width + items.astype(np.int64))
    return float(1 - len(pairs) / len(users))


def top_group_share(users: np.ndarray, groups: np.ndarray) -> float:
    """Mean share of a customer's orders in their single most-used city or cuisine."""
    frame = pd.DataFrame({"user": users, "group": groups})
    counts = frame.groupby(["user", "group"], observed=True).size()
    per_user = counts.groupby(level="user")
    return float((per_user.max() / per_user.sum()).mean())


def gini(counts: np.ndarray) -> float:
    ordered = np.sort(counts.astype(float))
    size = len(ordered)
    ranks = np.arange(1, size + 1)
    return float((2 * ranks - size - 1).dot(ordered) / (size * ordered.sum()))


def top_share(counts: np.ndarray, share: float = 0.01) -> float:
    ordered = np.sort(counts)[::-1]
    return float(ordered[: max(1, int(len(ordered) * share))].sum() / ordered.sum())


def _statistics(frame: pd.DataFrame) -> dict[str, float]:
    users = frame["customer_id"].to_numpy()
    return {
        "repeat_share": repeat_share(users, frame["restaurant_id"].to_numpy()),
        "top_city_share": top_group_share(users, frame["city"].to_numpy()),
        "top_cuisine_share": top_group_share(users, frame["cuisine"].to_numpy()),
    }


def audit(orders: pd.DataFrame, shuffles: int = SHUFFLES, seed: int = SEED) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    real = _statistics(orders)
    columns = ["restaurant_id", "city", "cuisine"]
    nulls: dict[str, list[float]] = {name: [] for name in real}
    for _ in range(shuffles):
        order = rng.permutation(len(orders))
        shuffled = orders.copy()
        for column in columns:
            shuffled[column] = orders[column].to_numpy()[order]
        for name, value in _statistics(shuffled).items():
            nulls[name].append(value)
    per_restaurant = orders.groupby("restaurant_id").size().to_numpy()
    per_user = orders.groupby("customer_id").size().to_numpy()
    measures = {
        name: {
            "real": round(real[name], 4),
            "if_random": round(float(np.mean(nulls[name])), 4),
            "times_random": round(real[name] / max(float(np.mean(nulls[name])), 1e-12), 2),
        }
        for name in real
    }
    return {
        "orders": len(orders),
        "customers": int(orders["customer_id"].nunique()),
        "restaurants": int(orders["restaurant_id"].nunique()),
        "shuffles": shuffles,
        "measures": measures,
        "orders_per_customer_median": float(np.median(per_user)),
        "orders_per_restaurant_median": float(np.median(per_restaurant)),
        "restaurant_gini": round(gini(per_restaurant), 4),
        "top_1pct_restaurants_share": round(top_share(per_restaurant), 4),
        "top_1pct_customers_share": round(top_share(per_user), 4),
    }


def popularity_vs_listing(db_path: Path) -> float:
    """Do restaurants with more listed ratings get more orders here, as they would in reality?"""
    con = duckdb.connect(str(db_path), read_only=True, config={"python_enable_replacements": False})
    try:
        frame = con.execute(
            """select r.rating_count_floor, count(*) as orders
               from marts.fct_orders o
               join marts.dim_restaurant r on r.restaurant_id = o.restaurant_id
               where r.rating_count_floor is not null group by o.restaurant_id, 1"""
        ).df()
    finally:
        con.close()
    return round(float(spearmanr(frame["rating_count_floor"], frame["orders"]).statistic), 4)
