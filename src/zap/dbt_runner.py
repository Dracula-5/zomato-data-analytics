"""Run dbt as a subprocess against the DuckDB file of a chosen dataset.

A subprocess (instead of dbt's in-process API) keeps dbt's global state and logging out of
our process and gives honest wall-clock timings for the benchmarks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from zap.config import get_settings


@dataclass
class DbtRun:
    args: list[str]
    returncode: int
    seconds: float
    run_results: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def dbt_executable() -> str:
    """The dbt installed next to the running Python (the project venv), else PATH."""
    local = Path(sys.executable).parent / ("dbt.exe" if os.name == "nt" else "dbt")
    return str(local) if local.exists() else "dbt"


def dbt_target_dir(db_path: Path, project_dir: Path) -> Path:
    """dbt's artifacts folder for one dataset: `target/<dataset>`.

    One folder per warehouse file, so runs on different datasets (a local sample, the
    test fixture, the Airflow container sharing this checkout) never overwrite each
    other's run_results.json, manifest or docs.
    """
    return project_dir.resolve() / "target" / db_path.stem


def dbt_env(db_path: Path, project_dir: Path) -> dict[str, str]:
    target = dbt_target_dir(db_path, project_dir)
    env = {
        **os.environ,
        "DUCKDB_PATH": str(db_path.resolve()),
        "DBT_PROFILES_DIR": str(project_dir.resolve()),
        "DBT_TARGET_PATH": str(target),
        "DBT_LOG_PATH": str(target / "logs"),
        "PYTHONUTF8": "1",
    }
    salt = get_settings().pii_hash_salt
    if salt is not None:  # may come from .env, which dbt itself never reads
        env["PII_HASH_SALT"] = salt.get_secret_value()
    return env


def run_dbt(args: list[str], db_path: Path, project_dir: Path, capture: bool = False) -> DbtRun:
    """Run `dbt <args>`; returns exit code, wall time and parsed run_results.json."""
    results_path = dbt_target_dir(db_path, project_dir) / "run_results.json"
    started_ns = time.time_ns()
    t0 = time.perf_counter()
    proc = subprocess.run(  # noqa: S603 - fixed executable, argument list, no shell
        [dbt_executable(), *args, "--project-dir", str(project_dir.resolve())],
        cwd=project_dir,
        env=dbt_env(db_path, project_dir),
        capture_output=capture,
        text=True,
        check=False,
    )
    seconds = round(time.perf_counter() - t0, 3)
    results: dict[str, Any] = {}
    if results_path.exists() and results_path.stat().st_mtime_ns >= started_ns:
        results = json.loads(results_path.read_text(encoding="utf-8"))
    return DbtRun(args=args, returncode=proc.returncode, seconds=seconds, run_results=results)
