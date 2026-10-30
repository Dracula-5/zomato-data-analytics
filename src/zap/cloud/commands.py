"""`zap cloud ...`: the M5 commands. Cloud SDKs are imported inside each command, so the
local DuckDB path never needs them."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import typer

from zap.bench import write_bench
from zap.config import REPO_ROOT, Settings, get_settings
from zap.dbt_runner import DbtRun, run_dbt
from zap.ingest.contracts import load_contracts

cloud_app = typer.Typer(help="AWS and Snowflake (milestone M5).", no_args_is_help=True)

SQL_DIR = REPO_ROOT / "infra" / "snowflake"
TERRAFORM_DIR = REPO_ROOT / "infra" / "terraform"
TRUST_FILE = "snowflake_trust.auto.tfvars.json"
PUBLIC_SALT = "local-dev-salt"  # the fallback in dbt/zomato/macros/pii.sql
DatasetOpt = Annotated[str, typer.Option(help="Lake dataset: `full`, `sample_5pct`, ...")]


def _terraform_output() -> dict[str, Any]:
    """Bucket and role, from the file `make cloud-infra` saves after `terraform apply`."""
    path = get_settings().artifacts_dir / "cloud" / "terraform_output.json"
    if not path.exists():
        typer.echo(f"ERROR: {path} not found. Run `make cloud-infra` first.", err=True)
        raise typer.Exit(code=1)
    outputs: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return {key: item["value"] for key, item in outputs.items()}


def _bucket() -> str:
    return get_settings().s3_bucket or str(_terraform_output()["bucket"])


@cloud_app.command("keys")
def keys_cmd() -> None:
    """Create the Snowflake key pair (if missing) and fill in the bootstrap SQL."""
    from zap.cloud.keys import ensure_key_pair, fill_bootstrap

    settings = get_settings()
    public_key = ensure_key_pair(settings.snowflake_private_key_path)
    out = fill_bootstrap(
        SQL_DIR / "00_bootstrap.sql",
        public_key,
        settings.artifacts_dir / "cloud" / "00_bootstrap.filled.sql",
    )
    typer.echo(f"Private key (keep it private): {settings.snowflake_private_key_path}")
    typer.echo(f"Run this file in a Snowsight worksheet as ACCOUNTADMIN: {out.resolve()}")


@cloud_app.command("whoami")
def whoami_cmd() -> None:
    """Show which AWS and Snowflake identities the project would use. Changes nothing."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    from snowflake.connector.errors import Error as SnowflakeError

    from zap.cloud import snowflake as sf

    settings = get_settings()
    ready = True
    try:
        session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
        identity = session.client("sts").get_caller_identity()
        typer.echo(f"AWS:       {identity['Arn']} (region {settings.aws_region})")
    except (BotoCoreError, ClientError) as err:
        ready = False
        typer.echo(f"AWS:       not ready: {err}")
    try:
        con = sf.connect(settings)
        try:
            sql = "SELECT CURRENT_USER(), CURRENT_ROLE(), CURRENT_REGION(), CURRENT_WAREHOUSE()"
            row = con.cursor().execute(sql).fetchone()
        finally:
            con.close()
        typer.echo(f"Snowflake: user {row[0]}, role {row[1]}, region {row[2]}, warehouse {row[3]}")
    except (SnowflakeError, ValueError, OSError) as err:
        ready = False
        typer.echo(f"Snowflake: not ready: {err}")
    if not ready:
        raise typer.Exit(code=1)


