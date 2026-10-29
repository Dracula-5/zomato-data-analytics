"""Delivery SLA: p50/p90 delivery time and breach rate by city and hour of day."""

import plotly.express as px
import streamlit as st

from zap.dashboard.theme import sequential_scale, style
from zap.dashboard.ui import load, page_setup, table_view, theme_mode

db = page_setup("Delivery SLA")
if db is not None:
    mode = theme_mode()
    measure = st.radio(
        "Measure",
        ["p90_delivery_min", "p50_delivery_min", "sla_breach_rate"],
        horizontal=True,
        format_func=lambda m: {
            "p90_delivery_min": "p90 minutes",
            "p50_delivery_min": "Median minutes",
            "sla_breach_rate": "Share later than 40 min",
        }[m],
    )
    sla = load("delivery_sla", db, 12)
    pivot = sla.pivot(index="city", columns="order_hour", values=measure)
    fmt = ".1%" if measure == "sla_breach_rate" else ".1f"
    heat = px.imshow(
        pivot,
        aspect="auto",
        color_continuous_scale=sequential_scale(mode),
        labels={"x": "Hour of day", "y": "", "color": ""},
    )
    heat.update_traces(
        hovertemplate=f"%{{y}} at %{{x}}:00<br>%{{z:{fmt}}}<extra></extra>", xgap=2, ygap=2
    )
    heat.update_layout(title="Busiest 12 cities by hour of day")
    st.plotly_chart(style(heat, mode, height=420), use_container_width=True)
    spread = sla[measure].max() - sla[measure].min()
    st.info(
        f"Spread across all city-hour cells: {spread:{fmt}}. In this SYNTHETIC dataset delivery "
        "time does not depend on city or hour, so there is no SLA hot-spot to find. The mart "
        "and this view are built to surface one in real data."
    )
    table_view(sla)
