"""The two tests that need more orders than the 5% sample has, run on the full data in DuckDB.

The graph holds a sample, and a restaurant with 25 orders can't show a real difference: on the
sample, the cancel test misses even restaurants that cancel three times as often. The full
data has about 20 times more orders per restaurant, so "nothing found" there means something.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from zap.kg import stats

MIN_ORDERS_TO_TEST = 20
BUSY_RESTAURANT = 100  # pairs are tested only where a restaurant has this many orders
MIN_TIMES_TOGETHER = 3
PLANTED_SHARE = 0.01
PAIR_MULTIPLES = (2, 3, 5)
SEED = 11

CANCELS = """
    select {column} as id, count(*) as orders,
           sum(case when is_cancelled then 1 else 0 end) as cancelled
    from marts.fct_orders group by 1 having count(*) >= ? order by 1
"""
BUSY_LINES = """
    create temporary table busy_lines as
    with busy as (select restaurant_id from marts.fct_orders group by 1 having count(*) >= ?)
    select distinct i.order_id, i.food_id, o.restaurant_id
    from marts.fct_order_items i
    join marts.fct_orders o on o.order_id = i.order_id
    join busy b on b.restaurant_id = o.restaurant_id
"""
PAIRS = """
    with dishes as (select restaurant_id, food_id, count(*) as orders_dish
                    from busy_lines group by 1, 2),
    sizes as (select restaurant_id, count(distinct order_id) as orders_here
              from busy_lines group by 1),
    pairs as (select a.restaurant_id, a.food_id as food_a, b.food_id as food_b, count(*) as both
              from busy_lines a
              join busy_lines b on a.order_id = b.order_id and a.food_id < b.food_id
              group by 1, 2, 3 having count(*) >= ?)
    select p.both, da.orders_dish as orders_a, db.orders_dish as orders_b, s.orders_here
    from pairs p
    join dishes da on da.restaurant_id = p.restaurant_id and da.food_id = p.food_a
    join dishes db on db.restaurant_id = p.restaurant_id and db.food_id = p.food_b
    join sizes s on s.restaurant_id = p.restaurant_id
    order by p.restaurant_id, p.food_a, p.food_b
"""
POSSIBLE_PAIRS = """
    select count(distinct restaurant_id), sum(n * (n - 1) / 2) from (
      select restaurant_id, count(distinct food_id) as n from busy_lines group by 1)
"""


def cancel_check(con: duckdb.DuckDBPyConnection, rng: np.random.Generator) -> dict[str, Any]:
    rate = float(
        con.execute(
            "select avg(case when is_cancelled then 1.0 else 0 end) from marts.fct_orders"
        ).fetchall()[0][0]
    )
    result: dict[str, Any] = {"base_rate": round(rate, 4), "min_orders": MIN_ORDERS_TO_TEST}
    for entity, column in (("restaurants", "restaurant_id"), ("users", "customer_id")):
        counts = con.execute(CANCELS.format(column=column), [MIN_ORDERS_TO_TEST]).df()
        scored = stats.rate_outliers(counts, rate)
        result[entity] = {
            **stats.outlier_summary(scored),
            "planted_power_SYNTHETIC": stats.planted_power(scored["orders"].to_numpy(), rate, rng),
        }
    return result


def pair_q_values(pairs: pd.DataFrame, possible: int) -> np.ndarray:
    p = stats.pairs_within_p(
        pairs["both"].to_numpy(),
        pairs["orders_a"].to_numpy(),
        pairs["orders_b"].to_numpy(),
        pairs["orders_here"].to_numpy(),
    )
    return stats.benjamini_hochberg(p, possible)


def pair_power(
    pairs: pd.DataFrame, possible: int, rng: np.random.Generator
) -> dict[str, dict[str, float]]:
    """SYNTHETIC check: if 1% of pairs were truly bought together more often, would this see it?"""
    power: dict[str, dict[str, float]] = {}
    planted = rng.random(len(pairs)) < PLANTED_SHARE
    for multiple in PAIR_MULTIPLES:
        changed = pairs.copy()
        boosted = np.minimum(
            changed["both"].to_numpy() * multiple,
            np.minimum(changed["orders_a"].to_numpy(), changed["orders_b"].to_numpy()),
        )
        changed["both"] = np.where(planted, boosted, changed["both"].to_numpy())
        found = pair_q_values(changed, possible) < stats.ALPHA
        power[f"{multiple}x"] = {
            "recall": round(float(found[planted].mean()), 4) if planted.any() else 0.0,
            "false_discoveries": int((found & ~planted).sum()),
        }
    return power


def pair_check(con: duckdb.DuckDBPyConnection, rng: np.random.Generator) -> dict[str, Any]:
    con.execute(BUSY_LINES, [BUSY_RESTAURANT])
    restaurants, possible = con.execute(POSSIBLE_PAIRS).fetchall()[0]
    pairs = con.execute(PAIRS, [MIN_TIMES_TOGETHER]).df()
    if pairs.empty:
        return {"restaurants": int(restaurants or 0), "pairs_tested": 0, "significant_pairs": 0}
    possible = int(possible)
    return {
        "restaurants": int(restaurants),
        "possible_pairs": possible,
        "pairs_tested": len(pairs),
        "significant_pairs": int((pair_q_values(pairs, possible) < stats.ALPHA).sum()),
        "planted_power_SYNTHETIC": pair_power(pairs, possible, rng),
    }


def run_full_checks(db_path: Path, seed: int = SEED) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    con = duckdb.connect(str(db_path), read_only=True, config={"python_enable_replacements": False})
    try:
        return {
            "dataset": db_path.stem,
            "cancellations": cancel_check(con, rng),
            "dish_pairs_within_a_restaurant": pair_check(con, rng),
        }
    finally:
        con.close()
