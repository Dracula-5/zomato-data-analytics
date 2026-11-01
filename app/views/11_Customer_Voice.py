"""Customer Voice: what the reviews say, as labelled by the LLM."""

import plotly.graph_objects as go
import streamlit as st

from zap.dashboard.theme import INK, series_color, style
from zap.dashboard.ui import load, page_setup, theme_mode

SENTIMENTS = ("negative", "neutral", "positive")

db = page_setup("Customer Voice", "What reviews talk about, and how they feel about it.")
if db is not None:
    if not load("has_review_labels", db):
        st.info(
            "No review labels for this dataset. They come from `zap ai enrich`, on the full data."
        )
        st.stop()

    mode = theme_mode()
    topics = load("topic_sentiment", db)
    total = int(topics["reviews"].sum())
    by_feeling = topics.groupby("sentiment")["reviews"].sum()
    tiles = st.columns(3)
    tiles[0].metric("Reviews labelled", f"{total:,}")
    tiles[1].metric("Positive", f"{by_feeling.get('positive', 0) / total:.1%}")
    tiles[2].metric("Negative", f"{by_feeling.get('negative', 0) / total:.1%}")

    pivot = topics.pivot(index="topic", columns="sentiment", values="reviews").fillna(0)
    pivot = pivot.reindex(columns=list(SENTIMENTS), fill_value=0)
    shares = pivot.div(pivot.sum(axis=1), axis=0).sort_values("negative")
    colours = {
        "negative": series_color(mode, 0),
        "neutral": INK[mode]["muted"],
        "positive": series_color(mode, 1),
    }
    left, right = st.columns([1.3, 1])
    with left:
        figure = go.Figure()
        for feeling in SENTIMENTS:
            figure.add_bar(
                x=shares[feeling],
                y=[topic.capitalize() for topic in shares.index],
                orientation="h",
                name=feeling.capitalize(),
                marker={
                    "color": colours[feeling],
                    "line": {"width": 2, "color": INK[mode]["surface"]},
                },
                customdata=pivot.loc[shares.index, feeling],
                hovertemplate="%{y}: %{x:.1%}<br>%{customdata:,.0f} reviews<extra></extra>",
            )
        figure.update_layout(
            barmode="stack", title="Feeling by topic", xaxis={"tickformat": ".0%", "range": [0, 1]}
        )
        st.plotly_chart(style(figure, mode, height=400), width="stretch")
    with right:
        st.subheader("Most common complaints")
        st.dataframe(
            load("review_issues", db),
            hide_index=True,
            width="stretch",
            column_config={
                "key_issue": "Complaint",
                "topic": "Topic",
                "reviews": st.column_config.NumberColumn("Reviews", format="localized"),
            },
        )
    st.caption(
        "An LLM labelled these from the review text alone, without seeing the rating. The "
        "300,000 reviews are 294 repeated SYNTHETIC sentences, so this shows the pipeline "
        "working, not what real customers think."
    )
