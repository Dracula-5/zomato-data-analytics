"""Deterministic user-level sampling.

A user is in the sample when `bucket(user_id) < pct * 100`, where the bucket is a Knuth
multiplicative hash in the range 0-9999. Why this hash:

* **Deterministic and portable:** integer arithmetic gives the same answer in Python,
  DuckDB and Snowflake, on every version. Built-in `hash()` functions don't promise that.
* **User-level:** a sampled user keeps their *entire* order history, which the graph,
  recommender and per-user features need. Random row sampling would cut histories apart.
* **Nested:** the 1% sample is a subset of the 5% sample, because thresholds only grow.
"""

from __future__ import annotations

KNUTH = 2_654_435_761  # floor(2**32 / golden ratio)
BUCKETS = 10_000


def bucket(user_id: int) -> int:
    return ((user_id * KNUTH) % 2**32) % BUCKETS


def threshold(pct: float) -> int:
    if not 0 < pct <= 100:
        raise ValueError(f"sample percent must be in (0, 100], got {pct}")
    return round(pct * BUCKETS / 100)


def in_sample(user_id: int, pct: float) -> bool:
    return bucket(user_id) < threshold(pct)


def bucket_sql(column: str) -> str:
    """SQL expression equal to `bucket()`; `column` must already be a quoted identifier."""
    return f"((CAST({column} AS BIGINT) * {KNUTH}) % {2**32}) % {BUCKETS}"


def label(pct: float) -> str:
    """Folder label for a dataset: `full` or e.g. `sample_5pct`."""
    return "full" if pct >= 100 else f"sample_{pct:g}pct".replace(".", "p")
