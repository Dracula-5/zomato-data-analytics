"""Copilot: a chat that picks a tool, checks its answer, and asks before costly queries."""

import streamlit as st

from zap.agent.graph import Copilot
from zap.agent.tools import Context
from zap.agent.trace import Tracer, read_trace
from zap.config import get_settings
from zap.dashboard.ui import page_setup
from zap.llm.client import LlmClient

TRACE_NAME = "agent/trace.jsonl"
TOOL_NAMES = {
    "sql": "Warehouse SQL",
    "rag": "Project documents",
    "forecast": "Forecasts",
    "graph": "Knowledge graph",
}
EXAMPLES = (
    "How many orders were placed with each payment method?",
    "What does the rule ORD_AMOUNT_MISMATCH check?",
    "Which forecasting model has the lowest error?",
    "Which restaurant chains did the graph find?",
)


@st.cache_resource(show_spinner=False)
def build_copilot() -> Copilot:
    settings = get_settings()
    context = Context(settings, LlmClient(settings))
    return Copilot(context, Tracer(settings.artifacts_dir / TRACE_NAME))


def show(result: dict) -> None:  # type: ignore[type-arg]
    tool = result.get("tool")
    routed_by = "keyword router" if result.get("routed_by") == "keywords" else "LLM router"
    st.badge(TOOL_NAMES.get(tool, str(tool)), icon=":material/build:")
    st.caption(f"Chosen by the {routed_by}.")
    if result.get("sql"):
        with st.expander("The SQL", expanded=bool(result.get("needs_approval"))):
            st.code(result["sql"], language="sql")
    if result.get("needs_approval"):
        return
    rows = result.get("rows") or []
    checks = result.get("checks", [])
    passed = bool(checks) and all(check["passed"] for check in checks)
    if passed and len(rows) > 1 and not result.get("text"):
        st.dataframe(rows, hide_index=True, width="stretch")
    else:
        st.markdown(result.get("answer", "").replace("$", "\\$"))  # two $ would become a formula
    if checks:
        states = (
            f"{'passed' if check['passed'] else 'FAILED'}: {check['name'].replace('_', ' ')}"
            for check in checks
        )
        st.caption("Checks. " + "; ".join(states))
    if result.get("source"):
        st.caption(f"Source: {result['source']}")
    with st.expander("Trace of this answer"):
        settings = get_settings()
        st.json(read_trace(settings.artifacts_dir / TRACE_NAME, result["run_id"]), expanded=False)


db = page_setup(
    "Copilot",
    "Ask in plain English. It picks a tool, checks the answer against the tool's output, "
    "and asks you before running a costly query.",
)
if db is not None:
    settings = get_settings()
    if not settings.duckdb_path("full").exists():
        st.info("The copilot uses the full dataset. Build it with `make local-full`.")
        st.stop()
    copilot = build_copilot()
    spent = copilot.context.client.spent_usd()
    budget = settings.llm_budget_usd
    if settings.llm_mode == "live":
        st.caption(
            f"Live model: a new question makes paid calls, and a repeat is free. "
            f"\\${spent:.4f} of \\${budget:.2f} used. It stops at the limit."
        )
    else:
        st.caption(
            "Replay mode, so no paid calls. Forecast and graph questions work, and questions "
            "already answered once replay from the saved answers."
        )

    history: list[dict] = st.session_state.setdefault("copilot_history", [])  # type: ignore[type-arg]
    if st.sidebar.button("New chat", disabled=not history, width="stretch"):
        history.clear()
        st.rerun()

    question = st.chat_input("Ask a question")
    if not history:
        st.write("Try one of these:")
        for cell, example in zip(st.columns(len(EXAMPLES)), EXAMPLES, strict=True):
            if cell.button(example, width="stretch"):
                question = example
    if question:
        with st.spinner("Working on it..."):
            history.append({"question": question, "result": copilot.ask(question)})
        st.rerun()

    for turn in history:
        with st.chat_message("user"):
            st.write(turn["question"])
        with st.chat_message("assistant"):
            show(turn["result"])

    if history and history[-1]["result"].get("needs_approval"):
        last = history[-1]
        st.warning("This query scans millions of rows, so it needs your approval.")
        yes, no = st.columns(2)
        if yes.button("Approve", type="primary", width="stretch"):
            with st.spinner("Running the query..."):
                last["result"] = copilot.resume(last["result"]["run_id"], True)
            st.rerun()
        if no.button("Deny", width="stretch"):
            last["result"] = copilot.resume(last["result"]["run_id"], False)
            st.rerun()
