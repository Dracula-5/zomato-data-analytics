"""Live-warehouse checks and Snowflake benchmarks against fakes: no network, no credits."""

from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
from pydantic import SecretStr
from snowflake.connector.errors import ProgrammingError
from typer.testing import CliRunner

from helpers import REPO_ROOT
from zap import dbt_runner
from zap.bench import FACTS
from zap.cli import app
from zap.cloud import bench, checks
from zap.config import Settings, get_settings
from zap.dbt_runner import DbtRun, dbt_env
from zap.ingest.contracts import Contract


class ScriptedCursor:
    """Answers each statement from a function, so a test can act as any Snowflake role."""

    def __init__(self, respond: Any) -> None:
        self.respond = respond
        self.sfqid = "query-1"
        self.rows: list[tuple[Any, ...]] = []
        self.statements: list[str] = []

    def execute(self, sql: str) -> "ScriptedCursor":
        self.statements.append(sql)
        self.rows = self.respond(sql)
        return self

    def fetchall(self) -> list[tuple[Any, ...]]:
        return self.rows

    def close(self) -> None:
        pass


class ScriptedConnection:
    def __init__(self, respond: Any) -> None:
        self.cur = ScriptedCursor(respond)

    def cursor(self) -> ScriptedCursor:
        return self.cur


def warehouse(denied: set[str], analyst_unmasked: int = 0, deployer_fails: bool = False) -> Any:
    """A fake account. `denied` holds substrings of statements ZAP_ANALYST may not run."""
    state = {"role": "ZAP_DEPLOYER"}

    def respond(sql: str) -> list[tuple[Any, ...]]:
        if sql.startswith("USE ROLE"):
            state["role"] = sql.split()[-1]
            return []
        if sql == checks.UNMASKED_ROWS:
            return [(7 if state["role"] == "ZAP_DEPLOYER" else analyst_unmasked,)]
        blocked = any(text in sql for text in denied)
        if blocked and (state["role"] == "ZAP_ANALYST" or deployer_fails):
            raise ProgrammingError(msg="not authorized", errno=2003)
        return [(1,)]

    return respond


ANALYST_DENIED = {"RAW.USERS", "STG_USERS", "DELETE", "LIST"}


def test_access_checks_pass_when_the_analyst_is_restricted() -> None:
    report = checks.run_access_checks(ScriptedConnection(warehouse(ANALYST_DENIED)))
    assert report["passed"]
    assert report["deployer_unmasked_rows"] == 7 and report["analyst_unmasked_rows"] == 0


def test_only_one_role_is_active_for_the_checks() -> None:
    con = ScriptedConnection(warehouse(ANALYST_DENIED))
    checks.run_access_checks(con)
    assert con.cur.statements[0] == "USE SECONDARY ROLES NONE"


def test_a_table_the_analyst_should_not_read_fails_the_check() -> None:
    report = checks.run_access_checks(ScriptedConnection(warehouse({"STG_USERS", "DELETE"})))
    assert not report["passed"]
    assert [c["check"] for c in report["checks"] if c["allowed"] != c["expected_allowed"]] == [
        "read raw users",
        "list the S3 stage",
    ]


def test_masking_that_leaks_fails_the_check() -> None:
    con = ScriptedConnection(warehouse(ANALYST_DENIED, analyst_unmasked=3))
    assert not checks.run_access_checks(con)["passed"]


def test_a_check_the_deployer_cant_run_proves_nothing() -> None:
    con = ScriptedConnection(warehouse({"RAW.USERS"}, deployer_fails=True))
    with pytest.raises(RuntimeError, match="read raw users"):
        checks.run_access_checks(con)


def test_an_unrelated_sql_error_is_not_counted_as_denied() -> None:
    def broken(sql: str) -> list[tuple[Any, ...]]:
        if "RAW.USERS" in sql:
            raise ProgrammingError(msg="syntax error", errno=1003)
        return [(1,)]

    with pytest.raises(ProgrammingError, match="syntax error"):
        checks.run_access_checks(ScriptedConnection(broken))


def test_profile_measures_suit_each_type() -> None:
    columns = [
        ("order_id", "BIGINT"),
        ("price", "DECIMAL(12,2)"),
        ("city", "VARCHAR"),
        ("is_delivered", "BOOLEAN"),
        ("_loaded_at", "TIMESTAMP"),
        ("valid_from", "TIMESTAMP"),
    ]
    exprs = checks.profile_exprs(columns)
    assert exprs["order_id:sum"] == "SUM(CAST(order_id AS DOUBLE))"
    assert exprs["price:sum"] == "SUM(CAST(price AS DOUBLE))"
    assert exprs["price:sum_sq"] == "SUM(CAST(price AS DOUBLE) * CAST(price AS DOUBLE))"
    assert exprs["city:distinct"] == "COUNT(DISTINCT city)"
    assert exprs["is_delivered:true"] == "SUM(CASE WHEN is_delivered THEN 1 ELSE 0 END)"
    assert not any(label.startswith(("_loaded_at", "valid_from")) for label in exprs)


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        (10, 10.0, True),
        (1000000.0, 1000000.0000001, True),
        (37478.8113, 37478.8114, False),  # one rounded digit in a large sum
        (100.0, 100.5, False),
        (None, None, True),
        (None, 0, False),
    ],
)
def test_values_match_allows_only_float_noise(a: Any, b: Any, same: bool) -> None:
    assert checks.values_match(a, b) is same


