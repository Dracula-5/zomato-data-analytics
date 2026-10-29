"""Schema contracts: the agreed shape of every source file, kept as YAML in `contracts/`.

A contract lists each column's name, type, description and data classification. The
pipeline enforces it at the door (drift detection) and reuses it downstream, for the data
dictionary and for deciding which columns need masking.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ColumnType = Literal["VARCHAR", "BIGINT", "INTEGER", "DOUBLE", "DECIMAL(12,2)", "DATE", "TIMESTAMP"]
Classification = Literal["public", "internal", "pii", "secret"]
SampleBy = Literal["none", "user", "order"]


class Column(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    type: ColumnType = "VARCHAR"
    description: str = Field(min_length=3)
    classification: Classification = "internal"


class PartitionLookup(BaseModel):
    """Take the partition value from a parent table (order_items get their order's date)."""

    model_config = ConfigDict(extra="forbid")

    table: str
    key: str


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    file: str
    kind: Literal["dimension", "fact"]
    description: str = Field(min_length=3)
    has_index_column: bool = False
    primary_key: list[str] = Field(min_length=1)
    partition_by: str | None = None
    partition_lookup: PartitionLookup | None = None
    sample_by: SampleBy = "none"
    columns: list[Column] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"{self.table}: duplicate column names")
        missing_pk = [k for k in self.primary_key if k not in names]
        if missing_pk:
            raise ValueError(f"{self.table}: primary key not in columns: {missing_pk}")
        if self.kind == "fact" and not self.partition_by:
            raise ValueError(f"{self.table}: fact tables need partition_by")
        if self.kind == "dimension" and (self.partition_by or self.sample_by != "none"):
            raise ValueError(f"{self.table}: dimensions are neither partitioned nor sampled")
        if self.partition_by and self.partition_lookup is None and self.partition_by not in names:
            raise ValueError(f"{self.table}: partition column {self.partition_by!r} not found")
        if self.partition_lookup and self.partition_lookup.key not in names:
            raise ValueError(f"{self.table}: lookup key {self.partition_lookup.key!r} not found")
        if self.sample_by == "user" and "user_id" not in names:
            raise ValueError(f"{self.table}: sample_by=user needs a user_id column")
        if self.sample_by == "order" and "order_id" not in names:
            raise ValueError(f"{self.table}: sample_by=order needs an order_id column")
        return self

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def depends_on(self) -> set[str]:
        """Tables that must be written to the lake before this one."""
        deps = {self.partition_lookup.table} if self.partition_lookup else set()
        if self.sample_by == "order":
            deps.add("orders")
        deps.discard(self.table)
        return deps


def load_contract(path: Path) -> Contract:
    return Contract.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_contracts(folder: Path) -> dict[str, Contract]:
    """All contracts in `folder`, ordered so every table comes after its dependencies."""
    contracts = {c.table: c for c in map(load_contract, sorted(folder.glob("*.yml")))}
    if not contracts:
        raise FileNotFoundError(f"no contracts found in {folder}")
    ordered: dict[str, Contract] = {}
    pending = dict(contracts)
    while pending:
        ready = [t for t, c in pending.items() if c.depends_on() <= ordered.keys()]
        if not ready:
            raise ValueError(f"unresolvable contract dependencies: {sorted(pending)}")
        for table in sorted(ready, key=lambda t: (pending[t].kind == "fact", t)):
            ordered[table] = pending.pop(table)
    return ordered
