"""From a question to a checked result: generate, guard, run, and repair once on failure."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd
from pydantic import BaseModel, Field

from zap.llm.client import LlmClient
from zap.text2sql.guard import GuardError, check_sql, run_readonly

ATTEMPTS = 2  # the first try and one repair
PURPOSE = "text2sql"

RULES = """You write one DuckDB SELECT statement that answers the question.
Use only the tables below, always with their schema, like marts.fct_orders.
Prefer the marts that already hold a measure over recomputing it from fct_orders.
GMV means the sales amount of delivered orders. Order data runs from 2024-01-01 to 2026-06-30.
Return only the columns the question asks for, plus any label needed to read them.
Don't round numbers unless the question asks for it.

Tables:
"""


class SqlDraft(BaseModel):
    sql: str = Field(description="One DuckDB SELECT statement.")


@dataclass
class SqlAnswer:
    question: str
    sql: str = ""
    rows: pd.DataFrame | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.rows is not None

    @property
    def attempts(self) -> int:
        return len(self.errors) + (1 if self.ok else 0)


def draft_sql(client: LlmClient, schema: str, prompt: str) -> str:
    return client.complete_as(RULES + schema, prompt, PURPOSE, SqlDraft, attempts=1).sql


def answer_question(client: LlmClient, db_path: Path, schema: str, question: str) -> SqlAnswer:
    """Every draft goes through the guard, so a repair can't skip the safety check."""
    answer = SqlAnswer(question)
    prompt = question
    for _ in range(ATTEMPTS):
        answer.sql = draft_sql(client, schema, prompt)
        try:
            answer.rows = run_readonly(db_path, check_sql(answer.sql))
            return answer
        except (GuardError, duckdb.Error) as err:
            reason = str(err)[:400]
            answer.errors.append(reason)
            prompt = f"{question}\n\nThis SQL failed:\n{answer.sql}\n\nThe error:\n{reason}"
    return answer
