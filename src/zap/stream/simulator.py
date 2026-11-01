"""Replay past orders as events, as if they were happening now. All of it is SIMULATED.

No customer fields are loaded, because the feed doesn't need them. A small share of events arrive
late, because real feeds do. An incident is a SYNTHETIC outage: for a while, a share of one city's
orders never happen, which is how the detector gets tested.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from zap.stream.store import Store

LATE_SHARE = 0.02
MAX_LATE_SECONDS = 150
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
MINUTE_FORMAT = "%Y-%m-%d %H:%M"
ORDERS_SQL = """
select o.order_timestamp, o.city, r.restaurant_name, o.order_status, o.sales_amount,
       o.is_sla_breach
from marts.fct_orders o
left join marts.dim_restaurant r on r.restaurant_id = o.restaurant_id
where o.order_date between ? and ?
order by o.order_timestamp
"""


def replay_days(db: Path, days: int, skip_days: int = 0) -> tuple[date, date]:
    """`days` days ending `skip_days` before the last day in the warehouse."""
    con = duckdb.connect(str(db), read_only=True)
    try:
        row = con.execute("select max(order_date) from marts.fct_orders").fetchone()
    finally:
        con.close()
    if row is None or row[0] is None:
        raise ValueError(f"no orders in {db}")
    last: date = row[0] - timedelta(days=skip_days)
    return last - timedelta(days=days - 1), last


def load_orders(db: Path, first: date, last: date, seed: int = 7) -> pd.DataFrame:
    con = duckdb.connect(str(db), read_only=True)
    try:
        orders = con.execute(ORDERS_SQL, [first, last]).df()
    finally:
        con.close()
    orders["restaurant_name"] = orders["restaurant_name"].fillna("Unknown restaurant")
    rng = np.random.default_rng(seed)
    late = rng.random(len(orders)) < LATE_SHARE
    delay = rng.integers(30, MAX_LATE_SECONDS, len(orders)) * late
    orders["arrival"] = orders["order_timestamp"] + pd.to_timedelta(delay, unit="s")
    orders["keep"] = rng.random(len(orders))  # fixed per order, so an incident is repeatable
    return orders.sort_values("arrival", kind="stable").reset_index(drop=True)


class Simulator:
    def __init__(self, store: Store, orders: pd.DataFrame) -> None:
        self.store = store
        self.orders = orders
        self.arrivals = orders["arrival"].to_numpy()
        self.position = 0
        self.incidents: list[tuple[str, str, str, float]] = []

    @property
    def finished(self) -> bool:
        return self.position >= len(self.orders)

    def skip_to(self, moment: datetime) -> None:
        """Start the replay at `moment`, without sending what came before it."""
        self.position = int(np.searchsorted(self.arrivals, np.datetime64(moment), side="right"))

    def _lost(self, city: str, stamp: str, keep: float) -> bool:
        return any(
            city == incident_city and start <= stamp < end and keep < share
            for incident_city, start, end, share in self.incidents
        )

    def advance(self, until: datetime) -> int:
        """Send every order that has arrived by `until`. Returns how many were sent."""
        end = int(np.searchsorted(self.arrivals, np.datetime64(until), side="right"))
        due = self.orders.iloc[self.position : end]
        self.position = end
        if due.empty:
            return 0
        self.incidents = self.store.open_incidents()
        rows = []
        for stamp, city, restaurant, status, amount, late, keep in zip(
            due["order_timestamp"].dt.strftime(TIME_FORMAT).tolist(),
            due["city"].tolist(),
            due["restaurant_name"].tolist(),
            due["order_status"].tolist(),
            due["sales_amount"].tolist(),
            due["is_sla_breach"].tolist(),
            due["keep"].tolist(),
            strict=True,
        ):
            if self.incidents and self._lost(city, stamp, keep):
                continue
            rows.append((stamp, city, restaurant, status, float(amount), int(bool(late))))
        self.store.append(rows)
        return len(rows)
