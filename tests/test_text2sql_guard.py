"""The SQL guard against attacks. Each of these must be refused before it reaches DuckDB."""

from pathlib import Path

import duckdb
import pytest

from zap.text2sql.guard import ALLOWED_TABLES, MAX_ROWS, GuardError, check_sql, run_readonly

ATTACKS = {
    "second statement": "SELECT 1; DROP TABLE marts.fct_orders",
    "hidden after a comment": "SELECT 1 /* x */; DELETE FROM marts.fct_orders",
    "delete": "DELETE FROM marts.fct_orders",
    "update": "UPDATE marts.fct_orders SET gst = 0",
    "insert": "INSERT INTO marts.dim_city VALUES ('x', 1, 1, 1, 1)",
    "merge": "MERGE INTO marts.dim_city t USING marts.dim_city s ON t.city = s.city "
    "WHEN MATCHED THEN DELETE",
    "drop": "DROP TABLE marts.fct_orders",
    "truncate": "TRUNCATE marts.fct_orders",
    "create table": "CREATE TABLE marts.x AS SELECT 1",
    "create or replace": "CREATE OR REPLACE VIEW marts.v AS SELECT 1",
    "create macro": "CREATE MACRO m() AS 1",
    "alter": "ALTER TABLE marts.fct_orders ADD COLUMN x INT",
    "copy out": "COPY marts.fct_orders TO 'orders.csv'",
    "copy query out": "COPY (SELECT * FROM marts.dim_customer) TO 'c.parquet'",
    "copy in": "COPY marts.dim_city FROM 'x.csv'",
    "attach": "ATTACH 'other.db' AS other",
    "detach": "DETACH other",
    "install": "INSTALL httpfs",
    "load": "LOAD httpfs",
    "pragma": "PRAGMA database_list",
    "set": "SET enable_external_access = true",
    "call": "CALL pragma_version()",
    "export": "EXPORT DATABASE 'out'",
    "describe": "DESCRIBE marts.fct_orders",
    "show": "SHOW TABLES",
    "read a csv": "SELECT * FROM read_csv('C:/Users/dheer/.env')",
    "read csv auto": "SELECT * FROM read_csv_auto('secrets.csv')",
    "read parquet": "SELECT * FROM read_parquet('x.parquet')",
    "read json": "SELECT * FROM read_json_auto('x.json')",
    "read text": "SELECT * FROM read_text('C:/Users/dheer/.env')",
    "read blob": "SELECT content FROM read_blob('x')",
    "file as a table": "SELECT * FROM 'C:/Users/dheer/.env'",
    "quoted file as a table": 'SELECT * FROM "secrets.csv"',
    "glob": "SELECT * FROM glob('C:/Users/*')",
    "sniff": "SELECT * FROM sniff_csv('x.csv')",
    "parquet metadata": "SELECT * FROM parquet_metadata('x.parquet')",
    "sqlite scan": "SELECT * FROM sqlite_scan('x.db', 't')",
    "postgres scan": "SELECT * FROM postgres_scan('dbname=x', 'public', 't')",
    "query function": "SELECT * FROM query('DROP TABLE marts.fct_orders')",
    "query_table": "SELECT * FROM query_table('raw.users')",
    "environment": "SELECT getenv('OPENAI_API_KEY')",
    "settings": "SELECT current_setting('enable_external_access')",
    "settings table": "SELECT * FROM duckdb_settings()",
    "secrets": "SELECT * FROM duckdb_secrets()",
    "system catalog": "SELECT * FROM information_schema.tables",
    "pg catalog": "SELECT * FROM pg_catalog.pg_tables",
    "sqlite master": "SELECT * FROM sqlite_master",
    "raw users": "SELECT * FROM raw.users",
    "staging users": "SELECT email FROM staging.stg_users",
    "unqualified table": "SELECT * FROM fct_orders",
    "another database": "SELECT * FROM other.marts.fct_orders",
    "hidden in a cte": "WITH u AS (SELECT * FROM raw.users) SELECT * FROM u",
    "hidden in a subquery": "SELECT 1 WHERE EXISTS (SELECT 1 FROM raw.users)",
    "hidden in a join": "SELECT 1 FROM marts.dim_city c JOIN staging.stg_users u ON true",
    "hidden in a union": "SELECT city FROM marts.dim_city UNION ALL SELECT email FROM raw.users",
    "function in a cte": "WITH f AS (SELECT * FROM read_csv('x')) SELECT * FROM f",
    "cte named like a table": "WITH raw AS (SELECT 1) SELECT * FROM raw.users",
    "email column": "SELECT email_masked FROM marts.dim_customer",
    "email hash": "SELECT c.email_hash FROM marts.dim_customer c",
    "email in a filter": "SELECT customer_id FROM marts.dim_customer WHERE email_hash = 'x'",
    "star on customers": "SELECT * FROM marts.dim_customer",
    "star via an alias": "SELECT c.* FROM marts.dim_customer c",
    "star in a cte": "WITH c AS (SELECT * FROM marts.dim_customer) SELECT 1 FROM c",
    "whole row by alias": "SELECT c FROM marts.dim_customer c",
    "whole row by table name": "SELECT dim_customer FROM marts.dim_customer",
    "whole row as json": "SELECT to_json(c) FROM marts.dim_customer c",
    "columns star": "SELECT COLUMNS(*) FROM marts.dim_customer",
    "columns by pattern": "SELECT COLUMNS('email.*') FROM marts.dim_customer",
    "columns by lambda": "SELECT COLUMNS(c -> c LIKE '%email%') FROM marts.dim_customer",
    "unpivot": "SELECT * FROM (UNPIVOT marts.dim_customer ON COLUMNS(*))",
    "from first": "FROM raw.users SELECT *",
    "from only": "FROM raw.users",
    "from a file first": "FROM 'C:/Users/dheer/.env'",
    "from a function first": "FROM read_csv('x') SELECT *",
    "pivot a file": "PIVOT 'x.csv' ON a USING sum(b)",
    "pivot a table": "PIVOT raw.users ON city USING count(*)",
    "summarize": "SUMMARIZE raw.users",
    "table statement": "TABLE raw.users",
    "schema qualified function": "SELECT * FROM main.read_csv('x')",
    "system qualified function": "SELECT * FROM system.main.read_text('x')",
    "qualified scalar": "SELECT main.getenv('OPENAI_API_KEY')",
    "materialized cte": "WITH u AS MATERIALIZED (SELECT * FROM raw.users) SELECT 1 FROM u",
    "lateral": "SELECT 1 FROM marts.dim_city, LATERAL (SELECT * FROM raw.users) u",
    "uppercase schema": "SELECT * FROM RAW.USERS",
    "quoted schema": 'SELECT * FROM "raw"."users"',
    "quoted function": """SELECT * FROM "read_csv"('x')""",
    "file in a quoted schema": 'SELECT * FROM "marts"."x.csv"',
    "cte shadowing with a schema": "WITH fct AS (SELECT 1) SELECT * FROM raw.fct",
    "select into": "SELECT * INTO marts.copy FROM marts.dim_city",
    "recursive": "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT * FROM r",
    "not sql": "please delete everything",
    "nothing": "",
}

