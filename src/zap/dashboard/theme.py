"""The look of the dashboard: one validated palette, with separate light and dark steps.

Rules followed (see docs/LEARNING_NOTES.md, M3):
* one hue per job: categorical slots in a fixed order, a single orange ramp for magnitude;
* never two y-axes; orders and GMV get separate charts;
* thin 2px lines, recessive grid, text in neutral ink (never in the series colour);
* hover tooltips on every chart (Plotly's default, with formatted templates).

The slot order passes scripts/validate_palette.js (from the dataviz guide) in both modes.
"""

from __future__ import annotations

from typing import Literal

import plotly.graph_objects as go

Mode = Literal["light", "dark"]

# Surfaces and ink (text never uses a series colour).
INK: dict[Mode, dict[str, str]] = {
    "light": {"surface": "#ffffff", "text": "#1c1917", "muted": "#6b635a", "grid": "#ece6de"},
    "dark": {"surface": "#1c1917", "text": "#f5f1eb", "muted": "#a8a095", "grid": "#2e2925"},
}
# Categorical slots in their validated, fixed order (identity follows the entity).
SERIES: dict[Mode, list[str]] = {
    "light": [
        "#eb6834",
        "#2a78d6",
        "#1baf7a",
        "#eda100",
        "#e87ba4",
        "#008300",
        "#6250d6",
        "#e34948",
    ],
    "dark": [
        "#d95926",
        "#3987e5",
        "#199e70",
        "#c98500",
        "#d55181",
        "#008300",
        "#9085e9",
        "#e66767",
    ],
}
# One-hue orange ramp for magnitude, from "near zero" (recedes into the surface) to "most".
SEQUENTIAL: dict[Mode, list[str]] = {
    "light": ["#fdebe1", "#fbd0b5", "#f6a97d", "#eb6834", "#c44d1c", "#933710", "#5f2208"],
    "dark": ["#3b1a0b", "#5f2a12", "#8f3f1a", "#c95a26", "#eb7a45", "#f6a97d", "#fcd7bf"],
}
# Reserved for state, never for a series.
STATUS: dict[Mode, dict[str, str]] = {
    "light": {"good": "#0f7b5a", "bad": "#c4281c"},
    "dark": {"good": "#34c28f", "bad": "#f26d62"},
}
LEGEND_CHAR_PX = 7


def series_color(mode: Mode, slot: int = 0) -> str:
    return SERIES[mode][slot % len(SERIES[mode])]


def sequential_scale(mode: Mode) -> list[list[float | str]]:
    steps = SEQUENTIAL[mode]
    return [[i / (len(steps) - 1), c] for i, c in enumerate(steps)]


