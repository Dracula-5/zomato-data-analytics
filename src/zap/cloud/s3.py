"""Mirror the local Parquet lake to S3, so a re-run uploads only what changed.

Credentials always come from a named profile: an explicit profile wins over `AWS_*` keys
in the environment, so another project's keys can't be used by accident.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import boto3

from zap.config import Settings

RAW_PREFIX = "raw/"


def lake_prefix(dataset: str) -> str:
    """`raw/<table>/` for the full data; a sample lives under its own folder."""
    return RAW_PREFIX if dataset == "full" else f"{dataset}/{RAW_PREFIX}"


def s3_client(settings: Settings) -> Any:
    session = boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)
    return session.client("s3")


@dataclass
class SyncResult:
    bucket: str
    prefix: str
    files: int
    bytes: int
    uploaded: int
    unchanged: int
    deleted: int
    seconds: float


def _remote_objects(client: Any, bucket: str, prefix: str) -> dict[str, dict[str, Any]]:
    pages = client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix)
    return {obj["Key"]: obj for page in pages for obj in page.get("Contents", [])}


def _is_current(local: Path, remote: dict[str, Any] | None) -> bool:
    """Same size, and uploaded after the file last changed."""
    if remote is None:
        return False
    stat = local.stat()
    return bool(
        remote["Size"] == stat.st_size and remote["LastModified"].timestamp() >= stat.st_mtime
    )


def sync_lake(
    client: Any, lake_dir: Path, bucket: str, prefix: str, workers: int = 8
) -> SyncResult:
    """Make `s3://bucket/prefix` match `lake_dir/raw` exactly (extra objects are deleted)."""
    started = time.perf_counter()
    root = lake_dir / "raw"
    if not root.exists():
        raise FileNotFoundError(f"No lake at {root}. Run `zap ingest` first.")
    files = {
        prefix + path.relative_to(root).as_posix(): path for path in sorted(root.rglob("*.parquet"))
    }
    remote = _remote_objects(client, bucket, prefix)
    todo = [key for key, path in files.items() if not _is_current(path, remote.get(key))]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # list() so an upload error is raised here instead of being lost in the pool.
        list(pool.map(lambda key: client.upload_file(str(files[key]), bucket, key), todo))
    stale = sorted(remote.keys() - files.keys())
    for start in range(0, len(stale), 1000):  # DeleteObjects takes 1,000 keys at a time
        batch = [{"Key": key} for key in stale[start : start + 1000]]
        client.delete_objects(Bucket=bucket, Delete={"Objects": batch, "Quiet": True})
    return SyncResult(
        bucket=bucket,
        prefix=prefix,
        files=len(files),
        bytes=sum(path.stat().st_size for path in files.values()),
        uploaded=len(todo),
        unchanged=len(files) - len(todo),
        deleted=len(stale),
        seconds=round(time.perf_counter() - started, 3),
    )


def write_sync_log(result: SyncResult, artifacts_dir: Path, dataset: str) -> Path:
    path = artifacts_dir / "cloud" / f"s3_sync_{dataset}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(result), indent=2) + "\n", encoding="utf-8", newline="\n")
    return path