SAFE = {
    "simple": "SELECT city, orders FROM marts.mart_city_daily",
    "aggregate": "SELECT city, SUM(gmv) AS gmv FROM marts.mart_city_daily GROUP BY city",
    "join": "SELECT r.restaurant_name, o.order_id FROM marts.fct_orders o "
    "JOIN marts.dim_restaurant r ON r.restaurant_id = o.restaurant_id",
    "cte": "WITH c AS (SELECT city FROM marts.dim_city) SELECT * FROM c",
    "window": "SELECT city, ROW_NUMBER() OVER (ORDER BY orders) AS r FROM marts.mart_city_daily",
    "union": "SELECT city FROM marts.dim_city UNION SELECT city FROM marts.mart_city_daily",
    "customers": "SELECT customer_id, age_band FROM marts.dim_customer_public",
    "all of the public customers": "SELECT * FROM marts.dim_customer_public",
    "a word that looks bad": "SELECT 'drop table' AS note FROM marts.dim_city",
    "column named like a keyword": "SELECT order_status FROM marts.fct_orders",
}


@pytest.mark.parametrize("name", list(ATTACKS))
def test_attack_is_refused(name: str) -> None:
    with pytest.raises(GuardError):
        check_sql(ATTACKS[name])


@pytest.mark.parametrize("name", list(SAFE))
def test_ordinary_analysis_is_allowed(name: str) -> None:
    assert check_sql(SAFE[name])


