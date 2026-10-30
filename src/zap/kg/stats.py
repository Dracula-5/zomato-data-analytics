"""Telling real patterns from noise.

Rank thousands of restaurants by cancel rate and the top of the list always looks alarming,
even when every restaurant is identical. So each one gets a p-value: how likely is a rate this
high by chance? Testing thousands of them produces many small p-values by luck alone, so
Benjamini-Hochberg turns them into q-values that control the false discovery rate.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import binom, hypergeom

ALPHA = 0.05
MULTIPLES = (2.0, 3.0, 5.0)


def benjamini_hochberg(p_values: np.ndarray, total_tests: int | None = None) -> np.ndarray:
    """q-values. `total_tests` counts hypotheses left out of `p_values` as untested but true:
    passing all possible pairs when only pairs seen together have a p-value stays valid."""
    p = np.asarray(p_values, dtype=float)
    if p.size == 0:
        return p
    tests = total_tests if total_tests is not None else p.size
    order = np.argsort(p)
    scaled = p[order] * tests / np.arange(1, p.size + 1)
    smallest_after = np.minimum.accumulate(scaled[::-1])[::-1]
    q = np.empty(p.size)
    q[order] = np.minimum(smallest_after, 1.0)
    return q


def high_rate_p(events: np.ndarray, trials: np.ndarray, base_rate: float) -> np.ndarray:
    """One-sided: the chance of at least this many events if the true rate is `base_rate`."""
    return np.asarray(binom.sf(np.asarray(events) - 1, trials, base_rate))


def together_p(
    both: np.ndarray, count_a: np.ndarray, count_b: np.ndarray, total: int
) -> np.ndarray:
    """One-sided: the chance two dishes share at least this many orders, across all orders."""
    return np.asarray(hypergeom.sf(np.asarray(both) - 1, total, count_a, count_b))


def pairs_within_p(
    both: np.ndarray, count_a: np.ndarray, count_b: np.ndarray, orders_here: np.ndarray
) -> np.ndarray:
    """The same test, within each pair's own restaurant. Any two dishes of one restaurant look
    linked across all orders simply because both are sold there; this removes that."""
    return np.asarray(hypergeom.sf(np.asarray(both) - 1, orders_here, count_a, count_b))


def rate_outliers(frame: pd.DataFrame, base_rate: float) -> pd.DataFrame:
    """Add `p` and `q` to a frame with `orders` and `cancelled`, most significant first."""
    p = high_rate_p(frame["cancelled"].to_numpy(), frame["orders"].to_numpy(), base_rate)
    scored = frame.assign(p=p, q=benjamini_hochberg(p))
    return scored.sort_values(["q", "p"], kind="stable").reset_index(drop=True)


def outlier_summary(scored: pd.DataFrame) -> dict[str, Any]:
    """What a naive p-value cut would claim, and what survives correction."""
    return {
        "tested": len(scored),
        "raw_p_below_alpha": int((scored["p"] < ALPHA).sum()),
        "expected_by_chance_at_most": round(len(scored) * ALPHA, 1),
        "after_fdr": int((scored["q"] < ALPHA).sum()),
    }


def planted_power(
    orders: np.ndarray,
    base_rate: float,
    rng: np.random.Generator,
    multiples: tuple[float, ...] = MULTIPLES,
    planted_share: float = 0.01,
    repeats: int = 20,
) -> dict[str, dict[str, float]]:
    """SYNTHETIC check: would this test notice real outliers if there were any?

    Simulates cancellations for entities with these order counts, with 1% of them cancelling
    `multiple` times as often, and runs the same test. Without this, finding none means
    little: the test might simply be unable to see them.
    """
    results: dict[str, dict[str, float]] = {}
    if orders.size == 0:
        return results
    size = max(1, int(orders.size * planted_share))
    for multiple in multiples:
        recalls, false_discoveries = [], []
        for _ in range(repeats):
            planted = np.zeros(orders.size, dtype=bool)
            planted[rng.choice(orders.size, size, replace=False)] = True
            rate = np.where(planted, min(base_rate * multiple, 1.0), base_rate)
            cancelled = rng.binomial(orders, rate)
            found = benjamini_hochberg(high_rate_p(cancelled, orders, base_rate)) < ALPHA
            recalls.append(found[planted].mean())
            false_discoveries.append((found & ~planted).sum())
        results[f"{multiple:g}x"] = {
            "recall": round(float(np.mean(recalls)), 4),
            "false_discoveries": round(float(np.mean(false_discoveries)), 2),
        }
    return results
