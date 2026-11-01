"""AI evaluation: what was measured, on what data, and what hasn't been run yet."""

import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from zap.config import REPO_ROOT
from zap.dashboard.theme import series_color, style
from zap.dashboard.ui import measured, page_setup, theme_mode
from zap.metrics.collect import NOT_RUN

GROUPS = ("AI evaluation", "Live feed", "LLM cost", "Recommender audit")
HEADLINES = (
    ("Text-to-SQL accuracy", "Text-to-SQL execution accuracy"),
    ("RAG faithfulness", "RAG faithfulness"),
    ("LLM router", "Routing accuracy, LLM router"),
    ("Keyword router", "Routing accuracy, keyword baseline"),
    ("Spent on the API", "Total spent"),
)
RETRIEVAL = REPO_ROOT / "eval" / "results" / "rag_retrieval.json"
STAGES = {
    "bm25": "BM25",
    "dense": "Dense",
    "hybrid": "Hybrid",
    "hybrid_rerank": "Hybrid + rerank",
}

if page_setup("AI Evaluation", "What was measured, on what data, and at what cost.") is not None:
    mode = theme_mode()
    metrics = measured()
    values = {metric.name: metric.value for metric in metrics}
    st.caption(
        'Every value comes from a file left by a real run. "not run" means no run has produced '
        "it yet."
    )
    for cell, (label, name) in zip(st.columns(len(HEADLINES)), HEADLINES, strict=True):
        cell.metric(label, values.get(name, NOT_RUN))

    if RETRIEVAL.exists():
        modes = json.loads(RETRIEVAL.read_text(encoding="utf-8"))["modes"]
        shown = [stage for stage in STAGES if stage in modes][::-1]
        charts = st.columns(2)
        for chart, measure, title in (
            (charts[0], "recall_at_5", "Recall@5 by search stage"),
            (charts[1], "mrr", "MRR by search stage"),
        ):
            scores = [modes[stage][measure] for stage in shown]
            fig = go.Figure(
                go.Bar(
                    x=scores,
                    y=[STAGES[stage] for stage in shown],
                    orientation="h",
                    marker={"color": series_color(mode), "cornerradius": 4},
                    text=[f"{score:.3f}" for score in scores],
                    textposition="outside",
                    hovertemplate="%{y}<br>%{x:.3f}<extra></extra>",
                )
            )
            fig.update_layout(title=title, xaxis={"range": [0, 1.1]})
            chart.plotly_chart(style(fig, mode, height=260), width="stretch")

    for tab, group in zip(st.tabs(GROUPS), GROUPS, strict=True):
        rows = [m for m in metrics if m.group == group]
        waiting = sum(m.value == NOT_RUN for m in rows)
        with tab:
            if waiting:
                st.caption(f"{waiting} of {len(rows)} not run yet.")
            table = pd.DataFrame(
                {
                    "Metric": [m.name for m in rows],
                    "Value": [m.value for m in rows],
                    "Data": [m.data for m in rows],
                    "Source": [m.source for m in rows],
                }
            )
            st.dataframe(
                table,
                hide_index=True,
                width="stretch",
                height="content",
                column_config={"Value": st.column_config.TextColumn(width="small")},
            )
