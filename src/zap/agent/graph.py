"""The copilot as an explicit state machine, so every path through it can be read and tested.

    route -> plan -> approve (only for expensive SQL) -> execute -> validate -> answer

Nothing reaches the user without passing `validate`. A step the model can't do, because it
isn't available, ends with a plain message rather than a guess.
"""

from __future__ import annotations

import uuid
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from zap.agent import tools, validate
from zap.agent.router import keyword_route, llm_route
from zap.agent.trace import Tracer
from zap.llm.client import LlmError, ReplayMiss

NEEDS_MODEL = (
    "This question needs the language model, which isn't available in this run. "
    "Forecast and graph questions still work."
)
MAX_ROWS_ANSWERED = 10


class State(TypedDict, total=False):
    run_id: str
    question: str
    tool: str
    routed_by: str
    sql: str
    rows: list[dict[str, Any]]
    text: str
    cited: list[str]
    retrieved: list[str]
    source: str
    checks: list[dict[str, Any]]
    answer: str
    stopped: str


def _fmt(key: str, value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return str(value)
    if key == "id" or key.endswith("_id"):
        return f"{value:.0f}"
    if float(value).is_integer():
        return f"{value:,.0f}"
    return f"{value:,.2f}" if abs(value) >= 100 else f"{value:.4g}"


def render_rows(rows: list[dict[str, Any]]) -> str:
    """Tool output as text, written by code, so no number can change on the way to the user."""
    if len(rows) == 1 and len(rows[0]) == 1:
        key, value = next(iter(rows[0].items()))
        return f"The answer is {_fmt(key, value)}."
    shown = rows[:MAX_ROWS_ANSWERED]
    lines = ["; ".join(f"{key}: {_fmt(key, value)}" for key, value in row.items()) for row in shown]
    more = f"\n({len(rows) - len(shown)} more rows.)" if len(rows) > len(shown) else ""
    return "\n".join(lines) + more


class Copilot:
    def __init__(self, context: tools.Context, tracer: Tracer) -> None:
        self.context = context
        builder: Any = StateGraph(State)
        for name, node in (
            ("route", self._route),
            ("plan", self._plan),
            ("approve", self._approve),
            ("execute", self._execute),
            ("validate", self._validate),
            ("answer", self._answer),
        ):
            # `approve` pauses by raising, and resuming reruns it, so it records nothing itself.
            builder.add_node(name, node if name == "approve" else tracer.wrap(name, node))
        builder.add_edge(START, "route")
        builder.add_edge("route", "plan")
        builder.add_conditional_edges("plan", self._after_plan)
        builder.add_conditional_edges("approve", self._after_approve)
        builder.add_edge("execute", "validate")
        builder.add_edge("validate", "answer")
        builder.add_edge("answer", END)
        self.graph: Any = builder.compile(checkpointer=InMemorySaver())

    def _route(self, state: State) -> dict[str, Any]:
        question = state["question"]
        try:
            return {"tool": llm_route(self.context.client, question).tool, "routed_by": "llm"}
        except LlmError:
            # The keyword router is measured too, so this fallback's quality is known.
            return {"tool": keyword_route(question), "routed_by": "keywords"}

    def _plan(self, state: State) -> dict[str, Any]:
        if state["tool"] != "sql":
            return {}
        try:
            sql, error = tools.plan_sql(self.context, state["question"])
        except LlmError:
            return {"stopped": NEEDS_MODEL}
        return {"sql": sql} if sql else {"stopped": f"No safe query was found: {error}"}

    def _after_plan(self, state: State) -> str:
        if state.get("stopped"):
            return "answer"
        return "approve" if tools.is_expensive(state.get("sql", "")) else "execute"

    def _approve(self, state: State) -> dict[str, Any]:
        approved = interrupt({"question": state["question"], "sql": state["sql"]})
        return {} if approved else {"stopped": "Not run: the query was declined."}

    def _after_approve(self, state: State) -> str:
        return "answer" if state.get("stopped") else "execute"

    def _execute(self, state: State) -> dict[str, Any]:
        tool, question = state["tool"], state["question"]
        try:
            if tool == "sql":
                result = tools.run_sql(self.context, state["sql"])
            elif tool == "rag":
                result = tools.run_rag(self.context, question)
            elif tool == "forecast":
                result = tools.run_forecast(self.context, question)
            else:
                result = tools.run_graph(self.context, question)
        except (ReplayMiss, LlmError):
            return {"stopped": NEEDS_MODEL}
        except Exception as err:  # any tool failure becomes a message, not a crash
            return {"stopped": f"The {tool} tool failed: {str(err)[:200]}"}
        return {
            "rows": result.rows,
            "text": result.text,
            "cited": result.cited,
            "retrieved": result.retrieved,
            "source": result.source,
        }

    def _validate(self, state: State) -> dict[str, Any]:
        if state.get("stopped"):
            return {"checks": []}
        rows = state.get("rows", [])
        draft = state.get("text") or render_rows(rows)
        checks = [validate.check_not_empty(rows)]
        if state["tool"] == "sql":
            checks.append(validate.check_sql_passes_guard(state["sql"]))
        checks.append(validate.check_numbers(draft, validate.values_in(rows)))
        if state["tool"] == "rag":
            checks.append(
                validate.check_citations(state.get("cited", []), state.get("retrieved", []))
            )
        return {"checks": [vars(check) for check in checks]}

    def _answer(self, state: State) -> dict[str, Any]:
        if state.get("stopped"):
            return {"answer": state["stopped"]}
        failed = [c for c in state.get("checks", []) if not c["passed"]]
        if failed:
            reasons = "; ".join(f"{c['name']}: {c['detail']}" for c in failed)
            return {"answer": f"This answer failed its checks, so it isn't shown. {reasons}"}
        rows = state.get("rows", [])
        return {"answer": state.get("text") or render_rows(rows)}

    def _result(
        self, config: dict[str, Any], run_id: str, output: dict[str, Any]
    ) -> dict[str, Any]:
        paused = output.get("__interrupt__")
        return {
            **output,
            "run_id": run_id,
            "needs_approval": bool(paused),
            "pending": paused[0].value if paused else None,
            "config": config,
        }

    def ask(self, question: str) -> dict[str, Any]:
        run_id = uuid.uuid4().hex[:12]
        config: dict[str, Any] = {"configurable": {"thread_id": run_id}}
        output = self.graph.invoke({"run_id": run_id, "question": question}, config)
        return self._result(config, run_id, output)

    def resume(self, run_id: str, approved: bool) -> dict[str, Any]:
        config: dict[str, Any] = {"configurable": {"thread_id": run_id}}
        output = self.graph.invoke(Command(resume=approved), config)
        return self._result(config, run_id, output)
