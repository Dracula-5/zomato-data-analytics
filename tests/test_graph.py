"""Graph logic with a fake driver: no server needed. test_graph_live.py uses a real Neo4j."""

from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest
from pydantic import SecretStr
from statsmodels.stats.multitest import multipletests
from typer.testing import CliRunner

from zap import sqlutil
from zap.cli import app
from zap.config import Settings, get_settings
from zap.dashboard import data
from zap.kg import analysis, full_checks, gds, load_neo4j, stats
from zap.kg.load_neo4j import LABELS, STEPS, Step, StepResult


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def consume(self) -> None:
        pass

    def single(self, strict: bool = False) -> "FakeRecord":
        return FakeRecord(self.rows[0])

    def __iter__(self) -> Any:
        return iter(FakeRecord(row) for row in self.rows)


class FakeRecord:
    def __init__(self, row: dict[str, Any]) -> None:
        self.row = row

    def __getitem__(self, index: int) -> Any:
        return list(self.row.values())[index]

    def data(self) -> dict[str, Any]:
        return self.row


class FakeTx:
    def __init__(self, driver: "FakeDriver") -> None:
        self.driver = driver

    def run(self, query: str, **params: Any) -> FakeResult:
        self.driver.writes.append((query, params))
        return FakeResult([])


class FakeSession:
    def __init__(self, driver: "FakeDriver") -> None:
        self.driver = driver

    def __enter__(self) -> "FakeSession":
        return self

    def __exit__(self, *_: object) -> None:
        pass

    def run(self, query: str, params: dict[str, Any] | None = None, **more: Any) -> FakeResult:
        self.driver.queries.append(query)
        if self.driver.fail_on and self.driver.fail_on in query:
            raise RuntimeError("server said no")
        return FakeResult(self.driver.respond(query))

    def execute_write(self, fn: Any, *args: Any) -> None:
        fn(FakeTx(self.driver), *args)


class FakeDriver:
    def __init__(self, respond: Any = None, fail_on: str | None = None) -> None:
        self.respond = respond or (lambda _: [{"n": 0}])
        self.fail_on = fail_on
        self.queries: list[str] = []
        self.writes: list[tuple[str, dict[str, Any]]] = []

    def session(self) -> FakeSession:
        return FakeSession(self)


def test_fdr_matches_a_reference_implementation() -> None:
    p = np.random.default_rng(1).uniform(size=500) ** 3
    expected = multipletests(p, method="fdr_bh")[1]
    np.testing.assert_allclose(stats.benjamini_hochberg(p), expected)


def test_fdr_keeps_the_input_order_and_handles_nothing() -> None:
    assert stats.benjamini_hochberg(np.array([0.5, 0.01])).tolist() == [0.5, 0.02]
    assert stats.benjamini_hochberg(np.array([])).size == 0


def test_counting_every_possible_pair_makes_fdr_stricter() -> None:
    p = np.array([1e-6, 1e-3])
    assert stats.benjamini_hochberg(p, total_tests=1000)[1] == pytest.approx(0.5)
    assert stats.benjamini_hochberg(p)[1] == pytest.approx(1e-3)


def test_binomial_tail_on_a_known_value() -> None:
    # Ten heads in ten fair flips: 1 / 1024.
    assert stats.high_rate_p(np.array([10]), np.array([10]), 0.5)[0] == pytest.approx(1 / 1024)
    assert stats.high_rate_p(np.array([0]), np.array([10]), 0.5)[0] == pytest.approx(1.0)


def test_hypergeometric_tail_on_a_known_value() -> None:
    # 10 orders, each dish in 5: both in the same 5 orders has one chance in C(10, 5) = 252.
    p = stats.together_p(np.array([5]), np.array([5]), np.array([5]), 10)
    assert p[0] == pytest.approx(1 / 252)


