"""Overview: headline KPIs, daily demand and when people order."""

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from zap.dashboard.theme import sequential_scale, series_color, style
from zap.dashboard.ui import load, page_setup, table_view, theme_mode

db = page_setup("Overview")
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
        "SLA breach rate", f"{k['sla_breach_rate']:.1%}", help="Delivered later than 40 min."
    )
    tiles[5].metric("Active customers", f"{k['customers']:,}")

    daily = load("daily_kpis", db)
    left, right = st.columns(2)
    for col, measure, label, fmt in (
        (left, "orders", "Orders per day", ",.0f"),
        (right, "gmv", "GMV per day (₹)", ",.0f"),
    ):
        fig = go.Figure(
            go.Scatter(
                x=daily["order_date"],
                y=daily[measure],
                mode="lines",
                line={"width": 2, "color": series_color(mode)},
                hovertemplate=f"%{{x|%a %d %b %Y}}<br>{label}: %{{y:{fmt}}}<extra></extra>",
            )
        )
        fig.update_layout(title=label, hovermode="x unified")
        col.plotly_chart(style(fig, mode), use_container_width=True)
    table_view(daily)

    st.subheader("When do people order?")
    grid = load("weekday_hour", db)
    pivot = grid.pivot(index="weekday", columns="hour", values="orders").fillna(0)
    pivot.index = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][: len(pivot.index)]
    heat = px.imshow(
        pivot,
        aspect="auto",
        color_continuous_scale=sequential_scale(mode),
        labels={"x": "Hour of day", "y": "", "color": "Orders"},
    )
    heat.update_traces(
        hovertemplate="%{y} %{x}:00<br>Orders: %{z:,}<extra></extra>", xgap=2, ygap=2
    )
    st.plotly_chart(style(heat, mode, height=300), use_container_width=True)
    st.caption(
        "Demand peaks at lunch (13:00) and dinner (19:00-20:00) and is higher at weekends: "
        "the main real signal in this synthetic dataset, used for capacity planning."
    )
    table_view(grid)
