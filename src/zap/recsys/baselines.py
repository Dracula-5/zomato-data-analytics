"""Recommenders on a temporal split: train on the past, score on later orders.

A random split would leak the future into training. Each customer gets ten restaurants they
haven't used, and is scored on which of them they ordered from after the cutoff. Random
recommendations set the floor, so a score means something only when it clears that.

Beating random isn't enough, because random ignores popularity, and popular restaurants get
more orders from anyone. So the models also run on a shuffled copy, where each restaurant keeps
its popularity and any link to customers is gone: a score no better there isn't personalization.
They also run on a planted taste pattern. If matrix factorization finds that but nothing in the
real orders, the result reflects the data rather than a bug.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.linalg import svds

from zap.recsys.signal_audit import audit, load_orders, popularity_vs_listing

TOP_K = 10
CUTOFF = "2026-04-01"
MIN_TRAIN_ORDERS = 5
EVAL_USERS = 3000
FACTORS = 32
BATCH = 256
BOOTSTRAPS = 1000
CANDIDATE_POOL = 600  # long enough that filtering out seen restaurants still leaves ten
SEED = 33

TASTE_GROUPS = 20
TASTE_STRENGTH = 0.3
TASTE_FAVOURITES = 200


@dataclass
class Split:
    matrix: csr_matrix
    users: np.ndarray  # row of each evaluated customer in the matrix
    truth: list[set[int]]  # for each of them, the unseen restaurants ordered after the cutoff
    item_city: np.ndarray
    user_top_city: np.ndarray


def make_split(
    orders: pd.DataFrame, cutoff: str, rng: np.random.Generator, eval_users: int = EVAL_USERS
) -> Split:
    dates = pd.to_datetime(orders["order_date"])
    train = orders[dates < pd.Timestamp(cutoff)]
    test = orders[dates >= pd.Timestamp(cutoff)]
    user_ids, user_rows = np.unique(train["customer_id"].to_numpy(), return_inverse=True)
    item_ids, item_cols = np.unique(train["restaurant_id"].to_numpy(), return_inverse=True)
    matrix = csr_matrix(
        (np.ones(len(train)), (user_rows, item_cols)), shape=(len(user_ids), len(item_ids))
    )
    matrix.sum_duplicates()

    user_lookup = pd.Series(np.arange(len(user_ids)), index=user_ids)
    item_lookup = pd.Series(np.arange(len(item_ids)), index=item_ids)
    pairs = pd.DataFrame(
        {
            "user": test["customer_id"].map(user_lookup).to_numpy(),
            "item": test["restaurant_id"].map(item_lookup).to_numpy(),
        }
    ).dropna()
    pairs = pairs.astype({"user": int, "item": int}).drop_duplicates()
    trained_orders = np.asarray(matrix.sum(axis=1)).ravel()
    wanted = pairs[trained_orders[pairs["user"].to_numpy()] >= MIN_TRAIN_ORDERS]
    seen = matrix[wanted["user"].to_numpy(), wanted["item"].to_numpy()].A1 > 0
    truth_by_user = wanted[~seen].groupby("user")["item"].agg(set)
    chosen = rng.permutation(truth_by_user.index.to_numpy())[:eval_users]
    chosen.sort()

    _, city_codes = np.unique(train["city"].astype(str).to_numpy(), return_inverse=True)
    item_city = pd.Series(city_codes).groupby(item_cols).first().to_numpy()
    counts = pd.DataFrame({"user": user_rows, "city": city_codes}).value_counts().reset_index()
    top = counts.sort_values("count", ascending=False, kind="stable").drop_duplicates("user")
    user_top_city = np.zeros(len(user_ids), dtype=int)
    user_top_city[top["user"].to_numpy()] = top["city"].to_numpy()
    return Split(matrix, chosen, [truth_by_user[u] for u in chosen], item_city, user_top_city)


def _unseen(split: Split, row: int, ranked: np.ndarray, k: int = TOP_K) -> np.ndarray:
    seen = split.matrix[row].indices
    return ranked[~np.isin(ranked, seen)][:k]


def recommend_random(split: Split, rng: np.random.Generator) -> list[np.ndarray]:
    items = split.matrix.shape[1]
    pool = min(items, 200)
    return [_unseen(split, row, rng.choice(items, pool, replace=False)) for row in split.users]


def recommend_popular(split: Split) -> list[np.ndarray]:
    popularity = np.asarray(split.matrix.sum(axis=0)).ravel()
    ranked = np.argsort(-popularity, kind="stable")[:CANDIDATE_POOL]
    return [_unseen(split, row, ranked) for row in split.users]


def recommend_city_popular(split: Split) -> list[np.ndarray]:
    """The most popular restaurants in the city each customer has used most."""
    popularity = np.asarray(split.matrix.sum(axis=0)).ravel()
    order = np.argsort(-popularity, kind="stable")
    by_city: dict[int, np.ndarray] = {}
    for city in np.unique(split.item_city):
        by_city[int(city)] = order[split.item_city[order] == city][:CANDIDATE_POOL]
    return [_unseen(split, r, by_city[int(split.user_top_city[r])]) for r in split.users]


def recommend_svd(split: Split, factors: int = FACTORS) -> list[np.ndarray]:
    """Truncated SVD of log-scaled counts: the classic latent-factor recommender."""
    logged = split.matrix.copy()
    logged.data = np.log1p(logged.data)
    limit = min(factors, min(logged.shape) - 1)
    left, sizes, right = svds(logged.astype(float), k=limit, random_state=SEED)
    user_factors = left * sizes
    results: list[np.ndarray] = []
    for start in range(0, len(split.users), BATCH):
        rows = split.users[start : start + BATCH]
        scores = user_factors[rows] @ right
        pool = min(CANDIDATE_POOL, scores.shape[1])
        top = np.argpartition(-scores, pool - 1, axis=1)[:, :pool]
        for row, line, candidates in zip(rows, scores, top, strict=True):
            ranked = candidates[np.argsort(-line[candidates], kind="stable")]
            results.append(_unseen(split, int(row), ranked))
    return results


def recall_at(recommended: np.ndarray, truth: set[int], k: int = TOP_K) -> float:
    return len(set(recommended[:k].tolist()) & truth) / len(truth)


def ndcg_at(recommended: np.ndarray, truth: set[int], k: int = TOP_K) -> float:
    gains = [1 / np.log2(rank + 2) for rank, i in enumerate(recommended[:k]) if int(i) in truth]
    ideal = sum(1 / np.log2(rank + 2) for rank in range(min(len(truth), k)))
    return float(sum(gains) / ideal)


def score(recommended: list[np.ndarray], truth: list[set[int]], seed: int = SEED) -> dict[str, Any]:
    recalls = np.array([recall_at(r, t) for r, t in zip(recommended, truth, strict=True)])
    ndcgs = np.array([ndcg_at(r, t) for r, t in zip(recommended, truth, strict=True)])
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(recalls), size=(BOOTSTRAPS, len(recalls)))
    low, high = np.percentile(recalls[draws].mean(axis=1), [2.5, 97.5])
    return {
        "recall_at_10": round(float(recalls.mean()), 6),
        "recall_ci95": [round(float(low), 6), round(float(high), 6)],
        "ndcg_at_10": round(float(ndcgs.mean()), 6),
        "users_with_a_hit": int((recalls > 0).sum()),
    }


def evaluate_split(split: Split, rng: np.random.Generator) -> dict[str, Any]:
    runs = {
        "random": recommend_random(split, rng),
        "popularity": recommend_popular(split),
        "city_popularity": recommend_city_popular(split),
        "svd": recommend_svd(split),
    }
    results = {name: score(recs, split.truth) for name, recs in runs.items()}
    floor = max(results["random"]["recall_at_10"], 1e-12)
    for result in results.values():
        result["times_random"] = round(result["recall_at_10"] / floor, 2)
    return {
        "users": len(split.users),
        "restaurants": int(split.matrix.shape[1]),
        "density": float(split.matrix.nnz / (split.matrix.shape[0] * split.matrix.shape[1])),
        "models": results,
    }


def plant_taste(
    orders: pd.DataFrame, rng: np.random.Generator, strength: float = TASTE_STRENGTH
) -> pd.DataFrame:
    """SYNTHETIC: give each customer a taste group, and move 30% of orders into that group."""
    users = orders["customer_id"].to_numpy()
    items = orders["restaurant_id"].to_numpy().copy()
    groups_of_user = pd.Series(
        rng.integers(0, TASTE_GROUPS, size=len(np.unique(users))), index=np.unique(users)
    )
    catalogue = np.unique(items)
    groups = np.array_split(rng.permutation(catalogue), TASTE_GROUPS)
    # A short favourite list per group, so the taste can be learned from a few orders each.
    size = min(TASTE_FAVOURITES, min(len(group) for group in groups))
    favourites = np.stack([group[:size] for group in groups])
    changed = rng.random(len(items)) < strength
    wanted = groups_of_user.reindex(users[changed]).to_numpy()
    items[changed] = favourites[wanted, rng.integers(0, size, len(wanted))]
    return orders.assign(restaurant_id=items)


def shuffle_within_days(orders: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    """Reassign restaurants among each day's orders. Customers keep their activity, and each
    restaurant keeps its popularity on every date, but any link between the two is gone."""
    days = pd.to_datetime(orders["order_date"]).to_numpy()
    items = orders["restaurant_id"].to_numpy()
    by_day = np.argsort(days, kind="stable")
    shuffled = np.lexsort((rng.random(len(items)), days))
    moved = items.copy()
    moved[by_day] = items[shuffled]
    return orders.assign(restaurant_id=moved)


def run_recsys(db_path: Path) -> dict[str, Any]:
    orders = load_orders(db_path)
    report: dict[str, Any] = {
        "dataset": db_path.stem,
        "cutoff": CUTOFF,
        "top_k": TOP_K,
        "factors": FACTORS,
        "signal_audit": audit(orders),
        "listed_ratings_vs_orders_spearman": popularity_vs_listing(db_path),
    }
    rng = np.random.default_rng(SEED)
    report["real_orders"] = evaluate_split(make_split(orders, CUTOFF, rng), rng)
    shuffled = shuffle_within_days(orders, rng)
    report["shuffled_control"] = evaluate_split(make_split(shuffled, CUTOFF, rng), rng)
    planted = plant_taste(orders, rng)
    report["planted_taste_SYNTHETIC"] = evaluate_split(make_split(planted, CUTOFF, rng), rng)
    return report