def test_identical_restaurants_give_no_outliers_after_correction() -> None:
    rng = np.random.default_rng(2)
    orders = rng.integers(20, 40, 3000)
    frame = pd.DataFrame({"orders": orders, "cancelled": rng.binomial(orders, 0.06)})
    summary = stats.outlier_summary(stats.rate_outliers(frame, 0.06))
    assert summary["raw_p_below_alpha"] > 20  # a naive list looks alarming
    assert summary["after_fdr"] == 0


def test_a_real_outlier_survives_correction() -> None:
    rng = np.random.default_rng(3)
    orders = rng.integers(20, 40, 2000)
    cancelled = rng.binomial(orders, 0.06)
    orders[7], cancelled[7] = 200, 80
    frame = pd.DataFrame({"id": range(2000), "orders": orders, "cancelled": cancelled})
    scored = stats.rate_outliers(frame, 0.06)
    assert scored.iloc[0]["id"] == 7 and scored.iloc[0]["q"] < stats.ALPHA


def test_power_check_rises_with_the_size_of_the_effect() -> None:
    rng = np.random.default_rng(4)
    power = stats.planted_power(np.full(5000, 100), 0.06, rng)
    assert power["2x"]["recall"] < power["5x"]["recall"]
    assert power["5x"]["recall"] > 0.9


def test_every_label_has_a_constraint_and_a_step() -> None:
    driver = FakeDriver()
    load_neo4j.create_constraints(driver)  # type: ignore[arg-type]
    for label in LABELS:
        assert any(f"FOR (n:{label}) REQUIRE n.id IS UNIQUE" in q for q in driver.queries)
        assert any(step.name == label for step in STEPS)


def test_no_personal_data_reaches_the_graph() -> None:
    selected = " ".join(step.sql.lower() for step in STEPS)
    for column in ("email", "phone", "password", "address", "licence", "source_url"):
        assert column not in selected
    user = next(step for step in STEPS if step.name == "User")
    assert "name" not in user.sql.lower().split("from")[0]


def test_nodes_and_relationships_use_merge() -> None:
    assert all("MERGE" in step.write and "CREATE" not in step.write for step in STEPS)


def test_review_relationships_stay_separate() -> None:
    reviewed = next(step for step in STEPS if "REVIEWED" in step.name)
    assert "r:REVIEWED{review_id: row.props.review_id}" in reviewed.write


@pytest.fixture
def small_db(tmp_path: Path) -> Path:
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE TABLE t AS SELECT range AS id, 'x' AS name FROM range(25)")
    con.execute("CREATE TABLE e AS SELECT 'a' AS a, 'b' AS b, 3 AS qty")
    con.close()
    return db


def test_rows_are_sent_in_batches(small_db: Path) -> None:
    driver = FakeDriver(lambda _: [{"n": 25}])
    con = duckdb.connect(str(small_db), read_only=True)
    step = Step("T", "select * from t", "UNWIND", "COUNT")
    result = load_neo4j.run_step(driver, con, step, batch_size=10)  # type: ignore[arg-type]
    con.close()
    assert [len(params["rows"]) for _, params in driver.writes] == [10, 10, 5]
    assert result.ok and result.rows_sent == 25


def test_relationship_rows_separate_their_properties(small_db: Path) -> None:
    driver = FakeDriver(lambda _: [{"n": 1}])
    con = duckdb.connect(str(small_db), read_only=True)
    load_neo4j.run_step(driver, con, Step("E", "select * from e", "W", "C"))  # type: ignore[arg-type]
    con.close()
    assert driver.writes[0][1]["rows"] == [{"a": "a", "b": "b", "props": {"qty": 3}}]


def test_a_lost_relationship_is_a_mismatch(small_db: Path) -> None:
    driver = FakeDriver(lambda _: [{"n": 24}])
    con = duckdb.connect(str(small_db), read_only=True)
    result = load_neo4j.run_step(driver, con, Step("T", "select * from t", "W", "C"))  # type: ignore[arg-type]
    con.close()
    assert not result.ok


def test_connect_needs_a_password() -> None:
    with pytest.raises(ValueError, match="NEO4J_PASSWORD"):
        load_neo4j.connect(Settings(neo4j_password=None))
    driver = load_neo4j.connect(Settings(neo4j_password=SecretStr("not-used")))
    driver.close()


