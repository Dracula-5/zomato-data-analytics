"""Snowflake tooling against a recording fake connection: no network, no credits."""

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from typer.testing import CliRunner

from helpers import REPO_ROOT
from zap.cli import app
from zap.cloud import keys
from zap.cloud import snowflake as sf
from zap.config import Settings
from zap.ingest.contracts import Contract

SQL_DIR = REPO_ROOT / "infra" / "snowflake"


class FakeCursor:
    def __init__(self, con: "FakeConnection") -> None:
        self.con = con
        self.description: list[tuple[str]] = []
        self._rows: list[tuple[Any, ...]] = []

    def execute(self, sql: str) -> "FakeCursor":
        self.con.statements.append(sql)
        if sql.startswith("COPY") and self.con.fail_copy:
            raise RuntimeError("COPY failed")
        if sql.startswith("COPY"):
            self.description = [("file",), ("status",), ("rows_loaded",)]
            self._rows = [("a.parquet", "LOADED", 7), ("b.parquet", "LOADED", 3)]
        elif sql.startswith("DESC INTEGRATION"):
            self._rows = [
                ("STORAGE_AWS_IAM_USER_ARN", "String", "arn:aws:iam::1:user/sf", ""),
                ("STORAGE_AWS_EXTERNAL_ID", "String", "EXT_ID", ""),
                ("ENABLED", "Boolean", "true", "false"),
            ]
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self._rows

    def close(self) -> None:
        self.con.closed_cursors += 1


class FakeConnection:
    def __init__(self, fail_copy: bool = False) -> None:
        self.statements: list[str] = []
        self.fail_copy = fail_copy
        self.closed_cursors = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def execute_string(self, sql: str, remove_comments: bool = False) -> list[FakeCursor]:
        self.statements.append(sql)
        return [self.cursor()]


def test_raw_ddl_matches_dbt_case_rules_and_has_no_secret(contracts: dict[str, Contract]) -> None:
    ddl = sf.raw_table_ddl(contracts["users"])
    assert ddl.startswith("CREATE TABLE IF NOT EXISTS RAW.USERS (")
    assert "USER_ID VARCHAR" in ddl  # unquoted names fold to upper case, as dbt expects
    assert '"Marital Status" VARCHAR' in ddl  # the source spelling stays exact
    assert "_LOADED_AT TIMESTAMP_NTZ" in ddl
    assert "PASSWORD" not in ddl.upper()


def test_every_contract_type_has_a_snowflake_type(contracts: dict[str, Contract]) -> None:
    for contract in contracts.values():
        assert sf.raw_table_ddl(contract)
    assert "ORDER_DATE DATE" in sf.raw_table_ddl(contracts["order_items"])  # from the parent


def test_copy_reads_one_table_or_one_day(contracts: dict[str, Contract]) -> None:
    whole = sf.copy_sql(contracts["orders"], "raw/")
    assert "COPY INTO RAW.ORDERS FROM @RAW.LAKE/raw/orders/ " in whole
    assert "MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE" in whole and "FORCE = TRUE" in whole
    day = sf.copy_sql(contracts["orders"], "sample_5pct/raw/", date(2026, 6, 24))
    assert "@RAW.LAKE/sample_5pct/raw/orders/order_date=2026-06-24/ " in day


def test_full_load_replaces_the_table_in_one_transaction(contracts: dict[str, Contract]) -> None:
    con = FakeConnection()
    result = sf.load_table(con, contracts["food"], "raw/")
    assert [s.split()[0] for s in con.statements] == ["BEGIN", "DELETE", "COPY", "COMMIT"]
    assert con.statements[1] == "DELETE FROM RAW.FOOD"
    assert result.rows == 10


def test_day_load_deletes_only_that_day(contracts: dict[str, Contract]) -> None:
    con = FakeConnection()
    sf.load_table(con, contracts["orders"], "raw/", date(2026, 6, 24))
    assert con.statements[1] == "DELETE FROM RAW.ORDERS WHERE ORDER_DATE = '2026-06-24'"


def test_failed_copy_rolls_back(contracts: dict[str, Contract]) -> None:
    con = FakeConnection(fail_copy=True)
    with pytest.raises(RuntimeError, match="COPY failed"):
        sf.load_table(con, contracts["food"], "raw/")
    assert con.statements[-1] == "ROLLBACK" and "COMMIT" not in con.statements
    assert con.closed_cursors == 1


