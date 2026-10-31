"""AI evaluation: what was measured, on what data, and what hasn't been run yet."""

import pandas as pd
import streamlit as st

from zap.config import REPO_ROOT, get_settings
from zap.dashboard.ui import page_setup
from zap.metrics.collect import NOT_RUN, collect

GROUPS = ("AI evaluation", "LLM cost", "Recommender audit")

if page_setup("AI Evaluation") is not None:
    settings = get_settings()
    metrics = collect(
        settings.artifacts_dir,
        REPO_ROOT / "eval" / "results",
        settings.llm_replay_dir / "spend.jsonl",
    )
    st.caption(
        'Every value comes from a file left by a real run. "not run" means no run has produced '
        "it yet, usually because it needs paid model calls."
    )
    for group in GROUPS:
        rows = [m for m in metrics if m.group == group]
        waiting = sum(m.value == NOT_RUN for m in rows)
        st.subheader(group)
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
        st.dataframe(table, hide_index=True, use_container_width=True)