def test_reset_deletes_in_batches() -> None:
    driver = FakeDriver()
    load_neo4j.clear_graph(driver)  # type: ignore[arg-type]
    assert all("IN TRANSACTIONS OF 10000 ROWS" in query for query in driver.queries)
    assert "DELETE r" in driver.queries[0] and "DELETE n" in driver.queries[1]
    assert not any("DETACH" in query for query in driver.queries)  # it ran out of memory


def answering_gds(query: str) -> list[dict[str, Any]]:
    if "nodeSimilarity" in query:
        return [{"nodesCompared": 9, "relationshipsWritten": 4}]
    if "louvain" in query:
        return [{"communityCount": 2, "modularity": 0.5, "nodePropertiesWritten": 4}]
    if "pageRank" in query:
        return [{"ranIterations": 20, "didConverge": True, "nodePropertiesWritten": 9}]
    return [{"nodeCount": 9}]


def test_gds_replaces_the_last_run_and_frees_memory() -> None:
    driver = FakeDriver(answering_gds)
    result = gds.run_gds(driver)  # type: ignore[arg-type]
    assert result["similarity"]["relationshipsWritten"] == 4
    assert result["settings"]["similarity_cutoff"] == gds.SIMILARITY_CUTOFF
    delete_at = next(i for i, q in enumerate(driver.queries) if "DELETE r" in q)
    write_at = next(i for i, q in enumerate(driver.queries) if "nodeSimilarity.write" in q)
    assert delete_at < write_at
    assert sum("gds.graph.drop" in q for q in driver.queries[write_at:]) == len(gds.PROJECTIONS)


def test_gds_skips_communities_when_no_menus_match() -> None:
    def no_matches(query: str) -> list[dict[str, Any]]:
        if "nodeSimilarity" in query:
            return [{"nodesCompared": 9, "relationshipsWritten": 0}]
        return answering_gds(query)

    driver = FakeDriver(no_matches)
    result = gds.run_gds(driver)  # type: ignore[arg-type]
    assert result["communities"] == {"communityCount": 0}
    assert not any("louvain" in q for q in driver.queries)


def test_gds_frees_memory_even_when_an_algorithm_fails() -> None:
    driver = FakeDriver(answering_gds, fail_on="gds.louvain.write")
    with pytest.raises(RuntimeError, match="server said no"):
        gds.run_gds(driver)  # type: ignore[arg-type]
    assert "gds.graph.drop" in driver.queries[-1]


@pytest.mark.parametrize(
    ("name", "key"),
    [("Domino's Pizza", "dominos pizza"), ("DOMINOS  PIZZA!", "dominos pizza"), (None, "")],
)
def test_chain_key_ignores_spelling_noise(name: str | None, key: str) -> None:
    assert analysis.chain_key(name) == key


def members_frame() -> pd.DataFrame:
    rows = [(1, f"r{i}", "Pizza Hut", "Pizza", f"City{i}") for i in range(6)]
    rows += [(2, f"s{i}", "KFC", "Burger", "Pune") for i in range(4)]
    rows += [(3, "t1", "Solo", "Thai", "Goa"), (3, "t2", "Other", "Thai", "Goa")]
    return pd.DataFrame(rows, columns=["community", "id", "name", "cuisine", "city"])


def test_community_table_describes_each_group() -> None:
    table = analysis.community_table(members_frame())
    top = table.iloc[0]
    assert (top["restaurants"], top["top_name"], top["name_purity"]) == (6, "Pizza Hut", 1.0)
    assert top["cities"] == 6 and top["top_cuisine"] == "Pizza"


def test_true_chains_beat_shuffled_names() -> None:
    table, scores = analysis.judge_communities(members_frame())
    assert len(table) == 2  # the pair is below the minimum size
    assert scores["name_purity"] == 1.0
    assert scores["name_purity_if_shuffled"] < 1.0
    assert scores["chains_found"] == 1  # KFC has only 4 outlets here


