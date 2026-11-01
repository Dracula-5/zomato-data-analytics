"""Insights: the scheduled digest of what changed, tested against normal variation."""

from datetime import UTC, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from zap.config import get_settings
from zap.dashboard.components import hero, note
from zap.dashboard.theme import INK, series_color, style
from zap.dashboard.ui import page_setup, table_view, theme_mode
from zap.insights import digest as weekly
from zap.llm.client import LlmClient

CITIES_SHOWN = 15

db = page_setup("Insights", "A scheduled look at what changed, tested against normal variation.")
if db is not None:
    mode = theme_mode()
    settings = get_settings()
    folder = settings.artifacts_dir / "digest"

    if st.button("Run the analysis now"):
        with st.spinner("Comparing the last two weeks..."):
            report = weekly.analyse(db)
            report["dataset"] = db.stem
            report["ran_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            report["brief"] = weekly.write_brief(LlmClient(settings), report["findings"])
            weekly.save(report, folder)
        st.toast("Analysis finished.")

    report = weekly.load_latest(folder)
    if report is None:
        st.info("No digest yet. Run it above, or let the daily Airflow job produce one.")
        st.stop()

    period = report["period"]
    moved = [city for city in report["cities"] if city["q"] < weekly.ALPHA]
    hero(
        "Weekly digest",
        f"{len(moved)} of {len(report['cities'])} cities moved beyond noise"
        if moved
        else "Nothing moved beyond noise this week",
        f"{period['first']} to {period['last']}, against the {weekly.DAYS} days before.",
        [
            f"Dataset: {report.get('dataset', 'unknown')}",
            f"Ran {report.get('ran_at', '')[:16].replace('T', ' ')} UTC",
        ],
        "Summary",
        report.get("brief") or report["findings"][0],
    )
    if report.get("brief"):
        st.caption("Every number in the summary above was checked against the findings below.")

    left, right = st.columns([1, 1.25])
    with left:
        st.subheader("Findings")
        for line in report["findings"]:
            note(line)
        st.caption(
            "Each change is tested before it's called one. Most weekly movement in this "
            "SYNTHETIC data is noise, and the digest says so."
        )
    with right:
        shown = report["cities"][:CITIES_SHOWN][::-1]
        scores = [city["z"] for city in shown]
        reach = max(3.5, max(abs(score) for score in scores) * 1.15)
        bars = go.Figure(
            go.Bar(
                x=scores,
                y=[city["city"] for city in shown],
                orientation="h",
                marker={"color": series_color(mode), "cornerradius": 3},
                customdata=[
                    [city["this_week"], city["last_week"], city["change"]] for city in shown
                ],
                hovertemplate=(
                    "%{y}<br>This week: %{customdata[0]:,}<br>Last week: %{customdata[1]:,}"
                    "<br>Change: %{customdata[2]:+.1%}<extra></extra>"
                ),
            )
        )
        bars.add_vrect(
            x0=-weekly.NOTEWORTHY_Z,
            x1=weekly.NOTEWORTHY_Z,
            fillcolor=INK[mode]["muted"],
            opacity=0.14,
            line_width=0,
            layer="below",
        )
        bars.update_layout(
            title="How unusual each city's week was",
            xaxis={"range": [-reach, reach], "title": "Standard deviations from no change"},
        )
        st.plotly_chart(style(bars, mode, height=470), width="stretch")
        st.caption("The shaded band is normal week-to-week variation.")
    table_view(pd.DataFrame(report["cities"]), "All cities tested")
