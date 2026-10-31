"""Score routing on a mixed question set, with the keyword router as the baseline."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from zap.agent.router import TOOLS, keyword_route, llm_route
from zap.llm.client import LlmClient, ReplayMiss


@dataclass(frozen=True)
class GoldRoute:
    id: str
    tool: str
    question: str


def load_golden(path: Path) -> list[GoldRoute]:
    return [GoldRoute(**entry) for entry in yaml.safe_load(path.read_text(encoding="utf-8"))]


def score_router(route: Callable[[str], str], questions: list[GoldRoute]) -> dict[str, Any]:
    predicted = {q.id: route(q.question) for q in questions}
    pairs = Counter((q.tool, predicted[q.id]) for q in questions)
    return {
        "accuracy": round(sum(predicted[q.id] == q.tool for q in questions) / len(questions), 4),
        "confusion": {
            actual: {guess: pairs[(actual, guess)] for guess in TOOLS} for actual in TOOLS
        },
        "wrong": [
            {"id": q.id, "expected": q.tool, "got": predicted[q.id], "question": q.question}
            for q in questions
            if predicted[q.id] != q.tool
        ],
    }


def evaluate_routing(client: LlmClient, questions: list[GoldRoute]) -> dict[str, Any]:
    report: dict[str, Any] = {
        "questions": len(questions),
        "by_tool": dict(Counter(q.tool for q in questions)),
        "keyword_router": score_router(keyword_route, questions),
    }
    try:
        report["llm_router"] = score_router(lambda q: llm_route(client, q).tool, questions)
    except ReplayMiss:
        report["llm_router"] = "not run"
    return report
