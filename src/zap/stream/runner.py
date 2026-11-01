"""Run the feed in real time, or as fast as possible to measure it."""

from __future__ import annotations

import random
import statistics
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from zap.stream.processor import WINDOW_MINUTES, Processor, Rates, load_rates, load_watch
from zap.stream.simulator import (
    MINUTE_FORMAT,
    TIME_FORMAT,
    Simulator,
    load_orders,
    replay_days,
)
from zap.stream.store import Store, utc_now

START_HOUR = 11  # lunch is about to start, so there's something to see at once
TICK_SECONDS = 1.0
SEVERITIES = (0.3, 0.5, 0.7, 1.0)
INCIDENT_MINUTES = 60
BUSY_HOURS = (11, 21)
INCIDENT_SEED = 11


def run_live(
    store: Store,
    simulator: Simulator,
    processor: Processor,
    start: datetime,
    speed: int,
    should_stop: Callable[[], bool],
    tick: float = TICK_SECONDS,
) -> datetime:
    """Move the replay clock forward in real time until the data runs out or someone stops it."""
    clock = start
    simulator.skip_to(start)
    store.set_status(started=utc_now(), speed=str(speed), stop="0")
    while not simulator.finished and not should_stop():
        began = time.monotonic()
        clock += timedelta(seconds=speed * tick)
        simulator.advance(clock)
        processor.step(clock)
        store.set_status(clock=clock.strftime(TIME_FORMAT), heartbeat=utc_now())
        time.sleep(max(0.0, tick - (time.monotonic() - began)))
    store.set_status(stopped=utc_now())
    return clock


def stop_requested(store: Store) -> bool:
    return store.status().get("stop") == "1"


def replay_fast(
    orders: pd.DataFrame,
    rates: Rates,
    watch: list[str],
    first: date,
    last: date,
    incidents: list[tuple[str, datetime, float]],
) -> tuple[list[tuple[Any, ...]], int, float]:
    """Everything the live feed does, minute by minute, with no waiting."""
    store = Store(":memory:")
    for city, start, share in incidents:
        end = start + timedelta(minutes=INCIDENT_MINUTES)
        store.add_incident(city, start.strftime(TIME_FORMAT), end.strftime(TIME_FORMAT), share)
    start_time = datetime(first.year, first.month, first.day)
    finish = datetime(last.year, last.month, last.day) + timedelta(days=1, minutes=10)
    simulator = Simulator(store, orders)
    processor = Processor(store, rates, watch, start_time)
    clock = start_time
    began = time.perf_counter()
    while clock < finish:
        clock += timedelta(minutes=1)
        simulator.advance(clock)
        processor.step(clock)
    seconds = time.perf_counter() - began
    alerts = store.con.execute("select raised_at, city, kind from alerts").fetchall()
    events = store.offset()
    store.close()
    return alerts, events, seconds


def plan_incidents(
    watch: list[str], first: date, days: int, share: float
) -> list[tuple[str, datetime, float]]:
    """One SYNTHETIC outage a day, in a big city at a busy hour, chosen before any scoring."""
    rng = random.Random(INCIDENT_SEED)  # noqa: S311 - choosing test cases, not secrets
    plan = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        hour = rng.randint(*BUSY_HOURS)
        start = datetime(day.year, day.month, day.day, hour, rng.randint(0, 59))
        plan.append((rng.choice(watch), start, share))
    return plan


def found(incident: tuple[str, datetime, float], alerts: list[tuple[Any, ...]]) -> float | None:
    """Minutes until the detector noticed this outage, or None if it never did."""
    city, start, _ = incident
    end = start + timedelta(minutes=INCIDENT_MINUTES + WINDOW_MINUTES)
    for raised_at, alert_city, kind in alerts:
        moment = datetime.strptime(raised_at, MINUTE_FORMAT)
        if alert_city == city and kind == "drop" and start <= moment <= end:
            return (moment - start).total_seconds() / 60
    return None


def evaluate(db: Path, days: int = 14, skip_days: int = 0) -> dict[str, Any]:
    """False alarms on the untouched days, then recall and delay on SYNTHETIC outages.

    `skip_days` measures an earlier stretch, which keeps some days out of the design work.
    """
    first, last = replay_days(db, days, skip_days)
    rates = load_rates(db, first, days)
    watch = load_watch(db, first)
    orders = load_orders(db, first, last)

    clean, events, seconds = replay_fast(orders, rates, watch, first, last, [])
    report: dict[str, Any] = {
        "replayed_days": days,
        "first_day": str(first),
        "last_day": str(last),
        "watched_cities": watch,
        "events": events,
        "events_per_second": round(events / seconds, 1),
        "false_alarms": len(clean),
        "false_alarms_per_day": round(len(clean) / days, 2),
        "false_alarm_detail": [list(alert) for alert in clean],
        "by_severity": {},
    }
    for share in SEVERITIES:
        plan = plan_incidents(watch, first, days, share)
        alerts, _, _ = replay_fast(orders, rates, watch, first, last, plan)
        delays = [delay for incident in plan if (delay := found(incident, alerts)) is not None]
        report["by_severity"][f"{share:.0%}"] = {
            "incidents": len(plan),
            "found": len(delays),
            "recall": round(len(delays) / len(plan), 4),
            "median_delay_minutes": statistics.median(delays) if delays else None,
        }
    return report
