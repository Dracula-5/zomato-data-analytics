"""`zap stream ...`: the SIMULATED live feed."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Annotated

import typer

from zap.config import get_settings

stream_app = typer.Typer(help="The SIMULATED live feed.", no_args_is_help=True)

DatasetOpt = Annotated[str, typer.Option(help="Warehouse to replay.")]


@stream_app.command("run")
def run_cmd(
    dataset: DatasetOpt = "full",
    days: Annotated[int, typer.Option(help="How many recent days to replay.")] = 14,
    speed: Annotated[int, typer.Option(help="Simulated seconds per real second.")] = 60,
) -> None:
    """Replay recent orders as a live feed until stopped. Free, and writes only its own file."""
    from zap.stream.processor import Processor, load_rates, load_watch
    from zap.stream.runner import START_HOUR, run_live, stop_requested
    from zap.stream.simulator import Simulator, load_orders, replay_days
    from zap.stream.store import Store, stream_path

    settings = get_settings()
    db = settings.duckdb_path(dataset)
    first, last = replay_days(db, days)
    start = datetime(first.year, first.month, first.day) + timedelta(hours=START_HOUR)
    orders = load_orders(db, first, last)
    store = Store(stream_path(settings.warehouse_dir))
    store.reset()
    simulator = Simulator(store, orders)
    processor = Processor(store, load_rates(db, first, days), load_watch(db, first), start)
    typer.echo(f"SIMULATED feed: replaying {first} to {last} at {speed}x. Ctrl+C to stop.")
    try:
        clock = run_live(store, simulator, processor, start, speed, lambda: stop_requested(store))
        typer.echo(f"Stopped at simulated time {clock}")
    finally:
        store.close()


@stream_app.command("stop")
def stop_cmd() -> None:
    """Ask a running feed to stop."""
    from zap.stream.store import Store, stream_path

    store = Store(stream_path(get_settings().warehouse_dir))
    store.set_status(stop="1")
    store.close()
    typer.echo("Stop requested.")


@stream_app.command("eval")
def eval_cmd(
    dataset: DatasetOpt = "full",
    skip_days: Annotated[int, typer.Option(help="Measure an earlier stretch of days.")] = 0,
    name: Annotated[str, typer.Option(help="Report name in artifacts/.")] = "stream_eval",
) -> None:
    """Measure false alarms and detection on SYNTHETIC outages, with no waiting."""
    from zap.stream.runner import evaluate

    settings = get_settings()
    report = evaluate(settings.duckdb_path(dataset), skip_days=skip_days)
    path = settings.artifacts_dir / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    typer.echo(f"{report['events']:,} events, {report['events_per_second']:,} a second")
    typer.echo(f"False alarms: {report['false_alarms']} in {report['replayed_days']} days")
    for share, row in report["by_severity"].items():
        delay = row["median_delay_minutes"]
        typer.echo(
            f"  {share} drop: found {row['found']} of {row['incidents']}, median {delay} min"
        )
    typer.echo(f"Report: {path}")
