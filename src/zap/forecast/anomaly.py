"""Anomaly detection, scored on synthetic anomalies injected into a copy of the real series.

The source data holds no labelled incidents, so real days can't be scored. Injected events
give a known answer, and they never touch the warehouse.

A plain rolling z-score fails on this data: Saturdays are 60% above Tuesdays, so the weekly
peak itself looks unusual. The detector here compares each day with the same weekday in the
weeks before, then asks how unusual that gap is. It uses medians, so an earlier anomaly can't
inflate the "normal" range, and only earlier days, so it could run live.

Thresholds are the textbook values, set before seeing any result.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any
from zlib import crc32

import numpy as np
import pandas as pd

from zap.forecast.features import Panel

WEEKS = 8
WINDOW = 56
MODIFIED_Z = 3.5  # Iglewicz and Hoaglin's cut-off for the median-based z-score
PLAIN_Z = 3.0
PLAIN_WINDOW = 28
MAD_TO_STD = 1.4826
WARMUP = 7 * WEEKS + WINDOW + 7  # days before the detector has a score
SEEDS = 20

# (name, change in volume, days it lasts). Each series gets every event twice per seed.
EVENTS = (
    ("spike_20pct", 0.2, 1),
    ("spike_30pct", 0.3, 1),
    ("spike_50pct", 0.5, 1),
    ("drop_20pct", -0.2, 1),
    ("drop_30pct", -0.3, 1),
    ("drop_50pct", -0.5, 1),
    ("outage_40pct_3days", -0.4, 3),
)
MIN_GAP_DAYS = 10

Detector = Callable[[pd.Series], pd.Series]


def expected_log(y: pd.Series) -> pd.Series:
    """Log of the typical value for this weekday over the previous weeks."""
    logged = pd.Series(np.log1p(y.astype(float)), index=y.index)
    weekdays = pd.DatetimeIndex(y.index).dayofweek
    return logged.groupby(weekdays, group_keys=False).transform(
        lambda days: days.shift(1).rolling(WEEKS).median()
    )


def _median_abs_deviation(window: np.ndarray) -> float:
    return float(np.median(np.abs(window - np.median(window))))


def score(y: pd.Series) -> pd.DataFrame:
    """Expected value and robust z-score for each day, from earlier days only."""
    base = expected_log(y)
    gap = np.log1p(y.astype(float)) - base
    earlier = gap.shift(1).rolling(WINDOW)
    centre = earlier.median()
    spread = earlier.apply(_median_abs_deviation, raw=True) * MAD_TO_STD
    return pd.DataFrame(
        {
            "actual": y,
            "expected": np.expm1(base + centre),
            "z": (gap - centre) / spread.replace(0.0, np.nan),
        }
    )


def seasonal_z(y: pd.Series) -> pd.Series:
    return score(y)["z"].rename("z")


def plain_z(y: pd.Series) -> pd.Series:
    """The textbook version, kept as the comparison."""
    earlier = y.shift(1).rolling(PLAIN_WINDOW)
    return ((y - earlier.mean()) / earlier.std()).rename("z")


def inject(y: pd.Series, rng: np.random.Generator) -> tuple[pd.Series, pd.Series]:
    """A changed copy of the series, and each changed day's event name (None elsewhere)."""
    events = [event for event in EVENTS for _ in range(2)]
    rng.shuffle(events)
    room = len(y) - WARMUP - sum(event[2] + MIN_GAP_DAYS for event in events)
    if room < 0:
        raise ValueError(f"series too short to inject events: {len(y)} days")
    # Spreading the spare days at random keeps every event apart without any rejection loop.
    spare = np.sort(rng.integers(0, room + 1, size=len(events)))
    changed = y.astype(float).copy()
    labels = pd.Series([None] * len(y), index=y.index, dtype=object)
    start = WARMUP
    for (name, change, days), extra in zip(events, spare, strict=True):
        begin = start + int(extra)
        changed.iloc[begin : begin + days] *= 1 + change
        labels.iloc[begin : begin + days] = name
        start += days + MIN_GAP_DAYS
    return changed, labels


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def evaluate(
    panel: Panel, detector: Detector, threshold: float, seeds: int = SEEDS
) -> dict[str, Any]:
    """Pool every injected day over all series and seeds. A flagged normal day is a false alarm."""
    outcome: Counter[str] = Counter()
    event_hits: Counter[str] = Counter()
    event_days: Counter[str] = Counter()
    scope_hits: Counter[str] = Counter()
    scope_days: Counter[str] = Counter()
    for metric, series in panel.items():
        for scope, y in series.items():
            for seed in range(seeds):
                rng = np.random.default_rng([seed, crc32(f"{metric}/{scope}".encode())])
                changed, labels = inject(y, rng)
                z = detector(changed)
                scored = z.notna() & (np.arange(len(z)) >= WARMUP)
                flagged = z.abs() >= threshold
                injected = labels.notna()
                outcome["tp"] += int((flagged & injected & scored).sum())
                outcome["fn"] += int((~flagged & injected & scored).sum())
                outcome["fp"] += int((flagged & ~injected & scored).sum())
                outcome["tn"] += int((~flagged & ~injected & scored).sum())
                for name, group in labels[injected & scored].groupby(labels):
                    hits = int(flagged[group.index].sum())
                    event_hits[str(name)] += hits
                    event_days[str(name)] += len(group)
                    scope_hits[scope] += hits
                    scope_days[scope] += len(group)
    precision = _rate(outcome["tp"], outcome["tp"] + outcome["fp"])
    recall = _rate(outcome["tp"], outcome["tp"] + outcome["fn"])
    both = (precision or 0) + (recall or 0)
    return {
        "threshold": threshold,
        "injected_days": outcome["tp"] + outcome["fn"],
        "normal_days": outcome["fp"] + outcome["tn"],
        "precision": precision,
        "recall": recall,
        "f1": round(2 * (precision or 0) * (recall or 0) / both, 4) if both else None,
        "false_alarms_per_1000_normal_days": round(
            1000 * outcome["fp"] / max(outcome["fp"] + outcome["tn"], 1), 2
        ),
        "recall_by_event": {
            name: _rate(event_hits[name], days) for name, days in sorted(event_days.items())
        },
        "recall_by_scope": {
            name: _rate(scope_hits[name], days) for name, days in sorted(scope_days.items())
        },
    }


def flag_real_days(panel: Panel, threshold: float = MODIFIED_Z) -> pd.DataFrame:
    """Days the detector flags in the real, unchanged series."""
    frames = []
    for metric, series in panel.items():
        for scope, y in series.items():
            scored = score(y)
            flagged = scored[scored["z"].abs() >= threshold]
            frames.append(
                flagged.reset_index(names="order_date").assign(metric=metric, scope=scope)
            )
    columns = ["metric", "scope", "order_date", "actual", "expected", "z"]
    return pd.concat(frames, ignore_index=True)[columns]