@cloud_app.command("setup")
def setup_cmd() -> None:
    """Create the storage integration, schemas, stage and grants; save the trust values."""
    from zap.cloud import snowflake as sf

    settings = get_settings()
    outputs = _terraform_output()
    bucket, role_arn = str(outputs["bucket"]), str(outputs["role_arn"])
    con = sf.connect(settings)
    try:
        integration = sf.render(SQL_DIR / "01_integration.sql", role_arn=role_arn, bucket=bucket)
        sf.run_script(con, integration)
        objects = sf.render(
            SQL_DIR / "02_objects.sql", bucket=bucket, database=settings.snowflake_database
        )
        sf.run_script(con, objects)
        masking = sf.render(SQL_DIR / "03_masking.sql", database=settings.snowflake_database)
        sf.run_script(con, masking)
        trust = sf.integration_trust(con)
    finally:
        con.close()
    path = TERRAFORM_DIR / TRUST_FILE
    path.write_text(json.dumps(trust, indent=2) + "\n", encoding="utf-8", newline="\n")
    typer.echo(f"Snowflake objects ready. Trust values saved to {path} (gitignored).")


@cloud_app.command("upload")
def upload_cmd(dataset: DatasetOpt = "full") -> None:
    """Mirror lake/<dataset> to S3. Only changed files are uploaded."""
    from zap.cloud.s3 import lake_prefix, s3_client, sync_lake, write_sync_log

    settings = get_settings()
    lake_dir = settings.lake_dir / dataset
    result = sync_lake(s3_client(settings), lake_dir, _bucket(), lake_prefix(dataset))
    write_sync_log(result, settings.artifacts_dir, dataset)
    typer.echo(
        f"s3://{result.bucket}/{result.prefix}: {result.files} files, "
        f"{result.bytes / 1024**3:.3f} GB; {result.uploaded} uploaded, "
        f"{result.unchanged} unchanged, {result.deleted} deleted, {result.seconds}s"
    )


@cloud_app.command("load")
def load_cmd(
    dataset: DatasetOpt = "full",
    table: Annotated[
        list[str] | None, typer.Option("--table", "-t", help="Only these tables.")
    ] = None,
    day: Annotated[
        datetime | None,
        typer.Option("--date", formats=["%Y-%m-%d"], help="Replace one day's facts only."),
    ] = None,
) -> None:
    """COPY the S3 lake into the Snowflake RAW tables (replaces what is there)."""
    from zap.cloud import snowflake as sf
    from zap.cloud.s3 import lake_prefix

    settings = get_settings()
    contracts = load_contracts(settings.contracts_dir)
    con = sf.connect(settings)
    try:
        run = sf.load_raw(
            con, contracts, lake_prefix(dataset), dataset, table, day.date() if day else None
        )
    finally:
        con.close()
    sf.write_load_log(run, settings.artifacts_dir)
    for item in run.tables:
        typer.echo(f"  {item.table}: {item.rows:,} rows in {item.seconds}s")
    typer.echo(f"Loaded {run.rows:,} rows in {run.seconds}s")


def _snowflake_dbt(settings: Settings, args: list[str], capture: bool = False) -> DbtRun:
    salt = settings.pii_hash_salt.get_secret_value() if settings.pii_hash_salt else PUBLIC_SALT
    if salt == PUBLIC_SALT:
        typer.echo(
            "ERROR: set a random PII_HASH_SALT in .env before building on Snowflake.", err=True
        )
        raise typer.Exit(code=1)
    os.environ.update(
        DBT_TARGET="snowflake",
        SNOWFLAKE_ACCOUNT=settings.snowflake_account or "",
        SNOWFLAKE_USER=settings.snowflake_user,
        SNOWFLAKE_ROLE=settings.snowflake_role,
        SNOWFLAKE_WAREHOUSE=settings.snowflake_warehouse,
        SNOWFLAKE_DATABASE=settings.snowflake_database,
        SNOWFLAKE_PRIVATE_KEY_PATH=str(settings.snowflake_private_key_path),
    )
    # run_dbt takes a warehouse path; on this target only its name is used (target/snowflake).
    return run_dbt(args, Path("snowflake.duckdb"), settings.dbt_project_dir, capture=capture)