def test_no_communities_is_a_valid_result() -> None:
    table, scores = analysis.judge_communities(pd.DataFrame())
    assert table.empty and scores["communities"] == 0 and scores["name_purity"] is None


def test_pagerank_that_only_repeats_order_counts_is_called_out() -> None:
    ranked = pd.DataFrame({"pagerank": [3.0, 2.0, 1.0], "orders": [30, 20, 10]})
    assert analysis.judge_pagerank(ranked)["spearman_vs_order_count"] == 1.0


def test_every_query_file_is_documented_and_used() -> None:
    source = Path(analysis.__file__).read_text(encoding="utf-8")
    files = sorted(analysis.QUERY_DIR.glob("*.cypher"))
    assert len(files) >= 6
    for path in files:
        assert path.read_text(encoding="utf-8").startswith("//")
        assert f'"{path.stem}"' in source


def restaurant_pool_baskets(seed: int = 5) -> pd.DataFrame:
    """SYNTHETIC: 300 restaurants, each selling 5 dishes of its own, with no buying habits."""
    rng = np.random.default_rng(seed)
    rows = []
    for restaurant in range(300):
        pool = [f"r{restaurant}d{i}" for i in range(5)]
        for order in range(40):
            for dish in rng.choice(pool, 2, replace=False):
                rows.append((restaurant, f"{restaurant}-{order}", dish))
    return pd.DataFrame(rows, columns=["restaurant", "order", "dish"])


def pair_counts(lines: pd.DataFrame) -> pd.DataFrame:
    paired = lines.merge(lines, on=["restaurant", "order"])
    paired = paired[paired["dish_x"] < paired["dish_y"]]
    both = paired.groupby(["restaurant", "dish_x", "dish_y"]).size().rename("both").reset_index()
    dish_orders = lines.groupby("dish")["order"].nunique()
    both["orders_a"] = both["dish_x"].map(dish_orders)
    both["orders_b"] = both["dish_y"].map(dish_orders)
    both["orders_here"] = 40
    return both


def test_a_shared_restaurant_fools_the_naive_test_but_not_the_stratified_one() -> None:
    """The trap in this data: dishes share orders only because they share a restaurant."""
    lines = restaurant_pool_baskets()
    pairs = pair_counts(lines)
    pairs = pairs[pairs["both"] >= 3]  # as the real query does
    orders = lines["order"].nunique()
    dishes = lines["dish"].nunique()
    possible = dishes * (dishes - 1) // 2
    both, a, b = (pairs[c].to_numpy() for c in ("both", "orders_a", "orders_b"))
    naive = stats.benjamini_hochberg(stats.together_p(both, a, b, orders), possible)
    within_p = stats.pairs_within_p(both, a, b, pairs["orders_here"].to_numpy())
    within = stats.benjamini_hochberg(within_p, 300 * 10)
    assert (naive < stats.ALPHA).mean() > 0.9  # nearly every pair looks like a habit
    assert (within < stats.ALPHA).sum() == 0  # and none of them is


def test_a_true_pairing_survives_the_stratified_test() -> None:
    # 40 orders, both dishes in 15 of them, always together.
    p = stats.pairs_within_p(np.array([15]), np.array([15]), np.array([15]), np.array([40]))
    assert stats.benjamini_hochberg(p, 3000)[0] < stats.ALPHA


def graph_answers(naive_pairs: list[dict[str, Any]], within: list[dict[str, Any]]) -> Any:
    def respond(query: str) -> list[dict[str, Any]]:
        if "count(f) AS dishes" in query:
            return [{"dishes": 100, "orders": 10_000}]
        if "sum(dishes * (dishes - 1) / 2)" in query:
            return [{"pairs": 500}]
        return within if "orders_here" in query else naive_pairs

    return respond


