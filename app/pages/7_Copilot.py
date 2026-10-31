"""Copilot: ask in plain English. Costly queries wait for a person to approve them."""

import streamlit as st

from zap.agent.graph import Copilot
from zap.agent.tools import Context
from zap.agent.trace import Tracer, read_trace
from zap.config import get_settings
from zap.dashboard.ui import page_setup
from zap.llm.client import LlmClient

TRACE_NAME = "agent/trace.jsonl"


@st.cache_resource(show_spinner=False)
def build_copilot() -> Copilot:
    settings = get_settings()
    context = Context(settings, LlmClient(settings))
    return Copilot(context, Tracer(settings.artifacts_dir / TRACE_NAME))


def show(result: dict) -> None:  # type: ignore[type-arg]
    routed_by = "the keyword router" if result.get("routed_by") == "keywords" else "the LLM"
    st.caption(f"Tool: {result.get('tool')}, chosen by {routed_by}.")
    if result.get("sql"):
        st.code(result["sql"], language="sql")
    if result.get("needs_approval"):
        return
    rows = result.get("rows") or []
    checks = result.get("checks", [])
    passed = bool(checks) and all(check["passed"] for check in checks)
    if passed and len(rows) > 1 and not result.get("text"):
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.markdown(result.get("answer", ""))
    for check in checks:
        st.write(("Passed: " if check["passed"] else "Failed: ") + check["name"].replace("_", " "))
    if result.get("source"):
        st.caption(f"Source: {result['source']}")
    with st.expander("Trace of this answer"):
        settings = get_settings()
        st.json(read_trace(settings.artifacts_dir / TRACE_NAME, result["run_id"]), expanded=False)


db = page_setup("Copilot")
if db is not None:
    settings = get_settings()
    if not settings.duckdb_path("full").exists():
        st.info("The copilot uses the full dataset. Build it with `make local-full`.")
        st.stop()
    if settings.llm_mode != "live":
        st.caption(
            "No paid calls in this mode. Forecast and graph questions work, and questions "
            "already answered once replay from the saved answers."
        )
    copilot = build_copilot()
    question = st.text_input(
        "Ask a question", placeholder="Which restaurant chains did the graph find?"
    )
    if st.button("Ask", type="primary", disabled=not question):
        st.session_state["copilot_result"] = copilot.ask(question)

    result = st.session_state.get("copilot_result")
    if result:
        show(result)
        if result.get("needs_approval"):
            st.warning("This query scans millions of rows, so it needs your approval.")
            yes, no = st.columns(2)
            if yes.button("Approve"):
                st.session_state["copilot_result"] = copilot.resume(result["run_id"], True)
                st.rerun()
            if no.button("Deny"):
                st.session_state["copilot_result"] = copilot.resume(result["run_id"], False)
                st.rerun()