def duckdb_marts(path: Path) -> None:
    con = duckdb.connect(str(path))
    con.execute("CREATE SCHEMA marts")
    con.execute("CREATE TABLE marts.t AS SELECT 1 AS id, 'Pune' AS city UNION ALL SELECT 2, 'Goa'")
    con.close()


def test_compare_finds_identical_tables(tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    duckdb_marts(db)
    results = checks.compare_engines(
        ScriptedConnection(lambda _: [(2, 2, 3, 5, 2, 2)]), db, ("marts",)
    )
    assert [r.identical for r in results] == [True]
    assert results[0].measures == 6


def test_compare_reports_the_measure_that_differs(tmp_path: Path) -> None:
    db = tmp_path / "x.duckdb"
    duckdb_marts(db)
    results = checks.compare_engines(
        ScriptedConnection(lambda _: [(2, 2, 9, 5, 2, 2)]), db, ("marts",)
    )
    assert [m["measure"] for m in results[0].mismatches] == ["id:sum"]
    assert checks.compare_summary(results, "full")["tables_identical"] == 0


def test_masking_hook_only_touches_snowflake() -> None:
    macros = (REPO_ROOT / "dbt/zomato/macros/pii.sql").read_text(encoding="utf-8")
    assert "default__apply_masking(columns) -%}\n{%- endmacro" in macros
    assert "snowflake__apply_masking" in macros


def test_dbt_gets_the_salt_from_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The value may live only in .env, so it can't be left for dbt to find in the environment.
    monkeypatch.delenv("PII_HASH_SALT", raising=False)
    settings = Settings(pii_hash_salt=SecretStr("from-dotenv"))
    monkeypatch.setattr(dbt_runner, "get_settings", lambda: settings)
    assert dbt_env(tmp_path / "a.duckdb", tmp_path)["PII_HASH_SALT"] == "from-dotenv"


@pytest.mark.parametrize("salt", [None, "local-dev-salt"])
def test_cloud_dbt_refuses_the_public_salt(
    salt: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)  # no .env here
    if salt:
        monkeypatch.setenv("PII_HASH_SALT", salt)
    else:
        monkeypatch.delenv("PII_HASH_SALT", raising=False)
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["cloud", "dbt", "build"])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "PII_HASH_SALT" in result.output


def run(ok: bool = True, seconds: float = 10.0) -> DbtRun:
    results = [{"unique_id": uid, "execution_time": seconds / 2} for _, uid in FACTS.values()]
    return DbtRun(
        args=[],
        returncode=0 if ok else 1,
        seconds=seconds,
        run_results={"elapsed_time": seconds, "results": results},
    )


class IncrementalWarehouse:
    """Fake account whose fact totals are what a benchmark should find after each cycle."""

    def __init__(self) -> None:
        self.day = date(2026, 6, 30)
        self.deletes: list[str] = []

    def __call__(self, sql: str) -> list[tuple[Any, ...]]:
        if "MAX(ORDER_DATE)" in sql:
            return [(self.day,)]
        if sql.startswith("DELETE"):
            self.deletes.append(sql)
            return [(5,)]
        if "FCT_ORDERS" in sql:
            return [(100, 2500.0, 200, 30)]
        return []


