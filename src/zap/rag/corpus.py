"""Turn the project's documents into chunks that each make sense on their own."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

MAX_CHARS = 1200
DOCUMENTS = ("docs/DATA_DICTIONARY.md", "docs/LEARNING_NOTES.md", "docs/CLOUD_SETUP.md")
RULES = "dbt/zomato/seeds/dq_rules.csv"
HEADING = re.compile(r"^(#{1,4})\s+(.+)$")


@dataclass(frozen=True)
class Chunk:
    id: str
    source: str
    title: str
    text: str

    @property
    def searchable(self) -> str:
        """The heading goes in too: a table row means little without knowing its table."""
        return f"{self.title}\n{self.text}"


def _pieces(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split on blank lines, then on lines, so a long table never exceeds the limit."""
    pieces: list[str] = []
    current = ""
    parts: list[str] = []
    for paragraph in text.split("\n\n"):
        parts.extend(paragraph.split("\n") if len(paragraph) > limit else [paragraph])
    for part in parts:
        if current and len(current) + len(part) + 2 > limit:
            pieces.append(current)
            current = ""
        current = f"{current}\n\n{part}" if current else part
    if current.strip():
        pieces.append(current)
    return pieces


def markdown_chunks(path: Path, source: str, limit: int = MAX_CHARS) -> list[Chunk]:
    """One or more chunks per section, titled with the path of headings above it."""
    trail: list[str] = []
    sections: list[tuple[str, list[str]]] = [("", [])]
    in_code = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_code = not in_code
        match = None if in_code else HEADING.match(line)
        if match:
            level = len(match.group(1))
            trail = [*trail[: level - 1], match.group(2).strip()]
            sections.append((" > ".join(trail), []))
        else:
            sections[-1][1].append(line)
    chunks: list[Chunk] = []
    for title, lines in sections:
        for number, piece in enumerate(_pieces("\n".join(lines).strip(), limit)):
            chunks.append(Chunk(f"{source}#{len(chunks)}.{number}", source, title, piece))
    return chunks


def rule_chunks(path: Path) -> list[Chunk]:
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            Chunk(
                f"dq_rules#{row['rule_id']}",
                "dq_rules",
                f"Data quality rule {row['rule_id']}",
                f"{row['description']}. Table: {row['table_name']}. "
                f"Severity: {row['severity']}. Action: {row['action']}.",
            )
            for row in csv.DictReader(handle)
        ]


def build_corpus(root: Path) -> list[Chunk]:
    chunks = [c for name in DOCUMENTS for c in markdown_chunks(root / name, Path(name).stem)]
    return chunks + rule_chunks(root / RULES)
