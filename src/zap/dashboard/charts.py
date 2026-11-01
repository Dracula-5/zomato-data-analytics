"""Chart builders that more than one page needs."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from zap.dashboard.geo import CITY_CENTRES
from zap.dashboard.theme import INK, Mode, series_color, style

AVERAGE_DAYS = 7
MAP_MAX_BUBBLE = 46
MAP_LABELS = 6
LABEL_SIDES = {"Bangalore": "middle left", "Chennai": "middle right"}  # close together


def _tint(color: str, alpha: float) -> str:
    red, green, blue = (int(color[i : i + 2], 16) for i in (1, 3, 5))
    return f"rgba({red},{green},{blue},{alpha})"


def trend(
    frame: pd.DataFrame, measure: str, title: str, mode: Mode, prefix: str = "", height: int = 340
) -> go.Figure:
    """A 7-day average inside the range of its week.

    Demand has a strong weekly cycle, so a plain daily line fills in as a solid band and hides
    the trend. The line shows the trend, and the shading shows how far a week swings around it.
    """
    color = series_color(mode)
    ordered = frame.sort_values("order_date")
    rolling = ordered[measure].rolling(AVERAGE_DAYS)
    fig = go.Figure()
    fig.add_scatter(
        x=ordered["order_date"],
        y=rolling.max(),
        mode="lines",
        line={"width": 0},
        showlegend=False,
        hovertemplate=f"Weekly high: {prefix}%{{y:,.0f}}<extra></extra>",
    )
    fig.add_scatter(
        x=ordered["order_date"],
        y=rolling.min(),
        name="Range in the week",
        mode="lines",
        line={"width": 0},
        fill="tonexty",
        fillcolor=_tint(color, 0.16),
        hovertemplate=f"Weekly low: {prefix}%{{y:,.0f}}<extra></extra>",
    )
    fig.add_scatter(
        x=ordered["order_date"],
        y=rolling.mean(),
        name=f"{AVERAGE_DAYS}-day average",
        mode="lines",
        line={"width": 2.5, "color": color},
        hovertemplate=f"{AVERAGE_DAYS}-day average: {prefix}%{{y:,.0f}}<extra></extra>",
    )
    fig.update_layout(title=title, hovermode="x unified", xaxis={"hoverformat": "%a %d %b %Y"})
    return style(fig, mode, height=height)


def live_window(frame: pd.DataFrame, title: str, mode: Mode, height: int = 340) -> go.Figure:
    """What the detector sees: the rolling count against the range it treats as normal."""
    color = series_color(mode)
    muted = INK[mode]["muted"]
    fig = go.Figure()
    fig.add_scatter(
        x=frame["minute"],
        y=frame["high"],
        mode="lines",
        line={"width": 0},
        showlegend=False,
        hoverinfo="skip",
    )
    fig.add_scatter(
        x=frame["minute"],
        y=frame["low"],
        name="Normal range",
        mode="lines",
        line={"width": 0},
        fill="tonexty",
        fillcolor=_tint(muted, 0.16),
        hoverinfo="skip",
    )
    fig.add_scatter(
        x=frame["minute"],
        y=frame["expected"],
        name="Expected",
        mode="lines",
        line={"width": 1.5, "color": muted, "dash": "dot"},
        hovertemplate="Expected: %{y:,.0f}<extra></extra>",
    )
    fig.add_scatter(
        x=frame["minute"],
        y=frame["observed"],
        name="Orders",
        mode="lines",
        line={"width": 2.5, "color": color},
        hovertemplate="Orders: %{y:,.0f}<extra></extra>",
    )
    fig.update_layout(title=title, hovermode="x unified", uirevision="live")
    return style(fig, mode, height=height)


def city_map(
    frame: pd.DataFrame, measure: str, title: str, mode: Mode, height: int = 420
) -> go.Figure:
    """A bubble per city on a bare coastline, with the largest named.

    No land fill or borders: the bubbles carry the message, and a dashboard shouldn't be the place
    that decides where a border runs.
    """
    ink = INK[mode]
    placed = frame[frame["city"].isin(CITY_CENTRES)].sort_values(measure, ascending=False)
    largest = float(placed[measure].max()) if len(placed) else 1.0
    fig = go.Figure(
        go.Scattergeo(
            lat=[CITY_CENTRES[city][0] for city in placed["city"]],
            lon=[CITY_CENTRES[city][1] for city in placed["city"]],
            hovertext=placed["city"],
            text=[city if rank < MAP_LABELS else "" for rank, city in enumerate(placed["city"])],
            textposition=[LABEL_SIDES.get(city, "top center") for city in placed["city"]],
            textfont={"size": 11, "color": ink["text"]},
            customdata=placed[measure],
            mode="markers+text",
            marker={
                "size": placed[measure] / (largest or 1.0) * MAP_MAX_BUBBLE,
                "sizemin": 4,
                "color": _tint(series_color(mode), 0.72),
                "line": {"width": 1, "color": ink["surface"]},
            },
            hovertemplate="%{hovertext}<br>%{customdata:,.0f}<extra></extra>",
        )
    )
    fig.update_layout(title=title)
    fig.update_geos(
        lataxis_range=[6, 34],
        lonaxis_range=[67, 95],
        projection_type="mercator",
        bgcolor="rgba(0,0,0,0)",
        showland=False,
        showcountries=False,
        showcoastlines=True,
        coastlinecolor=ink["muted"],
        coastlinewidth=0.6,
        showframe=False,
        showocean=False,
        showlakes=False,
    )
    return style(fig, mode, height=height)