def test_co_ordered_reports_both_tests() -> None:
    naive = [{"name_a": "A", "name_b": "B", "both": 20, "orders_a": 30, "orders_b": 30}]
    within = [
        {"restaurant_id": 1, "restaurant": "R", "name_a": "A", "name_b": "B", "both": 20,
         "orders_a": 20, "orders_b": 20, "orders_here": 40},
        {"restaurant_id": 2, "restaurant": "S", "name_a": "C", "name_b": "D", "both": 3,
         "orders_a": 20, "orders_b": 20, "orders_here": 40},
    ]  # fmt: skip
    driver = FakeDriver(graph_answers(naive, within))
    pairs, scores = analysis.co_ordered(driver)  # type: ignore[arg-type]
    assert list(pairs["name_a"]) == ["A"]
    assert scores["naive_significant_pairs"] == 1
    assert scores["significant_within_a_restaurant"] == 1
    assert scores["possible_pairs_within_restaurants"] == 500


def test_no_co_ordered_pairs_is_a_valid_result() -> None:
    pairs, scores = analysis.co_ordered(FakeDriver(graph_answers([], [])))  # type: ignore[arg-type]
    assert pairs.empty and scores["significant_within_a_restaurant"] == 0
    assert scores["naive_significant_pairs"] == 0


def test_one_menu_under_several_names_counts_as_multi_brand() -> None:
    rows = [(1, f"a{i}", "Badmash Biryani") for i in range(4)]
    rows += [(1, f"b{i}", "Sanskari Biryani") for i in range(4)]
    rows += [(2, f"c{i}", "Pizza Hut") for i in range(9)]
    rows += [(3, "d1", "Solo"), (3, "d2", "Other"), (3, "d3", "Third")]
    members = pd.DataFrame(rows, columns=["community", "id", "name"])
    assert analysis.multi_brand_groups(members) == 1


def test_chain_recall_counts_outlets_with_a_menu() -> None:
    members = pd.DataFrame(
        [(1, "Subway")] * 9 + [(2, "Subway")] * 3 + [(3, "Small Place")] * 2,
        columns=["community", "name"],
    )
    menus = pd.DataFrame({"name": ["Subway"] * 15 + ["Small Place"] * 2})
    assert analysis.chain_recall(members, menus) == {
        "chains": 1,
        "share_in_largest_community": 0.6,  # 9 of Subway's 15, and Small Place is too small
    }
    assert analysis.chain_recall(pd.DataFrame(), menus)["chains"] == 0


@pytest.fixture
def full_warehouse(tmp_path: Path) -> Path:
    """SYNTHETIC: orders with one chronically cancelling restaurant and one real dish pairing."""
    rng = np.random.default_rng(6)
    orders, lines = [], []
    order_id = 0
    for restaurant in range(40):
        for _ in range(150):
            order_id += 1
            rate = 0.6 if restaurant == 0 else 0.05
            orders.append((order_id, restaurant, order_id % 200, bool(rng.random() < rate)))
            for dish in rng.choice(6, 2, replace=False):
                lines.append((order_id, f"r{restaurant}d{dish}"))
            if restaurant == 1 and rng.random() < 0.5:
                lines += [(order_id, "paired_x"), (order_id, "paired_y")]
    db = tmp_path / "full.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA marts")
    frames = {
        "fct_orders": pd.DataFrame(
            orders, columns=["order_id", "restaurant_id", "customer_id", "is_cancelled"]
        ),
        "fct_order_items": pd.DataFrame(lines, columns=["order_id", "food_id"]),
    }
    for name, frame in frames.items():
        con.register("frame_view", frame)
        con.execute(f"CREATE TABLE marts.{name} AS SELECT * FROM frame_view")
        con.unregister("frame_view")
    con.close()
    return db


def test_full_checks_find_what_was_planted(full_warehouse: Path) -> None:
    result = full_checks.run_full_checks(full_warehouse)
    assert result["cancellations"]["restaurants"]["after_fdr"] == 1
    pairs = result["dish_pairs_within_a_restaurant"]
    assert pairs["restaurants"] == 40 and pairs["significant_pairs"] >= 1
    assert set(pairs["planted_power_SYNTHETIC"]) == {"2x", "3x", "5x"}


