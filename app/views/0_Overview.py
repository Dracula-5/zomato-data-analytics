"""Overview: where the business stands, what stands out, and how far it has all been tested."""

import pandas as pd
import plotly.express as px
import streamlit as st

from zap.config import get_settings
from zap.dashboard.charts import city_map, trend
from zap.dashboard.components import hero, stat
from zap.dashboard.theme import sequential_scale, style
from zap.dashboard.ui import load, measured, page_setup, table_view, theme_mode
from zap.insights.digest import load_latest
from zap.metrics.collect import NOT_RUN

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WINDOW_DAYS = 28
MEASURES = {"Orders": ("orders", ""), "GMV": ("gmv", "₹")}
# What each tile is called here, and the metric's name in docs/METRICS.md.
TESTED = (
    ("dbt tests", "dbt tests"),
    ("National forecast error", "National orders MAPE, ETS"),
    ("Text-to-SQL accuracy", "Text-to-SQL execution accuracy"),
    ("RAG faithfulness", "RAG faithfulness"),
    ("SQL attacks refused", "SQL attacks refused"),
)


def recent_and_before(series: pd.Series) -> tuple[float, float]:
    return (
        float(series.tail(WINDOW_DAYS).mean()),
        float(series.iloc[-2 * WINDOW_DAYS : -WINDOW_DAYS].mean()),
    )


def weekly(series: pd.Series) -> list[float]:
    """One point a week for a sparkline, since the daily values swing with the weekday."""
    return [float(value) for value in series.rolling(7).mean().dropna().iloc[::7]]


def tile(column, label, series, value, change, noise=False, help_text=None) -> None:  # type: ignore[no-untyped-def]
    column.metric(
        label,
        value,
        delta=change,
        delta_color="off" if noise else "normal",  # no red for movement that's only noise
        chart_data=weekly(series),
        chart_type="area",
        help=help_text,
    )


db = page_setup("Overview", "How the business is doing, and how far the platform has been tested.")
if db is not None:
    mode = theme_mode()
    k = load("kpi_summary", db)
    daily = load("daily_kpis", db).sort_values("order_date")
    cities = load("city_summary", db, k["first_day"], k["last_day"])
    digest = load_latest(get_settings().artifacts_dir / "digest")
    orders_text = f"{k['orders'] / 1e6:.2f}M" if k["orders"] > 1e6 else f"{k['orders']:,}"
    this_week = ""
    if digest:
        this_week = digest.get("brief") or " ".join(digest["findings"][:2])

    hero(
        "Food delivery operations",
        f"₹{k['gmv'] / 1e7:,.1f} Cr delivered from {orders_text} orders",
        f"{k['first_day']:%d %b %Y} to {k['last_day']:%d %b %Y}, across {len(cities):,} cities.",
        ["SYNTHETIC orders", f"Dataset: {db.stem}"],
        "This week",
        this_week,
    )

    tiles = st.columns(6)
    now, then = recent_and_before(daily["orders"])
    tile(tiles[0], "Orders a day", daily["orders"], f"{now:,.0f}", f"{now / then - 1:+.1%}")
    now, then = recent_and_before(daily["gmv"])
    tile(tiles[1], "GMV a day", daily["gmv"], f"₹{now / 1e5:,.1f} L", f"{now / then - 1:+.1%}")
    now, then = recent_and_before(daily["aov"])
    tile(
        tiles[2],
        "Avg order value",
        daily["aov"],
        f"₹{now:,.0f}",
        f"{now / then - 1:+.1%}",
        noise=True,
    )
    now, then = recent_and_before(daily["cancel_rate"])
    tile(
        tiles[3],
        "Cancel rate",
        daily["cancel_rate"],
        f"{now:.1%}",
        f"{(now - then) * 100:+.2f} pts",
        noise=True,
    )
    now, then = recent_and_before(daily["sla_breach_rate"])
    tile(
        tiles[4],
        "Late deliveries",
        daily["sla_breach_rate"],
        f"{now:.1%}",
        f"{(now - then) * 100:+.2f} pts",
        noise=True,
        help_text="Delivered later than 40 minutes.",
    )
    now, then = recent_and_before(daily["active_customers"])
    tile(
        tiles[5],
        "Customers a day",
        daily["active_customers"],
        f"{now:,.0f}",
        f"{now / then - 1:+.1%}",
    )

    st.caption(f"Averages of the last {WINDOW_DAYS} days, against the {WINDOW_DAYS} before them.")

    main, side = st.columns([2.2, 1])
    with main:
        choice = st.segmented_control(
            "Measure", list(MEASURES), default="Orders", label_visibility="collapsed"
        )
        measure, prefix = MEASURES[choice or "Orders"]
        st.plotly_chart(
            trend(daily, measure, f"{choice or 'Orders'} per day", mode, prefix, height=372),
            width="stretch",
        )
    with side:
        st.subheader("What stands out")
        grid = load("weekday_hour", db)
        history = daily["orders"]
        growth = history.tail(WINDOW_DAYS).mean() / history.head(WINDOW_DAYS).mean()
        busiest = grid.loc[grid["orders"].idxmax()]
        leader = cities.iloc[0]
        weekend = daily.groupby("is_weekend")["orders"].mean()
        stat(f"Daily orders now, against the first {WINDOW_DAYS} days", f"{growth:.1f}x")
        stat(
            "Busiest hour of the week",
            f"{WEEKDAYS[int(busiest['weekday']) - 1]} {int(busiest['hour']):02d}:00",
        )
        stat("Weekend orders, against a weekday", f"{weekend[True] / weekend[False]:.1f}x")
        stat(f"Largest city, {leader['gmv'] / cities['gmv'].sum():.1%} of GMV", leader["city"])

    left, right = st.columns(2)
    with left:
        pivot = grid.pivot(index="weekday", columns="hour", values="orders").fillna(0)
        pivot.index = WEEKDAYS[: len(pivot.index)]
        heat = px.imshow(
            pivot,
            aspect="auto",
            color_continuous_scale=sequential_scale(mode),
            labels={"x": "Hour of day", "y": "", "color": "Orders"},
        )
        heat.update_traces(
            hovertemplate="%{y} %{x}:00<br>Orders: %{z:,}<extra></extra>", xgap=2, ygap=2
        )
        heat.update_xaxes(dtick=3)
        heat.update_layout(title="When people order")
        st.plotly_chart(style(heat, mode, height=420), width="stretch")
    with right:
        st.plotly_chart(city_map(cities, "gmv", "Where GMV comes from", mode), width="stretch")
    st.caption(
        "Demand peaks at lunch (13:00) and dinner (19:00-20:00) and is higher at weekends: "
        "the main real signal in this synthetic dataset, used for capacity planning."
    )
    table_view(daily, "Daily figures as a table")

    st.subheader("How far it has been tested")
    st.caption('Each value comes from a file a real run left, and reads "not run" until one has.')
    values = {metric.name: metric.value for metric in measured()}
    for cell, (label, name) in zip(st.columns(len(TESTED)), TESTED, strict=True):
        cell.metric(label, values.get(name, NOT_RUN))
