"""The schema the model sees: real columns from the warehouse, meanings from the dbt docs."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb

from zap.text2sql.guard import ALLOWED_TABLES

# Results written by Python, so they have no dbt docs.
EXTRA_DESCRIPTIONS = {
    "forecast.accuracy": "Backtest error of each forecasting model, by metric and scope. "
    "metric is orders or gmv; scope is National or a city; model is seasonal_naive, ets or "
    "lightgbm; mape and smape are percentages, lower is better.",
    "forecast.next_days": "Forecast for the 14 days after the data ends, for each metric, "
    "scope and model.",
}


def _model_docs(manifest_path: Path) -> dict[str, tuple[str, dict[str, str]]]:
    """{"schema.table": (description, {column: description})} from dbt's manifest."""
    if not manifest_path.exists():
        return {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    docs = {}
    for node in manifest["nodes"].values():
        if node["resource_type"] == "model":
            columns = {name: c.get("description", "") for name, c in node["columns"].items()}
            docs[f"{node['schema']}.{node['name']}"] = (node.get("description", ""), columns)
    return docs


def schema_prompt(
    db_path: Path, manifest_path: Path, allowed: frozenset[str] = ALLOWED_TABLES
) -> str:
    """One block per allowed table that exists: its meaning, then `column TYPE: meaning`."""
    docs = _model_docs(manifest_path)
    con = duckdb.connect(str(db_path), read_only=True, config={"python_enable_replacements": False})
    try:
        rows = con.execute(
            "select table_schema, table_name, column_name, data_type "
            "from information_schema.columns "
            "order by table_schema, table_name, ordinal_position"
        ).fetchall()
    finally:
        con.close()
    blocks: dict[str, list[str]] = {}
    for schema, table, column, dtype in rows:
        full = f"{schema}.{table}"
        if full not in allowed or column.startswith("_"):
            continue
        description, columns = docs.get(full, (EXTRA_DESCRIPTIONS.get(full, ""), {}))
        lines = blocks.setdefault(full, [f"{full}: {' '.join(description.split())}".strip()])
        meaning = " ".join(columns.get(column, "").split())
        lines.append(f"  {column} {dtype}" + (f": {meaning}" if meaning else ""))
    return "\n\n".join("\n".join(lines) for _, lines in sorted(blocks.items()))