@cloud_app.command(
    "dbt", context_settings={"allow_extra_args": True, "ignore_unknown_options": True}
)
def dbt_cmd(ctx: typer.Context) -> None:
    """Run dbt on Snowflake, e.g. `zap cloud dbt build`. Artifacts go to target/snowflake."""
    run = _snowflake_dbt(get_settings(), ctx.args)
    if not run.ok:
        raise typer.Exit(code=run.returncode)


@cloud_app.command("check-access")
def check_access_cmd() -> None:
    """Prove what ZAP_ANALYST can and can't reach, and that PII is masked for it."""
    from zap.cloud import checks
    from zap.cloud import snowflake as sf

    settings = get_settings()
    con = sf.connect(settings)
    try:
        report = checks.run_access_checks(con)
    finally:
        con.close()
    path = checks.write_report("access_check", report, settings.artifacts_dir)
    for item in report["checks"]:
        expected = "allowed" if item["expected_allowed"] else "denied"
        state = "allowed" if item["allowed"] else "denied"
        mark = "ok  " if state == expected else "FAIL"
        typer.echo(f"  {mark} {item['check']}: {state}")
    typer.echo(
        f"  rows with a readable email: deployer {report['deployer_unmasked_rows']:,}, "
        f"analyst {report['analyst_unmasked_rows']:,}"
    )
    typer.echo(f"{'PASSED' if report['passed'] else 'FAILED'}. Report: {path}")
    if not report["passed"]:
        raise typer.Exit(code=1)


@cloud_app.command("compare")
def compare_cmd(dataset: DatasetOpt = "full") -> None:
    """Compare the marts on Snowflake with the same dataset's local DuckDB build."""
    from zap.cloud import checks
    from zap.cloud import snowflake as sf

    settings = get_settings()
    db_path = settings.duckdb_path(dataset)
    if not db_path.exists():
        typer.echo(f"ERROR: {db_path} not found. Build it first (`make local-full`).", err=True)
        raise typer.Exit(code=1)
    con = sf.connect(settings)
    try:
        results = checks.compare_engines(con, db_path)
    finally:
        con.close()
    summary = checks.compare_summary(results, dataset)
    path = checks.write_report(f"engine_compare_{dataset}", summary, settings.artifacts_dir)
    for item in results:
        if not item.identical:
            typer.echo(f"  DIFFERENT {item.table}: {len(item.mismatches)} of {item.measures}")
            for miss in item.mismatches[:5]:
                typer.echo(
                    f"    {miss['measure']}: duckdb {miss['duckdb']}, snowflake {miss['snowflake']}"
                )
    typer.echo(
        f"{summary['tables_identical']} of {summary['tables']} tables identical, "
        f"{summary['measures']:,} measures compared. Report: {path}"
    )
    if summary["tables_identical"] != summary["tables"]:
        raise typer.Exit(code=1)


@cloud_app.command("bench")
def bench_cmd(
    which: Annotated[
        Literal["all", "incremental", "clustering"], typer.Argument(help="Which benchmark.")
    ] = "all",
    dataset: DatasetOpt = "full",
) -> None:
    """Snowflake benchmarks: incremental vs full refresh, and clustering. Uses credits."""
    from zap.cloud import bench as cloud_bench
    from zap.cloud import snowflake as sf
    from zap.cloud.s3 import lake_prefix

    settings = get_settings()
    contracts = load_contracts(settings.contracts_dir)
    con = sf.connect(settings)
    results = []
    try:
        if which in ("all", "incremental"):
            results.append(
                cloud_bench.bench_incremental(
                    con,
                    contracts,
                    lake_prefix(dataset),
                    dataset,
                    lambda args: _snowflake_dbt(settings, args, capture=True),
                )
            )
        if which in ("all", "clustering"):
            results.append(cloud_bench.bench_clustering(con))
    finally:
        con.close()
    for result in results:
        path = write_bench(result, settings.artifacts_dir, f"snowflake_{dataset}")
        typer.echo(f"Wrote {path}")