def test_incremental_bench_holds_out_a_day_and_checks_the_totals(
    contracts: dict[str, Contract], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = IncrementalWarehouse()
    loads: list[date | None] = []
    monkeypatch.setattr(bench, "load_raw", lambda *a: loads.append(a[-1]))
    seconds = iter([100.0, 10.0, 90.0, 12.0, 110.0, 8.0])
    result = bench.bench_incremental(
        ScriptedConnection(fake),
        contracts,
        "raw/",
        "full",
        lambda _: run(seconds=next(seconds)),
    )
    assert result["result_correct"] and result["repeats"] == 3
    assert result["full_refresh_dbt_s"] == 100.0 and result["incremental_dbt_s"] == 10.0
    assert result["saved_pct"] == 90.0
    assert loads == [fake.day] * 3
    assert len(fake.deletes) == 3 * len(FACTS)
    assert "RAW.REVIEWS WHERE REVIEW_DATE = '2026-06-30'" in " ".join(fake.deletes)


def test_incremental_bench_notices_wrong_totals(
    contracts: dict[str, Contract], monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = IncrementalWarehouse()
    answers = iter([(100, 2500.0, 200, 30), (99, 2500.0, 200, 30)])

    def respond(sql: str) -> list[tuple[Any, ...]]:
        return [next(answers)] if "FCT_ORDERS" in sql else fake(sql)

    monkeypatch.setattr(bench, "load_raw", lambda *_: None)
    result = bench.bench_incremental(
        ScriptedConnection(respond), contracts, "raw/", "full", lambda _: run(), repeats=1
    )
    assert not result["result_correct"]


def test_held_out_day_is_reloaded_when_dbt_crashes(
    contracts: dict[str, Contract], monkeypatch: pytest.MonkeyPatch
) -> None:
    loads: list[Any] = []
    monkeypatch.setattr(bench, "load_raw", lambda *a: loads.append(a[-1]))

    def crash(_: list[str]) -> DbtRun:
        raise OSError("dbt missing")

    with pytest.raises(OSError, match="dbt missing"):
        bench.bench_incremental(
            ScriptedConnection(IncrementalWarehouse()), contracts, "raw/", "full", crash
        )
    assert loads == [date(2026, 6, 30)]


def test_failed_dbt_is_an_error_not_a_timing(
    contracts: dict[str, Contract], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(bench, "load_raw", lambda *_: None)
    with pytest.raises(RuntimeError, match="dbt run failed"):
        bench.bench_incremental(
            ScriptedConnection(IncrementalWarehouse()),
            contracts,
            "raw/",
            "full",
            lambda _: run(ok=False),
        )


def clustering_warehouse(answers_differ: bool = False) -> Any:
    def respond(sql: str) -> list[tuple[Any, ...]]:
        if "MAX(ORDER_DATE)" in sql:
            return [(date(2026, 6, 30),)]
        if "CLUSTERING_INFORMATION" in sql:
            return [('{"average_depth": 2.5}',)]
        if "GET_QUERY_OPERATOR_STATS" in sql:
            return [('{"pruning": {"partitions_scanned": 4, "partitions_total": 40}}',)]
        if "GROUP BY CITY" in sql:
            shuffled = "_BENCH_SHUFFLED" in sql and answers_differ
            return [("Pune", 1 if shuffled else 2, 3)]
        return []

    return respond


def test_clustering_bench_measures_every_layout_and_cleans_up() -> None:
    con = ScriptedConnection(clustering_warehouse())
    result = bench.bench_clustering(con, repeats=2)
    assert result["results_identical"] and result["result_cache"] == "off"
    assert result["sorted_by_date"]["partitions"] == {"scanned": 4, "total": 40}
    assert len(result["shuffled"]["runs_ms"]) == 2
    assert "ALTER SESSION SET USE_CACHED_RESULT = FALSE" in con.cur.statements
    assert "DROP TABLE IF EXISTS RAW._BENCH_SHUFFLED" in con.cur.statements
    assert "DROP TABLE IF EXISTS RAW._BENCH_SORTED" in con.cur.statements
    assert not any("DROP TABLE IF EXISTS MARTS" in s for s in con.cur.statements)


def test_clustering_bench_flags_different_answers() -> None:
    con = ScriptedConnection(clustering_warehouse(answers_differ=True))
    assert not bench.bench_clustering(con, repeats=1)["results_identical"]


def test_bench_tables_are_dropped_after_a_failure() -> None:
    def respond(sql: str) -> list[tuple[Any, ...]]:
        if "GROUP BY CITY" in sql:
            raise RuntimeError("warehouse suspended")
        return [(date(2026, 6, 30),)] if "MAX(ORDER_DATE)" in sql else []

    con = ScriptedConnection(respond)
    with pytest.raises(RuntimeError, match="warehouse suspended"):
        bench.bench_clustering(con, repeats=1)
    assert "DROP TABLE IF EXISTS RAW._BENCH_SORTED" in con.cur.statements


def test_check_access_command_reports_and_fails_on_a_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    con = ScriptedConnection(warehouse(ANALYST_DENIED, analyst_unmasked=2))
    con.close = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr("zap.cloud.snowflake.connect", lambda _: con)
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["cloud", "check-access"])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "analyst 2" in result.output and "FAILED" in result.output
    assert (tmp_path / "cloud" / "access_check.json").exists()


def test_compare_command_needs_a_duckdb_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["cloud", "compare", "--dataset", "nope"])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "make local-full" in result.output


def test_compare_command_lists_differences(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    duckdb_marts(tmp_path / "full.duckdb")
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path))
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
    con = ScriptedConnection(lambda _: [(2, 2, 9, 5, 2, 2)])
    con.close = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr("zap.cloud.snowflake.connect", lambda _: con)
    real_compare = checks.compare_engines
    monkeypatch.setattr(checks, "compare_engines", lambda c, db: real_compare(c, db, ("marts",)))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["cloud", "compare"])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "DIFFERENT marts.t" in result.output and "id:sum" in result.output
    assert "0 of 1 tables identical" in result.output
