"""Checks written in code, because a model shouldn't mark its own work.

A failed check blocks the answer. Every number in an answer must trace back to the tool's output,
so a figure the model invented can't reach the user, however confident it sounds.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from zap.text2sql.guard import GuardError, check_sql

NUMBER = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?%?")
# Small counts and ordinals read naturally in prose: "the top 5", "2 chains".
SMALL = 10


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


def numbers_in(text: str) -> list[float]:
    found = []
    for token in NUMBER.findall(text):
        value = float(token.rstrip("%").replace(",", ""))
        found.append(value)
    return found


def _digits(value: float) -> int:
    text = f"{value:.10f}".rstrip("0")
    return len(text.split(".")[1])


def _matches(claimed: float, source: float) -> bool:
    """Equal, or the same number at the answer's precision, or the same as a percentage."""
    places = _digits(claimed)
    return any(round(candidate, places) == claimed for candidate in (source, source * 100))


def check_numbers(answer: str, sources: Iterable[float]) -> Check:
    known = list(sources)
    unknown = [
        value
        for value in numbers_in(answer)
        if abs(value) > SMALL and not any(_matches(value, source) for source in known)
    ]
    if unknown:
        return Check(
            "numbers_trace_to_tool_output", False, f"not in the tool output: {unknown[:5]}"
        )
    return Check("numbers_trace_to_tool_output", True)


def values_in(rows: list[dict[str, Any]]) -> list[float]:
    values: list[float] = []
    for row in rows:
        for value in row.values():
            if isinstance(value, int | float) and not isinstance(value, bool):
                values.append(float(value))
            elif isinstance(value, str):
                values.extend(numbers_in(value))
    return values


def check_sql_passes_guard(sql: str) -> Check:
    try:
        check_sql(sql)
    except GuardError as err:
        return Check("sql_passed_the_guard", False, str(err)[:200])
    return Check("sql_passed_the_guard", True)


def check_not_empty(rows: list[dict[str, Any]]) -> Check:
    if rows:
        return Check("result_is_not_empty", True)
    return Check("result_is_not_empty", False, "the query returned no rows")


def check_citations(cited: list[str], retrieved: list[str]) -> Check:
    unknown = sorted(set(cited) - set(retrieved))
    if unknown:
        return Check("citations_were_retrieved", False, f"not retrieved: {unknown[:5]}")
    return Check("citations_were_retrieved", True)
