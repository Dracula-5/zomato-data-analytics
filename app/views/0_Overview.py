"""Overview: headline KPIs, the demand trend, when people order, and what has been tested."""

import plotly.express as px
import streamlit as st

from zap.dashboard.charts import trend
from zap.dashboard.theme import sequential_scale, style
from zap.dashboard.ui import load, measured, page_setup, table_view, theme_mode
from zap.metrics.collect import NOT_RUN

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WINDOW_DAYS = 28
# What each tile is called here, and the metric's name in docs/METRICS.md.
TESTED = (
    ("dbt tests", "dbt tests"),
    ("National forecast error", "National orders MAPE, ETS"),
    ("Text-to-SQL accuracy", "Text-to-SQL execution accuracy"),
    ("RAG faithfulness", "RAG faithfulness"),
    ("SQL attacks refused", "SQL attacks refused"),
)

db = page_setup("Overview", "How the business is doing, and how far the platform has been tested.")
if db is not None:
    mode = theme_mode()
    k = load("kpi_summary", db)
    st.caption(f"Orders from {k['first_day']:%d %b %Y} to {k['last_day']:%d %b %Y}.")

    tiles = st.columns(6)
    tiles[0].metric(
        "Orders", f"{k['orders'] / 1e6:.2f}M" if k["orders"] > 1e6 else f"{k['orders']:,}"
    )
    tiles[1].metric("GMV (delivered)", f"₹{k['gmv'] / 1e7:,.1f} Cr")
    tiles[2].metric("Avg order value", f"₹{k['aov']:,.0f}")
    tiles[3].metric("Cancel rate", f"{k['cancel_rate']:.1%}")
    tiles[4].metric(
        "SLA breaches", f"{k['sla_breach_rate']:.1%}", help="Delivered later than 40 min."
    )
    tiles[5].metric("Active customers", f"{k['customers']:,}")

    daily = load("daily_kpis", db)
    left, right = st.columns(2)
    left.plotly_chart(trend(daily, "orders", "Orders per day", mode), width="stretch")
    right.plotly_chart(trend(daily, "gmv", "GMV per day (₹)", mode, prefix="₹"), width="stretch")
    table_view(daily)

    st.subheader("What stands out")
    grid = load("weekday_hour", db)
    orders = daily.sort_values("order_date")["orders"]
    growth = orders.tail(WINDOW_DAYS).mean() / orders.head(WINDOW_DAYS).mean()
    busiest = grid.loc[grid["orders"].idxmax()]
    cities = load("city_summary", db, k["first_day"], k["last_day"])
    leader = cities.iloc[0]
    cells = st.columns(3)
    cells[0].metric(
        "Daily orders now vs at the start",
        f"{growth:.1f}x",
        help=f"Average of the last {WINDOW_DAYS} days over the first {WINDOW_DAYS}.",
    )
    cells[1].metric(
        "Busiest hour of the week",
        f"{WEEKDAYS[int(busiest['weekday']) - 1]} {int(busiest['hour']):02d}:00",
        help=f"{int(busiest['orders']):,} orders across the whole period.",
    )
    cells[2].metric(
        "Top city by GMV",
        leader["city"],
        help=f"{leader['gmv'] / cities['gmv'].sum():.1%} of delivered GMV.",
    )

    st.subheader("When do people order?")
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
    st.plotly_chart(style(heat, mode, height=300), width="stretch")
    st.caption(
        "Demand peaks at lunch (13:00) and dinner (19:00-20:00) and is higher at weekends: "
        "the main real signal in this synthetic dataset, used for capacity planning."
    )
    table_view(grid)

    st.subheader("How far it has been tested")
    st.caption('Each value comes from a file a real run left, and reads "not run" until one has.')
    values = {metric.name: metric.value for metric in measured()}
    for cell, (label, name) in zip(st.columns(len(TESTED)), TESTED, strict=True):
        cell.metric(label, values.get(name, NOT_RUN))
