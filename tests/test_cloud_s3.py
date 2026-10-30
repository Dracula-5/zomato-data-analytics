"""S3 sync against moto's in-memory S3: nothing here reaches real AWS."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from zap.cli import app
from zap.cloud.s3 import lake_prefix, sync_lake, write_sync_log

BUCKET = "zomato-ai-dp-test"
LONG_AGO = 1_600_000_000  # well before moto's upload time, so a file counts as unchanged


@pytest.fixture
def s3(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        yield client


def _write(path: Path, data: bytes = b"parquet") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.utime(path, (LONG_AGO, LONG_AGO))


@pytest.fixture
def lake(tmp_path: Path) -> Path:
    root = tmp_path / "lake" / "full"
    _write(root / "raw" / "food" / "food.parquet")
    _write(root / "raw" / "orders" / "order_date=2026-06-24" / "data_0.parquet")
    (root / "raw" / "notes.txt").write_text("not parquet", encoding="utf-8")
    return root


def _keys(client: Any) -> list[str]:
    return sorted(o["Key"] for o in client.list_objects_v2(Bucket=BUCKET).get("Contents", []))


def test_prefix_keeps_the_raw_table_layout_for_full_data() -> None:
    assert lake_prefix("full") == "raw/"
    assert lake_prefix("sample_5pct") == "sample_5pct/raw/"


def test_first_sync_uploads_only_parquet_and_keeps_the_layout(s3: Any, lake: Path) -> None:
    result = sync_lake(s3, lake, BUCKET, "raw/")
    assert (result.files, result.uploaded, result.unchanged) == (2, 2, 0)
    assert result.bytes == 2 * len(b"parquet")
    assert _keys(s3) == [
        "raw/food/food.parquet",
        "raw/orders/order_date=2026-06-24/data_0.parquet",
    ]


def test_second_sync_uploads_nothing(s3: Any, lake: Path) -> None:
    sync_lake(s3, lake, BUCKET, "raw/")
    again = sync_lake(s3, lake, BUCKET, "raw/")
    assert (again.uploaded, again.unchanged, again.deleted) == (0, 2, 0)


def test_changed_file_is_uploaded_again(s3: Any, lake: Path) -> None:
    sync_lake(s3, lake, BUCKET, "raw/")
    _write(lake / "raw" / "food" / "food.parquet", b"parquet, but longer")
    again = sync_lake(s3, lake, BUCKET, "raw/")
    assert (again.uploaded, again.unchanged) == (1, 1)
    body = s3.get_object(Bucket=BUCKET, Key="raw/food/food.parquet")["Body"].read()
    assert body == b"parquet, but longer"


def test_objects_no_longer_in_the_lake_are_deleted_only_under_the_prefix(
    s3: Any, lake: Path
) -> None:
    s3.put_object(Bucket=BUCKET, Key="raw/orders/order_date=2020-01-01/old.parquet", Body=b"x")
    s3.put_object(Bucket=BUCKET, Key="sample_5pct/raw/food/food.parquet", Body=b"x")
    result = sync_lake(s3, lake, BUCKET, "raw/")
    assert result.deleted == 1
    assert "sample_5pct/raw/food/food.parquet" in _keys(s3)  # another dataset: left alone
    assert "raw/orders/order_date=2020-01-01/old.parquet" not in _keys(s3)


def test_missing_lake_is_a_clear_error(s3: Any, tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="zap ingest"):
        sync_lake(s3, tmp_path / "nope", BUCKET, "raw/")


def test_sync_log(s3: Any, lake: Path, tmp_path: Path) -> None:
    path = write_sync_log(sync_lake(s3, lake, BUCKET, "raw/"), tmp_path, "full")
    assert json.loads(path.read_text(encoding="utf-8"))["files"] == 2


def test_upload_command_uses_the_named_profile(
    s3: Any, lake: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    credentials = tmp_path / "credentials"
    credentials.write_text(
        "[zomato]\naws_access_key_id = fake\naws_secret_access_key = fake\n", encoding="utf-8"
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("LAKE_DIR", str(lake.parent))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("S3_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    result = CliRunner().invoke(app, ["cloud", "upload", "--dataset", "full"])
    assert result.exit_code == 0, result.output
    assert "2 files" in result.output and "2 uploaded" in result.output
    assert (tmp_path / "artifacts" / "cloud" / "s3_sync_full.json").exists()
