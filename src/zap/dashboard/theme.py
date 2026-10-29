"""Chart styling from one validated palette, with separate light and dark steps.

Rules followed (see docs/LEARNING_NOTES.md, M3):
* one hue per job: categorical slots in a fixed order, a single blue ramp for magnitude;
* never two y-axes; orders and GMV get separate charts;
* thin 2px lines, recessive grid, text in neutral ink (never in the series colour);
* hover tooltips on every chart (Plotly's default, with formatted templates).
"""

from __future__ import annotations

from typing import Literal

import plotly.graph_objects as go

Mode = Literal["light", "dark"]

# Surfaces and ink (text never uses a series colour).
INK: dict[Mode, dict[str, str]] = {
    "light": {"surface": "#fcfcfb", "text": "#0b0b0b", "muted": "#52514e", "grid": "#e4e3df"},
    "dark": {"surface": "#1a1a19", "text": "#f0efec", "muted": "#c3c2b7", "grid": "#383835"},
}
# Categorical slots in their validated, fixed order (identity follows the entity).
SERIES: dict[Mode, list[str]] = {
    "light": [
        "#2a78d6",
        "#eb6834",
        "#1baf7a",
        "#eda100",
        "#e87ba4",
        "#008300",
        "#6250d6",
        "#e34948",
    ],
    "dark": [
        "#3987e5",
        "#d95926",
        "#199e70",
        "#c98500",
        "#d55181",
        "#008300",
        "#9085e9",
        "#e66767",
    ],
}
# One-hue blue ramp for magnitude, from "near zero" (recedes into the surface) to "most".
SEQUENTIAL: dict[Mode, list[str]] = {
    "light": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
    "dark": ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"],
}


def series_color(mode: Mode, slot: int = 0) -> str:
    return SERIES[mode][slot % len(SERIES[mode])]


def sequential_scale(mode: Mode) -> list[list[float | str]]:
    steps = SEQUENTIAL[mode]
    return [[i / (len(steps) - 1), c] for i, c in enumerate(steps)]


def style(fig: go.Figure, mode: Mode, height: int = 340) -> go.Figure:
    """Apply surface, ink, grid and spacing; charts keep their own data colours."""
    ink = INK[mode]
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": ink["text"], "size": 13},
        title={"font": {"size": 15, "color": ink["text"]}, "x": 0, "xanchor": "left"},
        hoverlabel={"bgcolor": ink["surface"], "font": {"color": ink["text"]}},
        legend={"orientation": "h", "y": -0.18, "font": {"color": ink["muted"]}},
        bargap=0.25,
    )
    axis = {
        "gridcolor": ink["grid"],
        "zeroline": False,
        "linecolor": ink["grid"],
        "tickfont": {"color": ink["muted"]},
        "title": {"font": {"color": ink["muted"]}},
    }
    fig.update_xaxes(**axis)
    fig.update_yaxes(**axis)
    return fig
