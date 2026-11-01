"""Restaurant leaderboard within a city."""

import plotly.graph_objects as go
import streamlit as st

from zap.dashboard.theme import series_color, style
from zap.dashboard.ui import load, page_setup, theme_mode

db = page_setup("Restaurants", "The leaderboard within a city, by GMV.")
if db is not None:
    mode = theme_mode()
    k = load("kpi_summary", db)
    cities = load("city_summary", db, k["first_day"], k["last_day"])["city"].tolist()
    left, right = st.columns(2)
    city = left.selectbox("City", cities)
    n = right.slider("How many restaurants", 5, 50, 15)
    top = load("top_restaurants", db, city, n)

    shown = top.iloc[::-1]
    fig = go.Figure(
        go.Bar(
            x=shown["gmv"],
            y=shown["restaurant_name"],
            orientation="h",
            marker={"color": series_color(mode), "cornerradius": 4},
            customdata=shown[["orders", "aov", "cancel_rate_pct"]],
            hovertemplate=(
                "%{y}<br>GMV: ₹%{x:,.0f}<br>Orders: %{customdata[0]:,}"
                "<br>AOV: ₹%{customdata[1]:,.0f}"
                "<br>Cancel rate: %{customdata[2]:.2f}%<extra></extra>"
            ),
        )
    )
    fig.update_layout(title=f"Top {n} restaurants by GMV in {city}", xaxis_title="GMV (₹)")
    st.plotly_chart(style(fig, mode, height=max(320, 26 * len(shown))), width="stretch")
    st.dataframe(
        top,
        hide_index=True,
        width="stretch",
        column_config={
            "rank": "Rank",
            "restaurant_name": "Restaurant",
            "orders": st.column_config.NumberColumn("Orders", format="localized"),
            "gmv": st.column_config.NumberColumn("GMV (₹)", format="localized"),
            "aov": st.column_config.NumberColumn("AOV (₹)", format="%.0f"),
            "cancel_rate_pct": st.column_config.NumberColumn("Cancel rate", format="%.2f%%"),
        },
    )
