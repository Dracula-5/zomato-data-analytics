"""Data quality: what the rules, Great Expectations, dbt tests and drift checks caught."""

import plotly.graph_objects as go
import streamlit as st

from zap.config import get_settings
from zap.dashboard.theme import series_color, style
from zap.dashboard.ui import load, page_setup, theme_mode

db = page_setup("Data quality", "What each layer of checks caught before the data was used.")
if db is not None:
    mode = theme_mode()
    rules = load("dq_results", db)
    tests = load("dbt_test_summary", db)
    gx = load("gx_results", db)

    tiles = st.columns(5)
    tiles[0].metric("Rules", len(rules))
    tiles[1].metric("Rules that fired", int((rules["rows_failed"] > 0).sum()))
    tiles[2].metric("Records flagged", f"{int(rules['rows_failed'].sum()):,}")
    passed = int(tests.loc[tests["status"] == "pass", "tests"].sum())
    total = int(tests["tests"].sum())
    tiles[3].metric("dbt tests passed", f"{passed}/{total}" if total else "not run")
    tiles[4].metric(
        "GX expectations met", f"{int(gx['success'].sum())}/{len(gx)}" if len(gx) else "not run"
    )

    fired = rules[rules["rows_failed"] > 0].iloc[::-1]
    fig = go.Figure(
        go.Bar(
            x=fired["rows_failed"],
            y=fired["rule_id"],
            orientation="h",
            marker={"color": series_color(mode), "cornerradius": 4},
            text=[f"{v:,}" for v in fired["rows_failed"]],
            textposition="outside",
            customdata=fired[["severity", "action", "description"]],
            hovertemplate=(
                "%{y}<br>Records: %{x:,}<br>Severity: %{customdata[0]}"
                "<br>Action: %{customdata[1]}<br>%{customdata[2]}<extra></extra>"
            ),
        )
    )
    fig.update_layout(title="Records caught by each rule", xaxis_title="Records")
    fig.update_xaxes(range=[0, float(fired["rows_failed"].max()) * 1.12])  # room for the labels
    st.plotly_chart(style(fig, mode, height=max(300, 34 * len(fired))), width="stretch")

    catalogue, expectations, drift, dbt_tests = st.tabs(
        ["Rule catalogue", "Great Expectations", "Schema drift", "dbt tests"]
    )
    with catalogue:
        st.dataframe(
            rules,
            hide_index=True,
            width="stretch",
            column_config={
                "failed_pct": st.column_config.ProgressColumn(
                    "Share failed", format="%.2f%%", min_value=0, max_value=100
                ),
            },
        )
    with expectations:
        st.caption("Runs before loading: an error-level failure stops the pipeline.")
        st.dataframe(gx, hide_index=True, width="stretch")
    with drift:
        st.caption("The latest check per source. Day runs check headers and leave rows empty.")
        log = get_settings().artifacts_dir / "governance" / "schema_drift_log.jsonl"
        st.dataframe(
            load("latest_drift", log) if log.exists() else None, hide_index=True, width="stretch"
        )
    with dbt_tests:
        st.dataframe(tests, hide_index=True, width="stretch")
