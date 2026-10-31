"""City performance: GMV, AOV and cancel rate by city over a chosen date range."""

import plotly.graph_objects as go
import streamlit as st

from zap.dashboard.theme import series_color, style
from zap.dashboard.ui import load, page_setup, table_view, theme_mode

db = page_setup("Cities")
if db is not None:
    mode = theme_mode()
    k = load("kpi_summary", db)
    start, end = st.date_input(
        "Date range",
        value=(k["first_day"], k["last_day"]),
        min_value=k["first_day"],
        max_value=k["last_day"],
    )
    cities = load("city_summary", db, start, end)
    top = cities.head(15).iloc[::-1]  # largest at the top of a horizontal bar chart

    fig = go.Figure(
        go.Bar(
            x=top["gmv"],
            y=top["city"],
            orientation="h",
            marker={"color": series_color(mode), "cornerradius": 4},
            text=[f"₹{v / 1e5:,.1f} L" for v in top["gmv"]],
            textposition="outside",
            hovertemplate="%{y}<br>GMV: ₹%{x:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(title="Top 15 cities by GMV", xaxis_title="GMV (₹)")
    st.plotly_chart(style(fig, mode, height=480), width="stretch")
    table_view(cities, "All cities as a table")

    city = st.selectbox("City trend", cities["city"].tolist())
    trend = load("city_daily", db, city)
    line = go.Figure(
        go.Scatter(
            x=trend["order_date"],
            y=trend["orders"],
            mode="lines",
            line={"width": 2, "color": series_color(mode)},
            hovertemplate="%{x|%d %b %Y}<br>Orders: %{y:,}<extra></extra>",
        )
    )
    line.update_layout(title=f"Orders per day, {city}", hovermode="x unified")
    st.plotly_chart(style(line, mode), width="stretch")