def test_day_mode_skips_dimensions(contracts: dict[str, Contract]) -> None:
    run = sf.load_raw(FakeConnection(), contracts, "raw/", "full", day=date(2026, 6, 24))
    assert sorted(t.table for t in run.tables) == ["order_items", "orders", "reviews"]
    assert run.rows == 30 and run.partition_date == "2026-06-24"


def test_full_load_covers_every_table_and_writes_a_log(
    contracts: dict[str, Contract], tmp_path: Path
) -> None:
    run = sf.load_raw(FakeConnection(), contracts, "raw/", "full")
    assert len(run.tables) == len(contracts)
    saved = json.loads(sf.write_load_log(run, tmp_path).read_text(encoding="utf-8"))
    assert saved["rows"] == 10 * len(contracts)


def test_copy_with_no_files_counts_zero_rows() -> None:
    cursor = FakeCursor(FakeConnection())
    cursor.description = [("status",)]  # Snowflake's "Copy executed with 0 files processed."
    assert sf._rows_loaded(cursor) == 0


def test_render_fills_markers_and_refuses_leftovers(tmp_path: Path) -> None:
    sql = sf.render(SQL_DIR / "01_integration.sql", role_arn="arn:x", bucket="b")
    assert "STORAGE_AWS_ROLE_ARN = 'arn:x'" in sql and "'s3://b/'" in sql
    objects = sf.render(SQL_DIR / "02_objects.sql", bucket="b", database="ZOMATO")
    assert "URL = 's3://b/'" in objects and "USE DATABASE ZOMATO" in objects
    with pytest.raises(ValueError, match="role_arn"):
        sf.render(SQL_DIR / "01_integration.sql", bucket="b")


def test_integration_uses_if_not_exists() -> None:
    # Re-creating it would change Snowflake's IAM user and break the role's trust policy.
    assert "CREATE STORAGE INTEGRATION IF NOT EXISTS" in (SQL_DIR / "01_integration.sql").read_text(
        encoding="utf-8"
    )


def test_trust_values_come_from_desc_integration() -> None:
    assert sf.integration_trust(FakeConnection()) == {
        "snowflake_iam_user_arn": "arn:aws:iam::1:user/sf",
        "snowflake_external_id": "EXT_ID",
    }


def test_connect_needs_an_account() -> None:
    with pytest.raises(ValueError, match="SNOWFLAKE_ACCOUNT"):
        sf.connect(Settings())


def test_key_pair_is_created_once_and_never_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "key.p8"
    public = keys.ensure_key_pair(path)
    first = path.read_bytes()
    assert first.startswith(b"-----BEGIN")  # PEM; the next lines load it as a real key
    assert keys.ensure_key_pair(path) == public  # same key, so Snowflake keeps trusting it
    assert path.read_bytes() == first
    assert "\n" not in public and "BEGIN" not in public
    loaded = serialization.load_der_private_key(keys.private_key_der(path), password=None)
    assert loaded.key_size == 2048  # type: ignore[union-attr]


def test_non_rsa_key_is_rejected(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    pem = ed25519.Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path = tmp_path / "ed.p8"
    path.write_bytes(pem)
    with pytest.raises(ValueError, match="not an RSA"):
        keys.load_private_key(path)


def test_bootstrap_template_is_filled(tmp_path: Path) -> None:
    out = keys.fill_bootstrap(SQL_DIR / "00_bootstrap.sql", "PUBLICKEY", tmp_path / "o" / "x.sql")
    text = out.read_text(encoding="utf-8")
    assert "RSA_PUBLIC_KEY = 'PUBLICKEY'" in text
    assert keys.PUBLIC_KEY_PLACEHOLDER not in text
    assert "AUTO_SUSPEND = 60" in text and "CREDIT_QUOTA = 10" in text
    assert "TYPE = SERVICE" in text


def test_bootstrap_needs_the_marker(tmp_path: Path) -> None:
    template = tmp_path / "t.sql"
    template.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(ValueError, match="marker"):
        keys.fill_bootstrap(template, "K", tmp_path / "o.sql")


def test_keys_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_PRIVATE_KEY_PATH", str(tmp_path / "k.p8"))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    result = CliRunner().invoke(app, ["cloud", "keys"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "artifacts" / "cloud" / "00_bootstrap.filled.sql").exists()


def test_setup_explains_a_missing_terraform_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    result = CliRunner().invoke(app, ["cloud", "setup"])
    assert result.exit_code == 1
    assert "make cloud-infra" in result.output
