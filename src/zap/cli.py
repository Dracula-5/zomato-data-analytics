"""`zap` command-line interface. The Makefile is a thin wrapper around these commands."""

from pathlib import Path
from typing import Annotated

import duckdb
import typer

from zap.config import get_settings
from zap.fixtures import FixtureSpec, generate_fixture
from zap.ingest import sample
from zap.ingest.contracts import load_contracts
from zap.ingest.drift import append_drift_log, check_drift
from zap.ingest.lake import DriftError, ingest, new_run_id, write_manifest

app = typer.Typer(help="Zomato AI Data Platform CLI.", no_args_is_help=True)

SourceOpt = Annotated[Path | None, typer.Option(help="CSV folder (default: DATA_DIR).")]
TablesOpt = Annotated[
    list[str] | None, typer.Option("--table", "-t", help="Only these tables (repeatable).")
]


def _drift_log() -> Path:
    return get_settings().artifacts_dir / "governance" / "schema_drift_log.jsonl"


@app.command()
def info() -> None:
    """Show the resolved settings (secrets masked)."""
    for name, value in get_settings().redacted().items():
        typer.echo(f"{name:<16} {value}")


@app.command()
def fixtures(
    out: Annotated[Path, typer.Option(help="Folder to write the CSVs to.")] = Path(
        ".cache/fixture"
    ),
    orders: Annotated[int, typer.Option(min=100, help="Number of orders.")] = 5_000,
    seed: Annotated[int, typer.Option(help="RNG seed; same seed gives identical files.")] = 42,
    defects: Annotated[bool, typer.Option(help="Inject the known data defects.")] = True,
) -> None:
    """Generate the SYNTHETIC fixture dataset used by tests, CI and `make demo`."""
    result = generate_fixture(out, FixtureSpec(n_orders=orders, seed=seed, defects=defects))
    typer.echo(f"Synthetic fixture written to {out}")
    for name, n in result.rows.items():
        typer.echo(f"  {name:<12} {n:>8,} rows")
    injected = {k: v for k, v in result.defects.as_dict().items() if v}
    typer.echo(f"  injected defects: {injected or 'none'}")


@app.command("drift-check")
def drift_check(source: SourceOpt = None, table: TablesOpt = None) -> None:
    """Compare source CSVs with their schema contracts. Exits 1 on breaking drift."""
    settings = get_settings()
    src = source or settings.data_dir
    contracts = load_contracts(settings.contracts_dir)
    con = duckdb.connect()
    reports = [check_drift(c, src, con) for t, c in contracts.items() if not table or t in table]
    append_drift_log(reports, _drift_log(), new_run_id())
    for r in reports:
        rows = f"{r.rows:>12,}" if r.rows is not None else f"{'-':>12}"
        typer.echo(f"  {r.table:<12} {r.status:<13} {rows} rows  {r.summary()}")
    if any(r.breaking for r in reports):
        raise typer.Exit(code=1)


@app.command("ingest")
def ingest_cmd(
    source: SourceOpt = None,
    lake: Annotated[
        Path | None, typer.Option(help="Lake root (default: LAKE_DIR/<dataset>).")
    ] = None,
    sample_pct: Annotated[
        float | None, typer.Option(min=0.01, max=100, help="Sample %% (default: SAMPLE_PCT).")
    ] = None,
    dataset: Annotated[str | None, typer.Option(help="Label, e.g. `fixture`.")] = None,
    table: TablesOpt = None,
) -> None:
    """Load source CSVs into the Parquet lake after a schema-drift gate."""
    settings = get_settings()
    pct = settings.sample_pct if sample_pct is None else sample_pct
    label = dataset or sample.label(pct)
    lake_dir = lake or settings.lake_dir / label
    try:
        manifest = ingest(
            load_contracts(settings.contracts_dir),
            source or settings.data_dir,
            lake_dir,
            pct=pct,
            tables=table,
            drift_log=_drift_log(),
            dataset=label,
        )
    except DriftError as err:
        typer.echo(f"ERROR: {err}", err=True)
        raise typer.Exit(code=1) from err
    path = write_manifest(manifest, settings.artifacts_dir)
    typer.echo(f"Ingested dataset '{label}' into {lake_dir} in {manifest.seconds:.1f}s")
    for t in manifest.tables:
        typer.echo(
            f"  {t.table:<12} {t.status:<27} {t.rows_written:>12,} rows "
            f"{t.bytes / 1e6:>9.1f} MB {t.partitions:>5} parts {t.seconds:>7.1f}s"
        )
    typer.echo(f"  total {manifest.rows_written:,} rows, {manifest.bytes_written / 1e6:.1f} MB")
    typer.echo(f"Manifest: {path}")


if __name__ == "__main__":  # pragma: no cover
    app()
