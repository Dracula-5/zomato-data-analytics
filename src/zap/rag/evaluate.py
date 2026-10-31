"""Measure retrieval against a golden set whose labels come from a fixed rule.

A chunk counts as relevant when it contains the question's `evidence` phrase, so nobody judges
results by hand after seeing them, and a wording change in the documents shows up as a failure.
"""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from zap.rag.corpus import Chunk
from zap.rag.retrieve import MODES, Retriever


@dataclass(frozen=True)
class GoldQuestion:
    id: str
    kind: str
    question: str
    evidence: str


def load_golden(path: Path) -> list[GoldQuestion]:
    return [GoldQuestion(**entry) for entry in yaml.safe_load(path.read_text(encoding="utf-8"))]


def normal_text(text: str) -> str:
    return " ".join(text.lower().split())


def relevant_chunks(chunks: list[Chunk], evidence: str) -> set[str]:
    phrase = normal_text(evidence)
    return {chunk.id for chunk in chunks if phrase in normal_text(chunk.text)}


def recall_at(ranking: list[str], relevant: set[str], k: int) -> float:
    """1 when a relevant chunk is in the top k. Any one of them can answer the question."""
    return float(bool(set(ranking[:k]) & relevant))


def reciprocal_rank(ranking: list[str], relevant: set[str]) -> float:
    return next((1 / rank for rank, c in enumerate(ranking, start=1) if c in relevant), 0.0)


def check_golden(questions: list[GoldQuestion], chunks: list[Chunk]) -> list[str]:
    """Questions whose evidence is in no chunk: the golden set itself would be broken."""
    return [q.id for q in questions if not relevant_chunks(chunks, q.evidence)]


def evaluate_retrieval(
    retriever: Retriever, questions: list[GoldQuestion], depth: int = 10
) -> dict[str, Any]:
    chunks = list(retriever.chunks.values())
    relevant = {q.id: relevant_chunks(chunks, q.evidence) for q in questions}
    report: dict[str, Any] = {"questions": len(questions), "chunks": len(chunks), "modes": {}}
    misses: dict[str, list[str]] = {}
    for mode in MODES:
        rankings = {q.id: retriever.search(q.question, mode, depth) for q in questions}
        scores: dict[str, Any] = {
            "recall_at_5": sum(recall_at(rankings[q.id], relevant[q.id], 5) for q in questions),
            "mrr": sum(reciprocal_rank(rankings[q.id], relevant[q.id]) for q in questions),
        }
        scores = {name: round(value / len(questions), 4) for name, value in scores.items()}
        for kind in sorted({q.kind for q in questions}):
            subset = [q for q in questions if q.kind == kind]
            hits = sum(recall_at(rankings[q.id], relevant[q.id], 5) for q in subset)
            scores[f"recall_at_5_{kind}"] = round(hits / len(subset), 4)
        report["modes"][mode] = scores
        misses[mode] = [q.id for q in questions if not recall_at(rankings[q.id], relevant[q.id], 5)]
    report["missed"] = misses
    return report


def measure_latency(retriever: Retriever, questions: list[GoldQuestion]) -> dict[str, Any]:
    """Median seconds per search, timed one question at a time rather than divided from a total."""
    timings: dict[str, list[float]] = {"hybrid": [], "hybrid_rerank": []}
    for question in questions:
        for mode, runs in timings.items():
            started = time.perf_counter()
            retriever.search(question.question, mode, 5)
            runs.append(time.perf_counter() - started)
    report: dict[str, Any] = {
        mode: round(statistics.median(runs), 3) for mode, runs in timings.items()
    }
    report["questions"] = len(questions)
    report["added_seconds"] = round(report["hybrid_rerank"] - report["hybrid"], 3)
    return report
