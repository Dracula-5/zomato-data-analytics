"""Chart builders that more than one page needs."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from zap.dashboard.theme import Mode, series_color, style

AVERAGE_DAYS = 7


def trend(
    frame: pd.DataFrame, measure: str, title: str, mode: Mode, prefix: str = "", height: int = 340
) -> go.Figure:
    """Daily values behind a 7-day average.

    Demand has a strong weekly cycle, so a plain daily line fills in as a solid band and hides
    the trend. The average shows the trend, and the faint line keeps the real values.
    """
    color = series_color(mode)
    ordered = frame.sort_values("order_date")
    fig = go.Figure()
    fig.add_scatter(
        x=ordered["order_date"],
        y=ordered[measure],
        name="Daily",
        mode="lines",
        opacity=0.25,
        line={"width": 1, "color": color},
        hovertemplate=f"Daily: {prefix}%{{y:,.0f}}<extra></extra>",
    )
    fig.add_scatter(
        x=ordered["order_date"],
        y=ordered[measure].rolling(AVERAGE_DAYS).mean(),
        name=f"{AVERAGE_DAYS}-day average",
        mode="lines",
        line={"width": 2.5, "color": color},
        hovertemplate=f"{AVERAGE_DAYS}-day average: {prefix}%{{y:,.0f}}<extra></extra>",
    )
    fig.update_layout(title=title, hovermode="x unified", xaxis={"hoverformat": "%a %d %b %Y"})
    return style(fig, mode, height=height)
