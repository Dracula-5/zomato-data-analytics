"""What the dashboard asks of the feed. Read-only, so a page can't disturb it."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

from zap.stream.detector import normal_range
from zap.stream.simulator import MINUTE_FORMAT, TIME_FORMAT
from zap.stream.store import ALL_CITIES, read_frame

STALE_SECONDS = 6


@dataclass(frozen=True)
class FeedState:
    running: bool
    clock: datetime | None
    speed: int


def feed_state(path: Path) -> FeedState:
    if not path.exists():
        return FeedState(False, None, 0)
    status = dict(read_frame(path, "select key, value from status").to_numpy().tolist())
    clock = datetime.strptime(status["clock"], TIME_FORMAT) if "clock" in status else None
    beat = status.get("heartbeat")
    running = False
    if beat:
        running = (datetime.now(UTC) - datetime.fromisoformat(beat)).total_seconds() < STALE_SECONDS
    return FeedState(running, clock, int(status.get("speed", 0)))


def hour_summary(path: Path, clock: datetime, hours_back: int = 0) -> dict[str, float]:
    last = clock - timedelta(hours=hours_back)
    first = last - timedelta(minutes=59)
    row = read_frame(
        path,
        """select coalesce(sum(orders), 0) as orders, coalesce(sum(gmv), 0) as gmv,
                  coalesce(sum(cancelled), 0) as cancelled, coalesce(sum(late), 0) as late
           from minute_stats where city = ? and minute between ? and ?""",
        (ALL_CITIES, first.strftime(MINUTE_FORMAT), last.strftime(MINUTE_FORMAT)),
    ).iloc[0]
    return {name: float(row[name]) for name in ("orders", "gmv", "cancelled", "late")}


def city_activity(path: Path, clock: datetime) -> pd.DataFrame:
    first = (clock - timedelta(minutes=59)).strftime(MINUTE_FORMAT)
    return read_frame(
        path,
        """select city, sum(orders) as orders from minute_stats
           where city != ? and minute >= ? group by city order by orders desc""",
        (ALL_CITIES, first),
    )


def window_history(path: Path, city: str, minutes: int = 360) -> pd.DataFrame:
    """Recent scored windows, oldest first, with the range that wouldn't raise an alert."""
    frame = read_frame(
        path,
        """select minute, observed, expected from window_scores
           where city = ? order by minute desc limit ?""",
        (city, minutes),
    ).iloc[::-1]
    frame["minute"] = pd.to_datetime(frame["minute"])
    ranges = [normal_range(value) for value in frame["expected"]]
    frame["low"] = [low for low, _ in ranges]
    frame["high"] = [high for _, high in ranges]
    return frame.reset_index(drop=True)


def recent_orders(path: Path, count: int = 10) -> pd.DataFrame:
    return read_frame(
        path,
        """select substr(event_time, 12, 5) as "Time", city as "City",
                  restaurant as "Restaurant", order_status as "Status", amount as "Amount"
           from events order by position desc limit ?""",
        (count,),
    )


def recent_alerts(path: Path, count: int = 8) -> pd.DataFrame:
    return read_frame(
        path,
        """select raised_at, resolved_at, city, kind, observed, expected
           from alerts order by id desc limit ?""",
        (count,),
    )


def recent_notes(path: Path, count: int = 6) -> pd.DataFrame:
    return read_frame(path, "select at, text from insights order by id desc limit ?", (count,))


def watched_cities(path: Path) -> list[str]:
    frame = read_frame(path, "select distinct city from window_scores order by city")
    cities = frame["city"].tolist()
    return sorted(cities, key=lambda city: city != ALL_CITIES)