def test_a_limit_is_added_when_missing() -> None:
    assert f"LIMIT {MAX_ROWS}" in check_sql("SELECT city FROM marts.dim_city")


def test_a_small_limit_is_kept() -> None:
    assert check_sql("SELECT city FROM marts.dim_city LIMIT 5").endswith("LIMIT 5")


def test_a_huge_limit_is_capped() -> None:
    sql = check_sql("SELECT city FROM marts.dim_city LIMIT 999999999")
    assert sql.endswith(f"LIMIT {MAX_ROWS}")


def test_a_computed_limit_is_capped_from_outside() -> None:
    sql = check_sql("SELECT city FROM marts.dim_city LIMIT 10 + 999999")
    assert sql.endswith(f"LIMIT {MAX_ROWS}")


def test_a_union_is_limited() -> None:
    sql = check_sql("SELECT city FROM marts.dim_city UNION SELECT city FROM marts.dim_city")
    assert f"LIMIT {MAX_ROWS}" in sql


def test_the_refusal_explains_itself_for_the_repair_prompt() -> None:
    with pytest.raises(GuardError, match=r"table raw\.users is not allowed"):
        check_sql("SELECT * FROM raw.users")


def test_no_allowed_table_holds_email() -> None:
    assert "marts.dim_customer" not in ALLOWED_TABLES


def test_only_the_rebuilt_sql_runs_so_a_comment_cannot_smuggle_anything() -> None:
    checked = check_sql("SELECT city /* ; DROP TABLE x */ FROM marts.dim_city -- ; DELETE")
    assert "DROP" not in checked and "DELETE" not in checked


def test_every_allowed_table_is_in_a_known_schema() -> None:
    assert {name.split(".")[0] for name in ALLOWED_TABLES} == {"marts", "governance", "forecast"}
    assert not any("stg_" in name or name.startswith("raw.") for name in ALLOWED_TABLES)


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA marts")
    con.execute("CREATE TABLE marts.dim_city AS SELECT 'Pune' AS city")
    con.close()
    return db


def test_checked_sql_runs(warehouse: Path) -> None:
    frame = run_readonly(warehouse, check_sql("SELECT city FROM marts.dim_city"))
    assert frame["city"].tolist() == ["Pune"]


def test_the_connection_cannot_write_even_without_the_guard(warehouse: Path) -> None:
    with pytest.raises(duckdb.Error):
        run_readonly(warehouse, "DELETE FROM marts.dim_city")


def test_the_connection_cannot_read_files_even_without_the_guard(
    warehouse: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secret.csv"
    secret.write_text("a\n1\n", encoding="utf-8")
    with pytest.raises(duckdb.Error):
        run_readonly(warehouse, f"SELECT * FROM read_csv('{secret.as_posix()}')")


def test_the_connection_cannot_turn_file_access_back_on(warehouse: Path) -> None:
    with pytest.raises(duckdb.Error):
        run_readonly(warehouse, "SET enable_external_access = true")


def test_a_slow_query_is_stopped(warehouse: Path) -> None:
    slow = "SELECT count(*) FROM range(100000000) a, range(100000000) b"
    with pytest.raises(duckdb.Error):
        run_readonly(warehouse, slow, timeout=0.2)