def test_full_checks_cope_with_no_busy_restaurants(tmp_path: Path) -> None:
    db = tmp_path / "small.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA marts")
    con.execute(
        "CREATE TABLE marts.fct_orders AS SELECT range AS order_id, 1 AS restaurant_id, "
        "range AS customer_id, false AS is_cancelled FROM range(30)"
    )
    con.execute(
        "CREATE TABLE marts.fct_order_items AS SELECT range AS order_id, 'x' AS food_id "
        "FROM range(30)"
    )
    con.close()
    result = full_checks.run_full_checks(db)
    assert result["dish_pairs_within_a_restaurant"]["significant_pairs"] == 0


def test_save_frames_replaces_earlier_results(tmp_path: Path) -> None:
    db = tmp_path / "w.duckdb"
    sqlutil.save_frames(db, "kg", {"t": pd.DataFrame({"a": [1, 2]})})
    sqlutil.save_frames(db, "kg", {"t": pd.DataFrame({"a": [3]})})
    con = duckdb.connect(str(db), read_only=True)
    assert con.execute("select a from kg.t").fetchall() == [(3,)]
    con.close()


def test_the_page_finds_the_warehouse_that_holds_the_graph(tmp_path: Path) -> None:
    full, sample = tmp_path / "full.duckdb", tmp_path / "sample_5pct.duckdb"
    duckdb.connect(str(full)).close()
    duckdb.connect(str(sample)).close()
    assert data.graph_warehouse(full) is None
    sqlutil.save_frames(sample, "kg", {"signal": pd.DataFrame({"measure": ["a"], "value": [1.0]})})
    assert data.graph_warehouse(full) == sample  # full is the default, but has no graph


def report_for_tables(checks: object) -> dict[str, Any]:
    tested = {
        "tested": 10,
        "raw_p_below_alpha": 1,
        "after_fdr": 0,
        "planted_power_SYNTHETIC": {"5x": {"recall": 0.9, "false_discoveries": 0}},
    }
    return {
        "cancellations": {"restaurants": tested, "users": tested},
        "co_ordered": {"significant_within_a_restaurant": 0},
        "full_data_checks": checks,
        "communities": {
            "chains_found": 3,
            "multi_brand_groups": 1,
            "name_purity": None,
            "name_purity_if_shuffled": None,
            "chain_recall": {"share_in_largest_community": None},
        },
        "pagerank": {"spearman_vs_order_count": 0.98},
    }


def test_outlier_table_shows_the_full_data_when_it_ran() -> None:
    sample_only = analysis.outlier_table(report_for_tables("not run"))
    assert set(sample_only["data"]) == {"5% sample"} and len(sample_only) == 2
    tested = report_for_tables("not run")["cancellations"]
    pairs = {
        "pairs_tested": 7,
        "significant_pairs": 0,
        "planted_power_SYNTHETIC": {"5x": {"recall": 0.98}},
    }
    full = analysis.outlier_table(
        report_for_tables({"cancellations": tested, "dish_pairs_within_a_restaurant": pairs})
    )
    assert len(full) == 5
    assert full.iloc[-1]["test"] == "Dish pairs within a restaurant"
    assert full.iloc[-1]["finds_planted_5x"] == 0.98


def test_scores_leave_out_what_is_unknown() -> None:
    scores = analysis.score_table(report_for_tables("not run"))
    names = set(scores["measure"])
    assert "chains_found" in names and "pagerank_vs_order_count" in names
    assert "name_purity" not in names  # absent, rather than a misleading zero


def test_step_result_compares_counts() -> None:
    assert StepResult("x", 3, 3, 0.1).ok and not StepResult("x", 3, 2, 0.1).ok


def test_cli_needs_a_warehouse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(tmp_path))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["graph", "load", "--dataset", "nope"])
    get_settings.cache_clear()
    assert result.exit_code == 1
    assert "no warehouse" in result.output
