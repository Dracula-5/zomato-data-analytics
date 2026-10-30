"""`zap graph ...`: the M7 commands. The driver is imported inside each command, so the rest
of the project (including the Airflow image) never needs it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from zap.config import get_settings

graph_app = typer.Typer(help="Neo4j knowledge graph (milestone M7).", no_args_is_help=True)

DatasetOpt = Annotated[str, typer.Option(help="Warehouse to load: orders come from this dataset.")]
ResetOpt = Annotated[bool, typer.Option(help="Delete everything in the graph first.")]


def _warehouse(dataset: str) -> Path:
    db_path = get_settings().duckdb_path(dataset)
    if not db_path.exists():
        typer.echo(f"ERROR: no warehouse for dataset '{dataset}' ({db_path})", err=True)
        raise typer.Exit(code=1)
    return db_path


def _load(driver: Any, db_path: Path, reset: bool) -> None:
    from zap.kg.load_neo4j import load_graph

    report = load_graph(driver, db_path, reset=reset)
    path = get_settings().artifacts_dir / "graph_load.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.as_dict(), indent=2) + "\n", encoding="utf-8", newline="\n")
    for step in report.steps:
        mark = "ok" if step.ok else "MISMATCH"
        typer.echo(f"  {step.step:34} {step.in_graph:>10,} in {step.seconds:7.1f}s  {mark}")
    typer.echo(f"Loaded in {report.seconds}s. Report: {path}")
    if not all(step.ok for step in report.steps):
        typer.echo("ERROR: the graph doesn't match the rows sent.", err=True)
        raise typer.Exit(code=1)


def _analyze(driver: Any, db_path: Path) -> None:
    from zap.kg.analysis import run_analysis

    settings = get_settings()
    full = settings.duckdb_path("full")
    report = run_analysis(driver, db_path, settings.artifacts_dir, full if full.exists() else None)
    signal = report["signal"]
    communities = report["communities"]
    pairs = report["co_ordered"]
    typer.echo(
        f"Restaurants with a menu: {signal['share_of_restaurants_with_a_menu']:.1%}; "
        f"where one exists, {signal['share_of_those_on_the_menu']:.1%} of lines are on it"
    )
    typer.echo(
        f"Communities of 3+: {communities['communities_of_3_or_more']:,}, name purity "
        f"{communities['name_purity']:.1%} "
        f"(shuffled {communities['name_purity_if_shuffled']:.1%}), "
        f"multi-brand groups {communities['multi_brand_groups']}"
    )
    typer.echo(
        f"Dish pairs: {pairs['naive_significant_pairs']:,} across all orders, "
        f"{pairs['significant_within_a_restaurant']:,} within a restaurant"
    )
    checks = report["full_data_checks"]
    source = checks["cancellations"] if isinstance(checks, dict) else report["cancellations"]
    for entity in ("restaurants", "users"):
        result = source[entity]
        typer.echo(
            f"Cancel-heavy {entity}: {result['raw_p_below_alpha']:,} by raw p, "
            f"{result['after_fdr']:,} after FDR, of {result['tested']:,} tested"
        )
    typer.echo(f"Report: {settings.artifacts_dir / 'graph_metrics.json'}")


@graph_app.command("load")
def load_cmd(dataset: DatasetOpt = "sample_5pct", reset: ResetOpt = False) -> None:
    """Load the warehouse into Neo4j, then prove the counts match."""
    from zap.kg.load_neo4j import connect

    db_path = _warehouse(dataset)
    with connect(get_settings()) as driver:
        _load(driver, db_path, reset)


@graph_app.command("analyze")
def analyze_cmd(dataset: DatasetOpt = "sample_5pct") -> None:
    """Run GDS and the queries, then publish the results for the dashboard."""
    from zap.kg.load_neo4j import connect

    db_path = _warehouse(dataset)
    with connect(get_settings()) as driver:
        _analyze(driver, db_path)


@graph_app.command("all")
def all_cmd(dataset: DatasetOpt = "sample_5pct", reset: ResetOpt = False) -> None:
    """Load, then analyze."""
    from zap.kg.load_neo4j import connect

    db_path = _warehouse(dataset)
    with connect(get_settings()) as driver:
        _load(driver, db_path, reset)
        _analyze(driver, db_path)
