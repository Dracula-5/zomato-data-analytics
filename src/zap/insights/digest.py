"""A weekly digest: what changed, and whether it's more than noise.

Two weeks of order counts always differ, so every change is tested before it's called one: if
counts are Poisson, `(this - last) / sqrt(this + last)` is how surprising the gap is. Cities are
corrected for being tested together, or one would look special by chance every week. On this data
that usually leaves nothing, and saying so is the finding.

An optional brief puts the findings in plain words. Every number in it must appear in the findings,
and a brief that fails that check is dropped rather than shown.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
from pydantic import BaseModel
from scipy.stats import norm

from zap.kg.stats import benjamini_hochberg
from zap.llm.client import LlmClient, LlmError

DAYS = 7
CITIES_TESTED = 30
ALPHA = 0.05
NOTEWORTHY_Z = 3.0
PURPOSE = "digest_brief"
MAX_BRIEF_TOKENS = 400
LATEST = "latest.json"
HISTORY = "history.jsonl"

KPI_SQL = """
select sum(orders) as orders,
       sum(gmv) as gmv,
       sum(cancel_rate * orders) / sum(orders) as cancel_rate,
       sum(sla_breach_rate * delivered_orders) / sum(delivered_orders) as sla_breach_rate
from marts.mart_daily_kpis
where order_date between ? and ?
"""
CITY_SQL = """
select city,
       count(*) filter (where order_date between ? and ?) as this_week,
       count(*) filter (where order_date between ? and ?) as last_week
from marts.fct_orders
where order_date between ? and ?
group by city
order by this_week + last_week desc, city
limit ?
"""
FORECAST_SQL = """
select sum(forecast) from forecast.next_days
where metric = 'orders' and scope = 'National' and model = 'ets'
  and target_date <= ?
"""

BRIEF_RULES = """You write a short brief for the operations lead of a food delivery company.
Use only the findings you are given. Add no causes, advice or numbers of your own.
A change described as within normal variation is noise, so don't present it as a trend.
Write at most three plain sentences."""


class Brief(BaseModel):
    text: str


def surprise(this: float, last: float) -> float:
    total = this + last
    return float((this - last) / np.sqrt(total)) if total else 0.0


def _kpis(con: duckdb.DuckDBPyConnection, first: date, last: date) -> dict[str, float]:
    row = con.execute(KPI_SQL, [first, last]).fetchone()
    names = ("orders", "gmv", "cancel_rate", "sla_breach_rate")
    if row is None or row[0] is None:
        raise ValueError("no orders in that period")
    return dict(zip(names, map(float, row), strict=True))


def _forecast(con: duckdb.DuckDBPyConnection, through: date) -> float | None:
    try:
        row = con.execute(FORECAST_SQL, [through]).fetchone()
    except duckdb.Error:  # no forecast schema in this warehouse
        return None
    return float(row[0]) if row and row[0] is not None else None


def _cities(con: duckdb.DuckDBPyConnection, end: date) -> list[dict[str, Any]]:
    start = end - timedelta(days=DAYS - 1)
    previous_end = start - timedelta(days=1)
    previous_start = previous_end - timedelta(days=DAYS - 1)
    rows = con.execute(
        CITY_SQL, [start, end, previous_start, previous_end, previous_start, end, CITIES_TESTED]
    ).fetchall()
    z = np.array([surprise(this, last) for _, this, last in rows])
    q = benjamini_hochberg(2 * norm.sf(np.abs(z)))
    return [
        {
            "city": city,
            "this_week": int(this),
            "last_week": int(last),
            "change": round((this - last) / last, 4) if last else None,
            "z": round(float(score), 2),
            "q": round(float(value), 4),
        }
        for (city, this, last), score, value in zip(rows, z, q, strict=True)
    ]


def _direction(change: float) -> str:
    return "rose" if change >= 0 else "fell"


def _verdict(z: float) -> str:
    return "beyond normal variation" if abs(z) >= NOTEWORTHY_Z else "within normal variation"


def findings(facts: dict[str, Any]) -> list[str]:
    now, before = facts["this_week"], facts["last_week"]
    orders_change = now["orders"] / before["orders"] - 1
    gmv_change = now["gmv"] / before["gmv"] - 1
    lines = [
        f"Orders {_direction(orders_change)} {abs(orders_change):.1%} to {now['orders']:,.0f} "
        f"against the previous {DAYS} days, which is {_verdict(facts['orders_z'])}.",
        f"Delivered GMV {_direction(gmv_change)} {abs(gmv_change):.1%} "
        f"to ₹{now['gmv'] / 1e7:,.2f} Cr.",
        f"The cancel rate was {now['cancel_rate']:.1%}, from {before['cancel_rate']:.1%}, and "
        f"{now['sla_breach_rate']:.1%} of deliveries were late, from "
        f"{before['sla_breach_rate']:.1%}.",
    ]
    moved = [city for city in facts["cities"] if city["q"] < ALPHA]
    if moved:
        names = ", ".join(f"{city['city']} ({city['change']:+.1%})" for city in moved)
        lines.append(f"Beyond noise after testing {len(facts['cities'])} cities: {names}.")
    else:
        lines.append(f"None of the {len(facts['cities'])} largest cities moved beyond noise.")
    if facts["forecast_next_week"] is not None:
        total = facts["forecast_next_week"]
        lines.append(f"The next {DAYS} days are forecast at {total:,.0f} orders.")
    return lines


def analyse(db: Path) -> dict[str, Any]:
    con = duckdb.connect(str(db), read_only=True)
    try:
        row = con.execute("select max(order_date) from marts.mart_daily_kpis").fetchone()
        if row is None or row[0] is None:
            raise ValueError(f"no orders in {db}")
        end: date = row[0]
        start = end - timedelta(days=DAYS - 1)
        previous_end = start - timedelta(days=1)
        previous_start = previous_end - timedelta(days=DAYS - 1)
        now = _kpis(con, start, end)
        before = _kpis(con, previous_start, previous_end)
        facts: dict[str, Any] = {
            "period": {"first": str(start), "last": str(end)},
            "previous": {"first": str(previous_start), "last": str(previous_end)},
            "this_week": now,
            "last_week": before,
            "orders_z": round(surprise(now["orders"], before["orders"]), 2),
            "cities": _cities(con, end),
            "forecast_next_week": _forecast(con, end + timedelta(days=DAYS)),
        }
    finally:
        con.close()
    facts["findings"] = findings(facts)
    return facts


def write_brief(client: LlmClient, lines: list[str]) -> str | None:
    """Plain words for the findings, or None if there's no model or it can't pass the check."""
    from zap.agent.validate import check_numbers, numbers_in

    source = "\n".join(lines)
    known = numbers_in(source)

    def only_these_numbers(brief: Brief) -> None:
        check = check_numbers(brief.text, known)
        if not check.passed:
            raise ValueError(check.detail)

    try:
        return client.complete_as(
            BRIEF_RULES,
            source,
            PURPOSE,
            Brief,
            check=only_these_numbers,
            max_output=MAX_BRIEF_TOKENS,
        ).text
    except LlmError:
        return None


def save(digest: dict[str, Any], folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    text = json.dumps(digest, indent=2, ensure_ascii=False)
    (folder / LATEST).write_text(text + "\n", encoding="utf-8", newline="\n")
    with (folder / HISTORY).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(digest, ensure_ascii=False) + "\n")
    return folder / LATEST


def load_latest(folder: Path) -> dict[str, Any] | None:
    path = folder / LATEST
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
