"""The log between the simulator and the processor, kept in SQLite.

WAL mode lets the dashboard read while the feed writes, which a DuckDB file doesn't allow across
processes. It keeps the parts of a broker that matter here: an append-only sequence of events, and
a consumer offset saved in the same transaction as its results, so a restart neither loses nor
double-counts an order. Kafka would replace this class and leave the code around it alone.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ALL_CITIES = "All cities"
CONSUMER = "minute_windows"
FILE_NAME = "stream.sqlite"

SCHEMA = """
create table if not exists events (
    position integer primary key autoincrement,
    event_time text not null,
    city text not null,
    restaurant text not null,
    order_status text not null,
    amount real not null,
    is_late integer not null
);
create table if not exists offsets (consumer text primary key, position integer not null);
create table if not exists minute_stats (
    minute text not null,
    city text not null,
    orders integer not null,
    gmv real not null,
    cancelled integer not null,
    late integer not null,
    primary key (minute, city)
);
create table if not exists window_scores (
    minute text not null,
    city text not null,
    observed real not null,
    expected real not null,
    z real not null,
    primary key (minute, city)
);
create table if not exists alerts (
    id integer primary key autoincrement,
    raised_at text not null,
    resolved_at text,
    city text not null,
    kind text not null,
    observed real not null,
    expected real not null,
    z real not null
);
create table if not exists incidents (
    id integer primary key autoincrement,
    city text not null,
    starts_at text not null,
    ends_at text not null,
    drop_share real not null
);
create table if not exists insights (
    id integer primary key autoincrement,
    at text not null,
    text text not null
);
create table if not exists status (key text primary key, value text not null);
"""


@dataclass(frozen=True)
class Event:
    position: int
    event_time: str
    city: str
    restaurant: str
    order_status: str
    amount: float
    is_late: int


def stream_path(warehouse_dir: Path) -> Path:
    return warehouse_dir / FILE_NAME


class Store:
    def __init__(self, path: Path | str) -> None:
        self.con = sqlite3.connect(str(path), timeout=10, isolation_level=None)
        self.con.execute("pragma journal_mode=wal")
        self.con.execute("pragma synchronous=normal")
        self.con.executescript(SCHEMA)

    def close(self) -> None:
        self.con.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.con.execute("begin immediate")
        try:
            yield
        except BaseException:
            self.con.execute("rollback")
            raise
        self.con.execute("commit")

    def reset(self) -> None:
        """Empty every table, for a fresh replay."""
        tables = (
            "events",
            "offsets",
            "minute_stats",
            "window_scores",
            "alerts",
            "incidents",
            "insights",
            "status",
        )
        with self.transaction():
            for table in tables:
                self.con.execute(f"delete from {table}")  # noqa: S608 - fixed names

    def append(self, rows: Iterable[tuple[str, str, str, str, float, int]]) -> None:
        with self.transaction():
            self.con.executemany(
                "insert into events (event_time, city, restaurant, order_status, amount, "
                "is_late) values (?, ?, ?, ?, ?, ?)",
                rows,
            )

    def read_after(self, position: int, limit: int = 5000) -> list[Event]:
        rows = self.con.execute(
            "select position, event_time, city, restaurant, order_status, amount, is_late "
            "from events where position > ? order by position limit ?",
            (position, limit),
        )
        return [Event(*row) for row in rows]

    def offset(self, consumer: str = CONSUMER) -> int:
        row = self.con.execute(
            "select position from offsets where consumer = ?", (consumer,)
        ).fetchone()
        return int(row[0]) if row else 0

    def save_offset(self, position: int, consumer: str = CONSUMER) -> None:
        self.con.execute(
            "insert into offsets values (?, ?) "
            "on conflict (consumer) do update set position = excluded.position",
            (consumer, position),
        )

    def add_to_minute(
        self, minute: str, city: str, orders: int, gmv: float, cancelled: int, late: int
    ) -> None:
        self.con.execute(
            "insert into minute_stats values (?, ?, ?, ?, ?, ?) "
            "on conflict (minute, city) do update set "
            "orders = orders + excluded.orders, gmv = gmv + excluded.gmv, "
            "cancelled = cancelled + excluded.cancelled, late = late + excluded.late",
            (minute, city, orders, gmv, cancelled, late),
        )

    def window_orders(self, first_minute: str, last_minute: str) -> dict[str, int]:
        rows = self.con.execute(
            "select city, sum(orders) from minute_stats where minute between ? and ? group by city",
            (first_minute, last_minute),
        )
        return {city: int(total) for city, total in rows}

    def open_incidents(self) -> list[tuple[str, str, str, float]]:
        rows = self.con.execute("select city, starts_at, ends_at, drop_share from incidents")
        return [(row[0], row[1], row[2], float(row[3])) for row in rows]

    def add_incident(self, city: str, starts_at: str, ends_at: str, drop_share: float) -> None:
        self.con.execute(
            "insert into incidents (city, starts_at, ends_at, drop_share) values (?, ?, ?, ?)",
            (city, starts_at, ends_at, drop_share),
        )

    def raise_alert(
        self, at: str, city: str, kind: str, seen: float, wanted: float, z: float
    ) -> None:
        self.con.execute(
            "insert into alerts (raised_at, city, kind, observed, expected, z) "
            "values (?, ?, ?, ?, ?, ?)",
            (at, city, kind, seen, wanted, z),
        )

    def resolve_alert(self, at: str, city: str) -> None:
        self.con.execute(
            "update alerts set resolved_at = ? where city = ? and resolved_at is null", (at, city)
        )

    def save_score(self, minute: str, city: str, seen: float, wanted: float, z: float) -> None:
        self.con.execute(
            "insert or replace into window_scores values (?, ?, ?, ?, ?)",
            (minute, city, seen, wanted, z),
        )

    def add_insight(self, at: str, text: str) -> None:
        self.con.execute("insert into insights (at, text) values (?, ?)", (at, text))

    def set_status(self, **values: str) -> None:
        with self.transaction():
            for key, value in values.items():
                self.con.execute(
                    "insert into status values (?, ?) "
                    "on conflict (key) do update set value = excluded.value",
                    (key, value),
                )

    def status(self) -> dict[str, str]:
        return dict(self.con.execute("select key, value from status").fetchall())


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read_frame(path: Path, sql: str, params: tuple[str | int, ...] = ()) -> pd.DataFrame:
    """A read-only query for the dashboard. It can't block the feed or change anything."""
    con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5)
    try:
        return pd.read_sql_query(sql, con, params=params)
    finally:
        con.close()
