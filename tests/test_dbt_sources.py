from pathlib import Path

import yaml

from helpers import REPO_ROOT
from zap.dbt_sources import render_sources, write_sources
from zap.ingest.contracts import Contract

COMMITTED = REPO_ROOT / "dbt" / "zomato" / "models" / "staging" / "_sources.yml"


def test_committed_sources_match_contracts(contracts: dict[str, Contract]) -> None:
    """Fails if someone edits a contract without running `zap gen-dbt-sources`."""
    assert COMMITTED.read_text(encoding="utf-8") == render_sources(contracts)


def test_sources_carry_classification_and_freshness(contracts: dict[str, Contract]) -> None:
    doc = yaml.safe_load(render_sources(contracts))
    tables = {t["name"]: t for t in doc["sources"][0]["tables"]}
    users = {c["name"]: c for c in tables["users"]["columns"]}
    assert users["password"]["meta"]["classification"] == "secret"
    assert tables["orders"]["config"]["loaded_at_field"] == "_loaded_at"
    assert "config" not in tables["food"]  # freshness only on facts
    assert tables["order_items"]["columns"][-1]["name"] == "order_date"


def test_write_sources_reports_changes(contracts: dict[str, Contract], tmp_path: Path) -> None:
    path = tmp_path / "sources.yml"
    assert write_sources(contracts, path) is True
    assert write_sources(contracts, path) is False
