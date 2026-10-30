"""`make local`: the whole batch pipeline on one machine, with every step timed.

    ingest (drift gate) -> GX gate -> load raw -> dbt build -> quality report -> dbt docs

The run summary (`artifacts/pipeline/<dataset>/latest.json`) records each step's wall time,
so the end-to-end runtime in METRICS.md comes from a real run, not an estimate.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from zap.config import Settings
from zap.dbt_runner import run_dbt
from zap.ingest.contracts import load_contracts
from zap.ingest.lake import ingest, write_manifest
from zap.quality.gx_suite import GxReport, validate_lake, write_gx_report
from zap.quality.report import build_report
from zap.warehouse.duckdb_loader import load_raw, write_load_log


class PipelineError(RuntimeError):
    pass


@dataclass
class Step:
    name: str
    seconds: float
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineRun:
    dataset: str
    sample_pct: float
    started_at: str
    steps: list[Step] = field(default_factory=list)
    seconds: float = 0.0
    ok: bool = False

    def write(self, artifacts_dir: Path) -> Path:
        folder = artifacts_dir / "pipeline" / self.dataset
        folder.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(self), indent=2)
        (folder / "latest.json").write_text(payload, encoding="utf-8")
        with (folder / "history.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(self)) + "\n")
        return folder / "latest.json"


def run_local(
    settings: Settings,
    source: Path,
    dataset: str,
    pct: float,
    docs: bool = True,
    echo: Callable[[str], None] = print,
) -> PipelineRun:
    run = PipelineRun(dataset=dataset, sample_pct=pct, started_at=datetime.now(UTC).isoformat())
    t_all = time.perf_counter()
    contracts = load_contracts(settings.contracts_dir)
    lake_dir = settings.lake_dir / dataset
    db_path = settings.duckdb_path(dataset)
    project = settings.dbt_project_dir

    def step(name: str, detail: dict[str, Any], t0: float) -> None:
        run.steps.append(Step(name, round(time.perf_counter() - t0, 3), detail))
        echo(f"[{name}] done in {run.steps[-1].seconds:.1f}s {detail}")

    try:
        t0 = time.perf_counter()
        manifest = ingest(
            contracts,
            source,
            lake_dir,
            pct=pct,
            dataset=dataset,
            drift_log=settings.artifacts_dir / "governance" / "schema_drift_log.jsonl",
        )
        write_manifest(manifest, settings.artifacts_dir)
        step("ingest", {"rows": manifest.rows_written, "bytes": manifest.bytes_written}, t0)

        t0 = time.perf_counter()
        gx: GxReport = validate_lake(contracts, lake_dir, dataset)
        write_gx_report(gx, settings.artifacts_dir)
        step("gx_gate", {"expectations": len(gx.results), "failed": len(gx.failed)}, t0)
        if not gx.gate_passed:
            bad = [
                f"{r.table}.{r.column}:{r.expectation}" for r in gx.failed if r.severity == "error"
            ]
            raise PipelineError(f"Great Expectations gate failed: {bad}")

        t0 = time.perf_counter()
        load = load_raw(contracts, lake_dir, db_path)
        write_load_log(load, settings.artifacts_dir, dataset)
        step("load_raw", {"rows": load.rows}, t0)

        if not (project / "dbt_packages").exists():
            t0 = time.perf_counter()
            if not run_dbt(["deps"], db_path, project).ok:
                raise PipelineError("dbt deps failed")
            step("dbt_deps", {}, t0)

        t0 = time.perf_counter()
        build = run_dbt(["build", "--full-refresh"], db_path, project)
        elapsed = build.run_results.get("elapsed_time")
        step("dbt_build", {"exit": build.returncode, "dbt_elapsed_s": elapsed}, t0)
        # Keep dbt's per-node timings and test results with this run (docs generate would
        # overwrite the dataset's run_results.json).
        folder = settings.artifacts_dir / "pipeline" / dataset
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "dbt_run_results.json").write_text(
            json.dumps(build.run_results), encoding="utf-8"
        )
        if not build.ok:
            raise PipelineError("dbt build failed; see the dbt output above")

        t0 = time.perf_counter()
        report = build_report(db_path, dataset, build.run_results, gx, settings.artifacts_dir)
        step("quality_report", {"dbt": report["dbt"], "rules": report["rules"]}, t0)

        if docs:
            t0 = time.perf_counter()
            run_dbt(["docs", "generate"], db_path, project, capture=True)
            step("dbt_docs", {}, t0)
        run.ok = True
    finally:
        run.seconds = round(time.perf_counter() - t_all, 3)
        run.write(settings.artifacts_dir)
    return run
