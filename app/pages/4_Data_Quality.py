"""Data quality: what the rules, Great Expectations, dbt tests and drift checks caught."""

import plotly.graph_objects as go
import streamlit as st

from zap.config import get_settings
from zap.dashboard.theme import series_color, style
from zap.dashboard.ui import load, page_setup, table_view, theme_mode

db = page_setup("Data quality")
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
    st.plotly_chart(style(fig, mode, height=max(300, 34 * len(fired))), use_container_width=True)
    st.subheader("Rule catalogue")
    st.dataframe(rules, hide_index=True, use_container_width=True)

    st.subheader("Great Expectations (pre-load gate)")
    st.dataframe(gx, hide_index=True, use_container_width=True)
    st.subheader("Schema drift (latest check per source)")
    log = get_settings().artifacts_dir / "governance" / "schema_drift_log.jsonl"
    st.dataframe(load("latest_drift", log) if log.exists() else None, hide_index=True)
    table_view(tests, "dbt test results by kind and status")
