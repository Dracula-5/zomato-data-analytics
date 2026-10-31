"""Score generated SQL by its results, because many different queries are equally right.

An answer passes when it has the same rows as the gold query. Every gold column must appear
under any name; extra label columns are fine; numbers are compared to two decimal places; and
row order only matters when the question asks for a ranking.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from zap.llm.client import LlmClient
from zap.text2sql.generate import answer_question
from zap.text2sql.guard import check_sql, run_readonly

Row = tuple[Any, ...]


@dataclass(frozen=True)
class GoldQuestion:
    id: str
    level: str
    question: str
    gold_sql: str
    ordered: bool = False


def load_golden(path: Path) -> list[GoldQuestion]:
    entries = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [GoldQuestion(**entry) for entry in entries]


def normal_value(value: Any) -> Any:
    """One form for each kind of value, so 1, 1.0 and Decimal('1.00') all compare equal."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    if value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float | Decimal):
        return round(float(value), 2) + 0.0  # + 0.0 turns -0.0 into 0.0
    if isinstance(value, datetime | pd.Timestamp):
        return value.date().isoformat() if value == value.replace(hour=0, minute=0) else str(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip() if isinstance(value, str) else value


def _column(frame: pd.DataFrame, index: int) -> list[Any]:
    return [normal_value(v) for v in frame.iloc[:, index].tolist()]


def matching_columns(gold: pd.DataFrame, answer: pd.DataFrame) -> list[int] | None:
    """For each gold column, the answer column with the same values, or None if one has none."""
    available = list(range(answer.shape[1]))
    chosen = []
    for index in range(gold.shape[1]):
        wanted = Counter(_column(gold, index))
        match = next((c for c in available if Counter(_column(answer, c)) == wanted), None)
        if match is None:
            return None
        available.remove(match)
        chosen.append(match)
    return chosen


def rows_match(gold: pd.DataFrame, answer: pd.DataFrame, ordered: bool) -> bool:
    if len(gold) != len(answer):
        return False
    chosen = matching_columns(gold, answer)
    if chosen is None:
        return False
    gold_rows = [
        tuple(row) for row in zip(*(_column(gold, i) for i in range(gold.shape[1])), strict=True)
    ]
    got_rows = [tuple(row) for row in zip(*(_column(answer, i) for i in chosen), strict=True)]
    return gold_rows == got_rows if ordered else Counter(gold_rows) == Counter(got_rows)


def run_gold(db_path: Path, questions: list[GoldQuestion]) -> dict[str, pd.DataFrame]:
    """Gold queries pass the same guard, so a question can't depend on something forbidden."""
    return {q.id: run_readonly(db_path, check_sql(q.gold_sql)) for q in questions}


def evaluate(
    client: LlmClient, db_path: Path, schema: str, questions: list[GoldQuestion]
) -> dict[str, Any]:
    gold = run_gold(db_path, questions)
    rows = []
    for question in questions:
        answer = answer_question(client, db_path, schema, question.question)
        correct = answer.rows is not None and rows_match(
            gold[question.id], answer.rows, question.ordered
        )
        rows.append(
            {
                "id": question.id,
                "level": question.level,
                "question": question.question,
                "correct": correct,
                "ran": answer.ok,
                "attempts": answer.attempts,
                "sql": answer.sql,
                "errors": answer.errors,
            }
        )
    frame = pd.DataFrame(rows)
    levels = frame.groupby("level")["correct"].agg(["sum", "count"])
    return {
        "questions": len(frame),
        "execution_accuracy": round(float(frame["correct"].mean()), 4),
        "ran_without_error": round(float(frame["ran"].mean()), 4),
        "needed_a_repair": int((frame["attempts"] > 1).sum()),
        "by_level": {
            str(level): {"correct": int(row["sum"]), "questions": int(row["count"])}
            for level, row in levels.iterrows()
        },
        "results": rows,
    }