def page_css(mode: Mode) -> str:
    """Cards, banners and spacing for the viewer's theme.

    Colours Streamlit can set itself live in config.toml. This is the part it can't, written once
    per mode from the same ink as the charts.
    """
    ink = INK[mode]
    status = STATUS[mode]
    shadow = "0 1px 2px rgba(28, 25, 23, 0.05), 0 4px 16px rgba(28, 25, 23, 0.05)"
    lift = shadow if mode == "light" else "none"
    card = (
        f"background: {ink['surface']}; border: 1px solid {ink['grid']}; "
        f"border-radius: 14px; box-shadow: {lift};"
    )
    return f"""<style>
[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stMainBlockContainer"] {{
  max-width: 1360px; padding: 2.2rem 2.6rem 3.5rem;
}}
h1 {{
  font-size: 1.85rem !important; font-weight: 700 !important; letter-spacing: -0.025em;
  padding: 0 !important;
}}
h2, h3 {{ font-size: 1.12rem !important; font-weight: 650 !important; letter-spacing: -0.012em; }}
[data-testid="stMetric"] {{ {card} padding: 0.85rem 1rem; height: 100%; }}
[data-testid="stMetricLabel"] {{ color: {ink["muted"]}; }}
[data-testid="stMetricValue"] {{ font-size: 1.6rem; font-weight: 700; letter-spacing: -0.02em; }}
[data-testid="stPlotlyChart"] {{ {card} overflow: hidden; }}
[data-testid="stChatMessage"] {{ {card} padding: 0.9rem 1rem; }}
[data-testid="stExpander"] details {{ border-radius: 12px; background: {ink["surface"]}; }}
[data-testid="stDataFrame"] {{ border-radius: 12px; overflow: hidden; }}
[data-testid="stSidebar"] hr {{ opacity: 0.2; }}

.zap-hero {{
  display: flex; flex-wrap: wrap; gap: 1.6rem 3rem; align-items: stretch; color: #fff8f1;
  background: linear-gradient(115deg, #1c1917 0%, #2c1d15 58%, #8a3412 135%);
  border-radius: 18px; padding: 1.7rem 2rem; margin-bottom: 0.4rem;
}}
.zap-hero-main {{ flex: 1 1 420px; min-width: 0; }}
.zap-hero-aside {{
  flex: 1 1 340px; min-width: 0; border-left: 1px solid rgba(255, 248, 241, 0.16);
  padding-left: 2rem; font-size: 0.95rem; line-height: 1.5; color: #f3e6da;
}}
.zap-eyebrow {{
  font-size: 0.72rem; font-weight: 700; letter-spacing: 0.09em; text-transform: uppercase;
  color: #fdba74; margin-bottom: 0.45rem;
}}
.zap-headline {{
  font-size: 1.85rem; line-height: 1.2; font-weight: 700; letter-spacing: -0.025em;
}}
.zap-detail {{ margin-top: 0.4rem; color: #d6c8bb; font-size: 0.95rem; }}
.zap-pills {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.9rem; }}
.zap-pill {{
  font-size: 0.74rem; font-weight: 600; padding: 0.18rem 0.6rem; border-radius: 999px;
  background: rgba(255, 248, 241, 0.11); color: #ffedd9; white-space: nowrap;
}}
.zap-dot {{
  display: inline-block; width: 0.55rem; height: 0.55rem; border-radius: 50%;
  margin-right: 0.4rem; background: {status["good"]};
  animation: zap-pulse 1.6s ease-in-out infinite;
}}
.zap-dot-off {{ background: #8a8177; animation: none; }}
@keyframes zap-pulse {{ 50% {{ opacity: 0.25; }} }}
.zap-note {{
  {card} padding: 0.7rem 0.95rem; margin-bottom: 0.5rem; font-size: 0.9rem; line-height: 1.45;
}}
.zap-note small {{ display: block; color: {ink["muted"]}; font-size: 0.76rem; }}
.zap-stat {{ font-size: 1.3rem; font-weight: 700; letter-spacing: -0.02em; line-height: 1.25; }}
.zap-note-bad {{ border-left: 3px solid {status["bad"]}; }}
.zap-note-good {{ border-left: 3px solid {status["good"]}; }}
.zap-feed {{
  font-size: 0.8rem; color: #e7e0d8; padding: 0.5rem 0.65rem; border-radius: 10px;
  background: rgba(255, 248, 241, 0.07);
}}
.zap-feed small {{ display: block; color: #a8a095; margin-top: 0.1rem; }}
</style>"""


def style(fig: go.Figure, mode: Mode, height: int = 340) -> go.Figure:
    """Apply surface, ink, grid and spacing; charts keep their own data colours."""
    ink = INK[mode]
    names = [trace.name for trace in fig.data if trace.name]
    fig.update_layout(
        height=height,
        margin={"l": 16, "r": 28, "t": 52, "b": 20},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": ink["text"], "size": 13},
        title={
            "font": {"size": 15, "color": ink["text"]},
            "x": 0,
            "xanchor": "left",
            "pad": {"l": 16},
        },
        hoverlabel={"bgcolor": ink["surface"], "font": {"color": ink["text"]}},
        legend={
            "orientation": "h",
            "x": 1,
            "xanchor": "right",
            "y": 1.02,
            "yanchor": "bottom",
            "font": {"color": ink["muted"]},
            # Plotly measures text before the page font loads, which clips the last entry.
            "entrywidth": max(map(len, names), default=0) * LEGEND_CHAR_PX or None,
        },
        bargap=0.3,
    )
    fig.update_xaxes(
        showgrid=False,
        zeroline=False,
        linecolor=ink["grid"],
        tickfont={"color": ink["muted"]},
        title={"font": {"color": ink["muted"]}},
    )
    fig.update_yaxes(
        gridcolor=ink["grid"],
        zeroline=False,
        showline=False,
        tickfont={"color": ink["muted"]},
        title={"font": {"color": ink["muted"]}},
    )
    return fig
