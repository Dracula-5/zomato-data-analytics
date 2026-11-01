"""Spot a city's orders falling or jumping, from a rolling window against what's normal for then.

Orders here behave like a Poisson count, so the exact Poisson tail says how likely a normal window
is to look this extreme. That holds at the low counts of smaller cities, where the usual z-score
doesn't: no orders against 14 expected is nearly impossible, yet it only scores -3.7.

An alert opens below `RAISE_CHANCE` and closes once the window is back under `CLEAR_Z`, so one
incident is one alert rather than a flicker of them. `RAISE_CHANCE` is small because about 13,000
windows get checked a day, which at this level allows at most 0.13 false alarms a day.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from scipy.stats import poisson

RAISE_CHANCE = 1e-5
CLEAR_Z = 2.0
MIN_EXPECTED = 5.0  # below this, even no orders at all isn't unusual enough to mean anything


@dataclass(frozen=True)
class Change:
    action: str  # "raise" or "resolve"
    kind: str = ""


def surprise(observed: float, expected: float) -> float:
    """For showing how far a window is from normal. Decisions use `tail_chance`."""
    return (observed - expected) / math.sqrt(expected) if expected > 0 else 0.0


def tail_chance(observed: float, expected: float) -> float:
    """How likely a normal window is to be at least this far out, on this side of expected."""
    if observed < expected:
        return float(poisson.cdf(observed, expected))
    return float(poisson.sf(observed - 1, expected))


def normal_range(expected: float) -> tuple[float, float]:
    """The window counts that wouldn't raise an alert."""
    return float(poisson.ppf(RAISE_CHANCE, expected)), float(poisson.isf(RAISE_CHANCE, expected))


def next_state(is_open: bool, observed: float, expected: float) -> Change | None:
    """What should happen to one city's alert, given its latest window."""
    if is_open:
        return Change("resolve") if abs(surprise(observed, expected)) < CLEAR_Z else None
    if expected < MIN_EXPECTED or tail_chance(observed, expected) >= RAISE_CHANCE:
        return None
    return Change("raise", "drop" if observed < expected else "spike")
