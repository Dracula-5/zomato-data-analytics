"""Turn the event log into minute windows, scores, alerts and an hourly note.

Events count towards the minute they happened in, not the minute they arrived, so a late event
lands in the right place. A minute only gets judged once the watermark has passed it, which gives
late events time to arrive. The offset is saved in the same transaction as the counts it produced.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb

from zap.stream import detector
from zap.stream.simulator import MINUTE_FORMAT
from zap.stream.store import ALL_CITIES, Event, Store

WINDOW_MINUTES = 30
LATENESS_MINUTES = 3
BASELINE_WEEKS = 8
RATES_SQL = """
select isodow(order_date) as weekday, order_hour as hour, city, count(*) as orders
from marts.fct_orders
where order_date >= ? and order_date < ?
group by all
"""
GROWTH_SQL = """
select count(*) filter (where order_date < ?) as earlier,
       count(*) filter (where order_date >= ?) as later
from marts.fct_orders
where order_date >= ? and order_date < ?
"""
WATCH_SQL = """
select city from marts.fct_orders
where order_date >= ? and order_date < ?
group by city order by count(*) desc, city limit ?
"""


@dataclass(frozen=True)
class Rates:
    """Normal orders per minute, by weekday, hour and city."""

    per_minute: dict[tuple[int, int, str], float]

    def expected(self, city: str, last_minute: datetime, minutes: int) -> float:
        total = 0.0
        for back in range(minutes):
            moment = last_minute - timedelta(minutes=back)
            total += self.per_minute.get((moment.isoweekday(), moment.hour, city), 0.0)
        return total


def load_rates(db: Path, before: date, replay_days: int, weeks: int = BASELINE_WEEKS) -> Rates:
    """The weeks before the replay, so a replayed day is never part of its own baseline.

    The business is growing, so a plain average of those weeks runs low and turns ordinary minutes
    into false spikes. The baseline's own growth carries it forward to the middle of the replay.
    """
    start = before - timedelta(weeks=weeks)
    middle = before - timedelta(weeks=weeks / 2)
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(RATES_SQL, [start, before]).fetchall()
        growth = con.execute(GROWTH_SQL, [middle, middle, start, before]).fetchone()
    finally:
        con.close()
    earlier, later = growth if growth else (0, 0)
    weeks_ahead = weeks / 2 + replay_days / 14  # from the baseline's middle to the replay's
    scale = (later / earlier) ** (weeks_ahead / (weeks / 2)) if earlier and later else 1.0
    rates: dict[tuple[int, int, str], float] = defaultdict(float)
    for weekday, hour, city, orders in rows:
        per_minute = scale * orders / (weeks * 60)
        rates[(weekday, hour, city)] += per_minute
        rates[(weekday, hour, ALL_CITIES)] += per_minute
    return Rates(dict(rates))


def load_watch(db: Path, before: date, cities: int = 8) -> list[str]:
    start = before - timedelta(weeks=BASELINE_WEEKS)
    con = duckdb.connect(str(db), read_only=True)
    try:
        return [row[0] for row in con.execute(WATCH_SQL, [start, before, cities]).fetchall()]
    finally:
        con.close()


def minute_of(stamp: str) -> str:
    return stamp[:16]


class Processor:
    def __init__(self, store: Store, rates: Rates, watch: list[str], started: datetime) -> None:
        self.store = store
        self.rates = rates
        self.watched = [ALL_CITIES, *watch]
        self.started = started.replace(second=0, microsecond=0)
        self.judged_through = self.started
        self.open_alerts: set[str] = set()

    def _count(self, events: list[Event]) -> None:
        totals: dict[tuple[str, str], list[float]] = defaultdict(lambda: [0, 0.0, 0, 0])
        for event in events:
            for city in (event.city, ALL_CITIES):
                row = totals[(minute_of(event.event_time), city)]
                row[0] += 1
                if event.order_status == "Delivered":
                    row[1] += event.amount
                row[2] += event.order_status == "Cancelled"
                row[3] += event.is_late
        with self.store.transaction():
            for (minute, city), (orders, gmv, cancelled, late) in totals.items():
                self.store.add_to_minute(minute, city, int(orders), gmv, int(cancelled), int(late))
            self.store.save_offset(events[-1].position)

    def consume(self) -> int:
        """Count everything new in the log. Returns how many events that was."""
        total = 0
        while events := self.store.read_after(self.store.offset()):
            self._count(events)
            total += len(events)
        return total

    def _judge(self, minute: datetime) -> None:
        first = minute - timedelta(minutes=WINDOW_MINUTES - 1)
        label = minute.strftime(MINUTE_FORMAT)
        seen = self.store.window_orders(first.strftime(MINUTE_FORMAT), label)
        for city in self.watched:
            observed = float(seen.get(city, 0))
            expected = self.rates.expected(city, minute, WINDOW_MINUTES)
            z = detector.surprise(observed, expected)
            self.store.save_score(label, city, observed, expected, z)
            change = detector.next_state(city in self.open_alerts, observed, expected)
            if change is None:
                continue
            if change.action == "raise":
                self.open_alerts.add(city)
                self.store.raise_alert(label, city, change.kind, observed, expected, z)
            else:
                self.open_alerts.discard(city)
                self.store.resolve_alert(label, city)

    def _note_the_hour(self, minute: datetime) -> None:
        first = minute - timedelta(minutes=59)
        seen = self.store.window_orders(
            first.strftime(MINUTE_FORMAT), minute.strftime(MINUTE_FORMAT)
        )
        orders = seen.pop(ALL_CITIES, 0)
        expected = self.rates.expected(ALL_CITIES, minute, 60)
        if not orders or not expected:
            return
        busiest = max(seen, key=lambda city: seen[city])
        gap = (orders - expected) / expected
        self.store.add_insight(
            minute.strftime(MINUTE_FORMAT),
            f"{first:%H:%M} to {minute:%H:%M}: {orders:,} orders, {gap:+.1%} against normal for "
            f"this hour. Busiest city: {busiest} with {seen[busiest]:,}.",
        )

    def step(self, clock: datetime) -> int:
        """Bring everything up to `clock`. Returns how many events were counted."""
        counted = self.consume()
        watermark = clock.replace(second=0, microsecond=0) - timedelta(minutes=LATENESS_MINUTES)
        while self.judged_through < watermark:
            self.judged_through += timedelta(minutes=1)
            minute = self.judged_through
            if minute < self.started + timedelta(minutes=WINDOW_MINUTES):
                continue  # the first full window isn't here yet
            with self.store.transaction():
                self._judge(minute)
            if minute.minute == 59:
                self._note_the_hour(minute)
        return counted
