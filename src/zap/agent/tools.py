"""The four tools. Forecast and graph read stored results, so they need no LLM at all."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

import pandas as pd

from zap.config import REPO_ROOT, Settings
from zap.dashboard import data
from zap.dbt_runner import dbt_target_dir
from zap.llm.client import LlmClient
from zap.rag.answer import CitedAnswer
from zap.rag.answer import answer_question as rag_answer
from zap.text2sql.generate import draft_sql
from zap.text2sql.guard import check_sql, run_readonly
from zap.text2sql.schema_prompt import schema_prompt

MAX_ROWS_SHOWN = 10
CHAIN_PURITY = 0.8  # the same line the graph analysis uses to call a group a chain
MULTI_BRAND_PURITY = 0.6
# These hold millions of rows. Reading one without a date filter takes real time and memory.
BIG_TABLES = ("marts.fct_order_items", "marts.fct_orders")


@dataclass
class ToolResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    text: str = ""
    cited: list[str] = field(default_factory=list)
    retrieved: list[str] = field(default_factory=list)
    source: str = ""


def _plain(value: Any) -> Any:
    """JSON-friendly values. Every date here is a whole day, so the time adds only noise."""
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    return value.item() if hasattr(value, "item") else value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{str(k): _plain(v) for k, v in row.items()} for row in frame.to_dict("records")]


class Context:
    """What the tools need. Heavy parts load on first use, so simple questions stay quick."""

    def __init__(self, settings: Settings, client: LlmClient) -> None:
        self.settings = settings
        self.client = client
        self.warehouse = settings.duckdb_path("full")
        self.graph_warehouse = data.graph_warehouse(self.warehouse)

    @cached_property
    def schema(self) -> str:
        target = dbt_target_dir(self.warehouse, self.settings.dbt_project_dir)
        return schema_prompt(self.warehouse, target / "manifest.json")

    @cached_property
    def retriever(self) -> Any:
        from zap.rag.corpus import build_corpus
        from zap.rag.retrieve import LocalEmbedder, LocalReranker, Retriever

        cache = Path.home() / ".cache" / "fastembed"
        return Retriever(build_corpus(REPO_ROOT), LocalEmbedder(cache), LocalReranker(cache))


def is_expensive(sql: str) -> bool:
    """A scan of a huge fact table with no date limit needs a person to say yes first."""
    text = sql.lower()
    touches_big_table = any(table in text for table in BIG_TABLES)
    limited = re.search(r"\b(order_date|order_timestamp)\b.*(=|<|>|between)", text) is not None
    return touches_big_table and not limited


def plan_sql(context: Context, question: str) -> tuple[str, str]:
    """Write the SQL and guard it, without running it yet. Returns the safe SQL and any error."""
    feedback = question
    last_error = ""
    for _ in range(2):
        draft = draft_sql(context.client, context.schema, feedback)
        try:
            return check_sql(draft), ""
        except ValueError as err:
            last_error = str(err)[:300]
            feedback = f"{question}\n\nThis SQL was refused:\n{draft}\n\nThe reason:\n{last_error}"
    return "", last_error


def run_sql(context: Context, sql: str) -> ToolResult:
    frame = run_readonly(context.warehouse, sql)
    return ToolResult(rows=_records(frame), source="warehouse")


def run_rag(context: Context, question: str) -> ToolResult:
    answer: CitedAnswer = rag_answer(context.client, context.retriever, question)
    return ToolResult(
        text=answer.text if answer.found else "",
        cited=answer.cited,
        retrieved=answer.retrieved,
        rows=[{"passage": passage} for passage in answer.passages],
        source="documents",
    )


def _scope(context: Context, question: str) -> str:
    scopes = data.forecast_scopes(context.warehouse)
    text = question.lower()
    return next((s for s in scopes if s != "National" and s.lower() in text), "National")


def run_forecast(context: Context, question: str) -> ToolResult:
    text = question.lower()
    metric = "gmv" if "gmv" in text or "revenue" in text else "orders"
    scope = _scope(context, question)
    if re.search(r"anomal|unusual", text):
        flagged = data.real_anomalies(context.warehouse)
        wanted = flagged[flagged["scope"] == scope].head(MAX_ROWS_SHOWN)
        return ToolResult(rows=_records(wanted), source="forecast.anomalies")
    if re.search(r"accura|mape|error|best model", text):
        return ToolResult(
            rows=_records(data.forecast_accuracy(context.warehouse, metric, scope)),
            source="forecast.accuracy",
        )
    model = data.best_forecast_model(context.warehouse, metric)
    lines = data.forecast_lines(context.warehouse, metric, scope, model)
    upcoming = lines[lines["kind"] == "next"][["target_date", "forecast"]].round({"forecast": 0})
    return ToolResult(
        rows=_records(upcoming.assign(metric=metric, scope=scope, model=model)),
        source="forecast.next_days",
    )


def run_graph(context: Context, question: str) -> ToolResult:
    db = context.graph_warehouse
    if db is None:
        raise LookupError("the graph hasn't been built: run `make graph`")
    text = question.lower()
    if re.search(r"recommend", text):
        return ToolResult(
            rows=_records(data.graph_recommendations(db)), source="kg.recommendations"
        )
    if re.search(r"pagerank|central", text):
        top = data.graph_top_restaurants(db).head(MAX_ROWS_SHOWN)
        return ToolResult(rows=_records(top), source="kg.top_restaurants")
    if re.search(r"bought together|co-?order", text):
        return ToolResult(rows=_records(data.graph_outlier_tests(db)), source="kg.outlier_tests")
    communities = data.graph_communities(db)
    if re.search(r"kitchen|virtual|several (brand|name)|same menu", text):
        communities = communities[communities["name_purity"] < MULTI_BRAND_PURITY]
    elif re.search(r"chain", text):
        communities = communities[communities["name_purity"] >= CHAIN_PURITY]
    columns = ["top_name", "restaurants", "name_purity", "cities"]
    return ToolResult(
        rows=_records(communities[columns].head(MAX_ROWS_SHOWN)), source="kg.communities"
    )
