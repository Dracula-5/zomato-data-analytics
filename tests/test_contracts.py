import csv
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from helpers import CONTRACTS_DIR
from zap.fixtures import HEADERS, FixtureResult
from zap.ingest.contracts import Contract, load_contract, load_contracts

VALID_FACT: dict[str, Any] = {
    "table": "t",
    "file": "t.csv",
    "kind": "fact",
    "description": "test fact",
    "primary_key": ["id"],
    "partition_by": "d",
    "columns": [
        {"name": "id", "type": "BIGINT", "description": "identifier"},
        {"name": "d", "type": "DATE", "description": "date"},
        {"name": "user_id", "type": "BIGINT", "description": "user"},
        {"name": "order_id", "type": "BIGINT", "description": "order"},
    ],
}


def _variant(**changes: Any) -> dict[str, Any]:
    return {**VALID_FACT, **changes}


def test_all_seven_contracts_load_in_dependency_order(contracts: dict[str, Contract]) -> None:
    order = list(contracts)
    assert set(order) == {"restaurant", "food", "menu", "users", "orders", "order_items", "reviews"}
    dims = [t for t in order if contracts[t].kind == "dimension"]
    assert order[: len(dims)] == dims  # dimensions first
    assert order.index("orders") < order.index("order_items")
    assert order.index("orders") < order.index("reviews")


def test_contracts_match_fixture_headers(
    contracts: dict[str, Contract], fixture_data: tuple[Path, FixtureResult]
) -> None:
    """The fixture and the contracts must describe the same files."""
    for table, contract in contracts.items():
        header = HEADERS[table]
        assert contract.has_index_column == (header[0] == "")
        assert contract.column_names == [h for h in header if h]
        with (fixture_data[0] / contract.file).open(encoding="utf-8", newline="") as fh:
            assert next(csv.reader(fh)) == header


def test_personal_data_is_classified(contracts: dict[str, Contract]) -> None:
    users = {c.name: c.classification for c in contracts["users"].columns}
    assert users["password"] == "secret"  # noqa: S105 - a classification label, not a password
    assert users["email"] == "pii"
    assert users["name"] == "pii"


def test_valid_fact_and_dependencies() -> None:
    c = Contract.model_validate(
        _variant(sample_by="order", partition_lookup={"table": "orders", "key": "order_id"})
    )
    assert c.depends_on() == {"orders"}
    assert Contract.model_validate(_variant(sample_by="user")).depends_on() == set()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"partition_by": None}, "need partition_by"),
        ({"primary_key": ["nope"]}, "primary key not in columns"),
        ({"partition_by": "missing"}, "partition column"),
        ({"kind": "dimension"}, "neither partitioned nor sampled"),
        ({"partition_lookup": {"table": "orders", "key": "nope"}}, "lookup key"),
        ({"columns": [*VALID_FACT["columns"], VALID_FACT["columns"][0]]}, "duplicate column"),
        ({"surprise": 1}, "Extra inputs"),
        ({"columns": [{"name": "id", "type": "TEXT", "description": "bad type"}]}, "type"),
    ],
)
def test_invalid_contracts_rejected(changes: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Contract.model_validate(_variant(**changes))


@pytest.mark.parametrize(("sample_by", "drop"), [("user", "user_id"), ("order", "order_id")])
def test_sample_key_required(sample_by: str, drop: str) -> None:
    cols = [c for c in VALID_FACT["columns"] if c["name"] != drop]
    with pytest.raises(ValidationError, match=f"needs an {drop}|needs a {drop}"):
        Contract.model_validate(_variant(sample_by=sample_by, columns=cols))


def test_load_contract_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "t.yml"
    path.write_text(yaml.safe_dump(VALID_FACT), encoding="utf-8")
    assert load_contract(path).table == "t"


def test_empty_folder_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_contracts(tmp_path)


def test_unresolvable_dependency_raises(tmp_path: Path) -> None:
    lookup = {"table": "ghost", "key": "order_id"}
    (tmp_path / "t.yml").write_text(
        yaml.safe_dump(_variant(partition_lookup=lookup)), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="unresolvable"):
        load_contracts(tmp_path)


def test_repo_contracts_folder_exists() -> None:
    assert len(list(CONTRACTS_DIR.glob("*.yml"))) == 7
