"""Claims in the public documents have to trace back to a measured result."""

import re

from helpers import REPO_ROOT

METRICS = REPO_ROOT / "docs" / "METRICS.md"
BULLETS = REPO_ROOT / "RESUME_BULLETS.md"
NUMBER = re.compile(r"(?<![\w.])\d+(?:,\d{3})*(?:\.\d+)?%?(?![\w])")
ADRS = sorted((REPO_ROOT / "docs" / "adr").glob("*.md"))
LINK = re.compile(r"\]\((?!http)([^)#]+)(?:#[^)]*)?\)")


def test_every_number_in_the_resume_bullets_is_in_the_metrics() -> None:
    measured = set(NUMBER.findall(METRICS.read_text(encoding="utf-8")))
    bullets = BULLETS.read_text(encoding="utf-8").split("## Bullets", 1)[1].split("## ", 1)[0]
    claimed = set(NUMBER.findall(bullets))
    assert claimed, "the bullets should quote measured results"
    assert claimed <= measured, f"not in METRICS.md: {sorted(claimed - measured)}"


def test_the_metrics_warn_about_the_data() -> None:
    text = METRICS.read_text(encoding="utf-8")
    assert "synthetic-looking" in text and "SYNTHETIC" in text and "not run" in text


def test_there_are_at_least_five_decisions_recorded() -> None:
    assert len(ADRS) >= 5
    for path in ADRS:
        text = path.read_text(encoding="utf-8")
        assert "## Context" in text and "## Decision" in text and "## Consequences" in text


def test_links_between_the_documents_work() -> None:
    documents = [REPO_ROOT / "README.md", *sorted((REPO_ROOT / "docs").glob("*.md")), *ADRS]
    for document in documents:
        for target in LINK.findall(document.read_text(encoding="utf-8")):
            assert (document.parent / target).exists(), f"{document.name} links to {target}"
