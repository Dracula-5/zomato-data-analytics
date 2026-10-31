"""`zap` command-line interface. The Makefile is a thin wrapper around these commands."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import duckdb
import typer

from zap.ai_commands import ai_app
from zap.bench import bench_incremental, bench_queries, write_bench
from zap.cloud.commands import cloud_app
from zap.config import get_settings
from zap.dbt_runner import dbt_target_dir, run_dbt
from zap.dbt_sources import write_sources
from zap.docs_dictionary import write_dictionary
from zap.fixtures import FixtureSpec, generate_fixture
from zap.ingest import sample
from zap.ingest.contracts import load_contracts
from zap.ingest.drift import append_drift_log, check_drift
from zap.ingest.lake import DriftError, ingest, new_run_id, write_manifest
from zap.kg.commands import graph_app
from zap.pipeline import PipelineError, run_local
from zap.quality.gx_suite import GxReport, validate_lake, write_gx_report
from zap.quality.report import build_report
from zap.warehouse.duckdb_loader import load_raw, write_load_log
from zap.warehouse.fingerprint import diff as fingerprint_diff
from zap.warehouse.fingerprint import fingerprint

app = typer.Typer(help="Zomato AI Data Platform CLI.", no_args_is_help=True)
app.add_typer(cloud_app, name="cloud")
app.add_typer(graph_app, name="graph")
app.add_typer(ai_app, name="ai")

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
    day: Annotated[
        datetime | None,
        typer.Option("--date", formats=["%Y-%m-%d"], help="Extract ONE day's fact partitions."),
    ] = None,
    until: Annotated[
        datetime | None,
        typer.Option(formats=["%Y-%m-%d"], help="Only history up to this date (bootstrap)."),
    ] = None,
    skip_drift_check: Annotated[
        bool, typer.Option(help="Drift already checked by a previous step (date runs only).")
    ] = False,
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
            day=day.date() if day else None,
            until=until.date() if until else None,
            skip_drift=skip_drift_check,
        )
    except (DriftError, ValueError) as err:
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


DatasetOpt = Annotated[
    str | None, typer.Option(help="Dataset label (default: from SAMPLE_PCT, e.g. sample_5pct).")
]


def _dataset(dataset: str | None) -> str:
    return dataset or sample.label(get_settings().sample_pct)


@app.command("gen-dbt-sources")
def gen_dbt_sources() -> None:
    """Regenerate the dbt sources YAML from the schema contracts."""
    settings = get_settings()
    path = settings.dbt_project_dir / "models" / "staging" / "_sources.yml"
    changed = write_sources(load_contracts(settings.contracts_dir), path)
    typer.echo(f"{'Updated' if changed else 'Unchanged'}: {path}")


@app.command("load")
def load_cmd(
    dataset: DatasetOpt = None,
    lake: Annotated[
        Path | None, typer.Option(help="Lake root (default: LAKE_DIR/<dataset>).")
    ] = None,
    day: Annotated[
        datetime | None, typer.Option("--date", formats=["%Y-%m-%d"], help="Load one partition.")
    ] = None,
    table: TablesOpt = None,
) -> None:
    """Load the lake into the DuckDB warehouse `raw` schema (full, or one day's partition)."""
    settings = get_settings()
    label = _dataset(dataset)
    result = load_raw(
        load_contracts(settings.contracts_dir),
        lake or settings.lake_dir / label,
        settings.duckdb_path(label),
        tables=table,
        partition_date=day.date() if day else None,
    )
    write_load_log(result, settings.artifacts_dir, label)
    typer.echo(f"Loaded {result.rows:,} rows into {result.database} in {result.seconds:.1f}s")
    for t in result.tables:
        typer.echo(f"  {t.table:<12} {t.mode:<10} {t.rows:>12,} rows {t.seconds:>7.1f}s")


@app.command("dbt", context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def dbt_cmd(ctx: typer.Context, dataset: DatasetOpt = None) -> None:
    """Run dbt against a dataset's warehouse, e.g. `zap dbt --dataset fixture build`."""
    settings = get_settings()
    run = run_dbt(ctx.args, settings.duckdb_path(_dataset(dataset)), settings.dbt_project_dir)
    typer.echo(f"dbt {' '.join(ctx.args)} finished in {run.seconds:.1f}s (exit {run.returncode})")
    raise typer.Exit(code=run.returncode)


@app.command("gx")
def gx_cmd(
    dataset: DatasetOpt = None,
    day: Annotated[
        datetime | None, typer.Option("--date", formats=["%Y-%m-%d"], help="One partition only.")
    ] = None,
) -> None:
    """Run the Great Expectations gate on a dataset's lake. Exits 1 if the gate fails."""
    settings = get_settings()
    label = _dataset(dataset)
    report = validate_lake(
        load_contracts(settings.contracts_dir),
        settings.lake_dir / label,
        label,
        partition_date=day.date() if day else None,
    )
    path = write_gx_report(report, settings.artifacts_dir)
    typer.echo(
        f"GX: {len(report.results)} expectations, {len(report.failed)} failed, "
        f"gate {'PASSED' if report.gate_passed else 'FAILED'} ({report.seconds:.1f}s) -> {path}"
    )
    for r in report.failed:
        typer.echo(f"  [{r.severity}] {r.table}.{r.column} {r.expectation}: {r.unexpected_count}")
    if not report.gate_passed:
        raise typer.Exit(code=1)


@app.command("data-dictionary")
def data_dictionary_cmd(
    out: Annotated[Path, typer.Option(help="Output Markdown file.")] = Path(
        "docs/DATA_DICTIONARY.md"
    ),
    dataset: DatasetOpt = None,
) -> None:
    """Write the data dictionary from the last `dbt docs generate` and the contracts."""
    settings = get_settings()
    target = dbt_target_dir(settings.duckdb_path(_dataset(dataset)), settings.dbt_project_dir)
    path = write_dictionary(target, load_contracts(settings.contracts_dir), out)
    typer.echo(f"Data dictionary written to {path}")


@app.command("bench")
def bench_cmd(
    which: Annotated[str, typer.Argument(help="incremental | query | all")] = "all",
    dataset: DatasetOpt = None,
) -> None:
    """Benchmark full-refresh vs incremental dbt runs, and fact scan vs aggregate mart."""
    settings = get_settings()
    label = _dataset(dataset)
    db_path = settings.duckdb_path(label)
    results = []
    if which in ("incremental", "all"):
        results.append(bench_incremental(db_path, settings.dbt_project_dir))
    if which in ("query", "all"):
        results.append(bench_queries(db_path))
    for result in results:
        path = write_bench(result, settings.artifacts_dir, label)
        typer.echo(json.dumps(result, indent=2))
        typer.echo(f"-> {path}")


@app.command("forecast")
def forecast_cmd(dataset: DatasetOpt = None) -> None:
    """Backtest the forecasters, forecast 14 days ahead, and score the anomaly detector."""
    from zap.forecast.run import run_all

    settings = get_settings()
    label = _dataset(dataset)
    db_path = settings.duckdb_path(label)
    if not db_path.exists():
        typer.echo(f"ERROR: no warehouse for dataset '{label}' ({db_path})", err=True)
        raise typer.Exit(code=1)
    report = run_all(db_path, settings.artifacts_dir)
    for metric, result in report["metrics"].items():
        typer.echo(f"{metric}: best model {result['best_model']}")
        for model, scores in result["models"].items():
            typer.echo(
                f"  {model:15} national MAPE {scores['national_mape']:6.2f}%  "
                f"cities {scores['cities_mean_mape']:6.2f}%  "
                f"gain vs baseline {scores['mape_gain_vs_baseline_pct']:5.1f}%"
            )
    for name, result in report["anomalies"]["detectors"].items():
        typer.echo(
            f"{name}: precision {result['precision']}, recall {result['recall']}, "
            f"{result['false_alarms_per_1000_normal_days']} false alarms per 1,000 normal days"
        )
    typer.echo(f"Report: {settings.artifacts_dir / 'forecast_backtest.json'}")


@app.command("recsys")
def recsys_cmd(dataset: DatasetOpt = "full") -> None:
    """Audit the orders for taste, then score recommenders on a temporal split."""
    from zap.recsys.baselines import run_recsys

    settings = get_settings()
    label = _dataset(dataset)
    db_path = settings.duckdb_path(label)
    if not db_path.exists():
        typer.echo(f"ERROR: no warehouse for dataset '{label}' ({db_path})", err=True)
        raise typer.Exit(code=1)
    report = run_recsys(db_path)
    report["ran_at"] = datetime.now(UTC).isoformat()
    path = settings.artifacts_dir / "recsys_eval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    for name, measure in report["signal_audit"]["measures"].items():
        typer.echo(f"  {name:18} real {measure['real']:.4f}  random {measure['if_random']:.4f}")
    for title in ("real_orders", "shuffled_control", "planted_taste_SYNTHETIC"):
        typer.echo(title)
        for model, scores in report[title]["models"].items():
            typer.echo(
                f"  {model:16} Recall@10 {scores['recall_at_10']:.5f}  "
                f"NDCG@10 {scores['ndcg_at_10']:.5f}  {scores['times_random']:.2f}x random"
            )
    typer.echo(f"Report: {path}")


@app.command("metrics")
def metrics_cmd() -> None:
    """Regenerate docs/METRICS.md from the files that real runs wrote."""
    from zap.config import REPO_ROOT
    from zap.metrics.collect import NOT_RUN, collect
    from zap.metrics.render import write

    settings = get_settings()
    metrics = collect(
        settings.artifacts_dir,
        REPO_ROOT / "eval" / "results",
        settings.llm_replay_dir / "spend.jsonl",
    )
    write(metrics, REPO_ROOT / "docs" / "METRICS.md")
    missing = [metric.name for metric in metrics if metric.value == NOT_RUN]
    typer.echo(f"{len(metrics) - len(missing)} of {len(metrics)} metrics measured")
    for name in missing:
        typer.echo(f"  not run: {name}")


@app.command("dq-report")
def dq_report_cmd(
    dataset: DatasetOpt = None,
    run_results_file: Annotated[
        Path | None,
        typer.Option("--run-results", help="A saved run_results.json (default: the last run)."),
    ] = None,
) -> None:
    """Rebuild the quality report from the last dbt run and the last GX run of a dataset."""
    settings = get_settings()
    label = _dataset(dataset)
    db_path = settings.duckdb_path(label)
    results_path = run_results_file or (
        dbt_target_dir(db_path, settings.dbt_project_dir) / "run_results.json"
    )
    if not results_path.exists():
        typer.echo(f"ERROR: no dbt run for dataset '{label}' ({results_path})", err=True)
        raise typer.Exit(code=1)
    run_results = json.loads(results_path.read_text(encoding="utf-8"))
    gx_path = settings.artifacts_dir / "quality" / label / "gx_results.json"
    gx = GxReport.from_json(gx_path) if gx_path.exists() else None
    report = build_report(
        settings.duckdb_path(label), label, run_results, gx, settings.artifacts_dir
    )
    typer.echo(json.dumps({k: report[k] for k in ("dbt", "gx", "rules")}, indent=2))


@app.command("fingerprint")
def fingerprint_cmd(
    dataset: DatasetOpt = None,
    schema: Annotated[
        list[str] | None, typer.Option("--schema", "-s", help="Schemas (default: marts).")
    ] = None,
    out: Annotated[Path | None, typer.Option(help="Also save the fingerprint here.")] = None,
    compare: Annotated[
        Path | None, typer.Option(help="A saved fingerprint; exit 1 if anything differs.")
    ] = None,
) -> None:
    """Row counts + order-independent checksums of a warehouse (proves two builds match)."""
    settings = get_settings()
    fp = fingerprint(settings.duckdb_path(_dataset(dataset)), tuple(schema or ["marts"]))
    text = json.dumps(fp, indent=2)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8", newline="\n")
    if compare is None:
        typer.echo(text)
        return
    changed = fingerprint_diff(json.loads(compare.read_text(encoding="utf-8")), fp)
    if changed:
        typer.echo(f"DIFFERENT: {', '.join(changed)}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"IDENTICAL: {len(fp)} tables match {compare}")


@app.command("local")
def local_cmd(
    source: SourceOpt = None,
    sample_pct: Annotated[
        float | None, typer.Option(min=0.01, max=100, help="Sample %% (default: SAMPLE_PCT).")
    ] = None,
    dataset: DatasetOpt = None,
    docs: Annotated[bool, typer.Option(help="Also run `dbt docs generate`.")] = True,
) -> None:
    """Run the whole local pipeline: ingest, GX gate, load, dbt build, quality report."""
    settings = get_settings()
    pct = settings.sample_pct if sample_pct is None else sample_pct
    label = dataset or sample.label(pct)
    try:
        run = run_local(
            settings, source or settings.data_dir, label, pct, docs=docs, echo=typer.echo
        )
    except (PipelineError, DriftError) as err:
        typer.echo(f"PIPELINE FAILED: {err}", err=True)
        raise typer.Exit(code=1) from err
    typer.echo(f"Pipeline '{label}' finished in {run.seconds:.1f}s")


if __name__ == "__main__":  # pragma: no cover
    app()
