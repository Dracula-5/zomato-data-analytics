"""City performance: GMV, AOV and cancel rate by city over a chosen date range."""

import plotly.graph_objects as go
import streamlit as st

from zap.dashboard.charts import city_map, trend
from zap.dashboard.theme import series_color, style
from zap.dashboard.ui import load, page_setup, table_view, theme_mode

db = page_setup("Cities", "Which cities drive GMV, over any date range.")
if db is not None:
    mode = theme_mode()
    k = load("kpi_summary", db)
    picker, _ = st.columns([1, 2])
    days = picker.date_input(
        "Date range",
        value=(k["first_day"], k["last_day"]),
        min_value=k["first_day"],
        max_value=k["last_day"],
    )
    if len(days) != 2:  # the picker reruns the page after the first click of a range
        st.info("Now pick the end date.")
        st.stop()
    start, end = days
    cities = load("city_summary", db, start, end)
    if cities.empty:
        st.info("No orders in this range.")
        st.stop()
    top = cities.head(15).iloc[::-1]  # largest at the top of a horizontal bar chart

    tiles = st.columns(3)
    tiles[0].metric("Cities with orders", f"{len(cities):,}")
    tiles[1].metric("Largest city", cities["city"].iloc[0])
    tiles[2].metric(
        "Top 15 cities' share of GMV", f"{cities['gmv'].head(15).sum() / cities['gmv'].sum():.1%}"
    )

    fig = go.Figure(
        go.Bar(
            x=top["gmv"],
            y=top["city"],
            orientation="h",
            marker={"color": series_color(mode), "cornerradius": 4},
            text=[f"₹{v / 1e7:,.1f} Cr" for v in top["gmv"]],
            textposition="outside",
            hovertemplate="%{y}<br>GMV: ₹%{x:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(title="Top 15 cities by GMV", xaxis_title="GMV (₹)")
    fig.update_xaxes(range=[0, float(top["gmv"].max()) * 1.12])  # room for the labels
    left, right = st.columns([1.25, 1])
    left.plotly_chart(style(fig, mode, height=480), width="stretch")
    right.plotly_chart(city_map(cities, "gmv", "GMV on the map", mode, height=480), width="stretch")
    table_view(cities, "All cities as a table")

    st.subheader("One city over time")
    picker, _ = st.columns([1, 2])
    city = picker.selectbox("City", cities["city"].tolist())
    daily = load("city_daily", db, city)
    st.plotly_chart(trend(daily, "orders", f"Orders per day, {city}", mode), width="stretch")
