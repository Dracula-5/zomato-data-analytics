"""The copilot end to end, with a fake model and a small SYNTHETIC warehouse."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from helpers import REPO_ROOT
from zap.agent import evaluate as agent_eval
from zap.agent import router, tools, validate
from zap.agent.graph import NEEDS_MODEL, Copilot, render_rows
from zap.agent.trace import Tracer, read_trace
from zap.config import Settings, get_settings
from zap.llm.client import LlmClient
from zap.rag.corpus import Chunk
from zap.sqlutil import save_frames


class Script:
    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[str] = []

    def _reply(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs["input"])
        return SimpleNamespace(
            status="completed",
            incomplete_details=None,
            output_text=self.answer(kwargs["instructions"], kwargs["input"]),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    create = _reply
    parse = _reply


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    db = tmp_path / "full.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA marts")
    con.execute("CREATE TABLE marts.dim_city AS SELECT 'Pune' AS city, 12 AS restaurants")
    con.execute("CREATE TABLE marts.empty_marker AS SELECT 1 AS x")
    con.execute(
        "CREATE TABLE marts.fct_orders AS "
        "SELECT range AS order_id, DATE '2026-06-01' AS order_date FROM range(5)"
    )
    con.close()
    save_frames(
        db,
        "forecast",
        {
            "accuracy": pd.DataFrame(
                {
                    "metric": ["orders", "orders"],
                    "scope": ["National", "Pune"],
                    "model": ["ets", "ets"],
                    "mape": [0.64, 3.1],
                    "smape": [0.64, 3.2],
                    "bias_pct": [-0.2, 0.1],
                }
            ),
            "backtest": pd.DataFrame(
                {
                    "metric": ["orders"],
                    "scope": ["Pune"],
                    "model": ["ets"],
                    "fold": [1],
                    "target_date": [pd.Timestamp("2026-06-30")],
                    "actual": [100.0],
                    "forecast": [101.0],
                }
            ),
            "next_days": pd.DataFrame(
                {
                    "metric": ["orders"] * 2,
                    "scope": ["Pune"] * 2,
                    "model": ["ets"] * 2,
                    "target_date": pd.to_datetime(["2026-07-01", "2026-07-02"]),
                    "forecast": [1234.4, 1300.6],
                }
            ),
            "anomalies": pd.DataFrame(
                {
                    "metric": ["orders"],
                    "scope": ["Pune"],
                    "order_date": [pd.Timestamp("2026-05-01")],
                    "actual": [900.0],
                    "expected": [500.0],
                    "z": [5.5],
                }
            ),
        },
    )
    save_frames(
        db,
        "kg",
        {
            "signal": pd.DataFrame({"measure": ["x"], "value": [1.0]}),
            "communities": pd.DataFrame(
                {
                    "community": [1, 2],
                    "restaurants": [25, 12],
                    "top_name": ["Pizza Hut", "Badmash Biryani"],
                    "name_purity": [1.0, 0.25],
                    "top_cuisine": ["Pizzas", "Biryani"],
                    "cuisine_purity": [1.0, 1.0],
                    "cities": [14, 2],
                }
            ),
            "top_restaurants": pd.DataFrame({"name": ["Chapter 2"], "pagerank": [3.9]}),
            "recommendations": pd.DataFrame({"name": ["Faasos"], "score": [5.1]}),
            "outlier_tests": pd.DataFrame({"test": ["Cancel"], "after_fdr": [0]}),
        },
    )
    return Settings(
        warehouse_dir=tmp_path,
        artifacts_dir=tmp_path / "artifacts",
        llm_replay_dir=tmp_path / "replay",
    )


def copilot_with(settings: Settings, answer: Any | None = None) -> tuple[Copilot, Settings]:
    if answer is None:
        client = LlmClient(settings)  # replay with nothing saved: the model is unavailable
    else:
        live = settings.model_copy(update={"llm_mode": "live"})
        client = LlmClient(live, api=SimpleNamespace(responses=Script(answer)))
    context = tools.Context(settings, client)
    context.schema = "marts.dim_city: cities\n  city VARCHAR"  # type: ignore[misc]
    return Copilot(context, Tracer(settings.artifacts_dir / "trace.jsonl")), settings


def route_and_sql(tool: str, sql: str) -> Any:
    def answer(instructions: str, _: str) -> str:
        if instructions.startswith("You send a question"):
            return json.dumps({"tool": tool, "reason": "test"})
        return json.dumps({"sql": sql})

    return answer


@pytest.mark.parametrize(
    ("question", "tool"),
    [
        ("How many orders were there?", "sql"),
        ("What does REV_BEFORE_ORDER check?", "rag"),
        ("Why does Terraform run twice?", "rag"),
        ("Which chains were found?", "graph"),
        ("Who is most central by PageRank?", "graph"),
        ("Forecast next week's orders", "forecast"),
        ("Any anomalies in Pune?", "forecast"),
    ],
)
def test_keyword_router(question: str, tool: str) -> None:
    assert router.keyword_route(question) == tool


def test_the_golden_set_covers_every_tool() -> None:
    questions = agent_eval.load_golden(REPO_ROOT / "eval" / "agent_golden.yml")
    assert len(questions) >= 25
    assert {q.tool for q in questions} == set(router.TOOLS)
    assert len({q.id for q in questions}) == len(questions)


def test_router_scoring_builds_a_confusion_matrix() -> None:
    questions = [
        agent_eval.GoldRoute("a", "sql", "x"),
        agent_eval.GoldRoute("b", "rag", "y"),
    ]
    result = agent_eval.score_router(lambda _: "sql", questions)
    assert result["accuracy"] == 0.5
    assert result["confusion"]["rag"]["sql"] == 1
    assert result["wrong"][0]["id"] == "b"


def test_an_unavailable_model_is_reported_not_faked(tmp_path: Path) -> None:
    client = LlmClient(Settings(llm_replay_dir=tmp_path))
    questions = [agent_eval.GoldRoute("a", "sql", "x")]
    report = agent_eval.evaluate_routing(client, questions)
    assert report["llm_router"] == "not run"
    assert report["keyword_router"]["accuracy"] == 1.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [("1,234 orders and 5.5%", [1234.0, 5.5]), ("no numbers", []), ("-3 and 0.25", [-3.0, 0.25])],
)
def test_numbers_are_read_from_text(text: str, expected: list[float]) -> None:
    assert validate.numbers_in(text) == expected


def test_a_number_in_the_tool_output_passes() -> None:
    assert validate.check_numbers("GMV was 4,418,169,887", [4418169887.0]).passed


def test_an_invented_number_fails() -> None:
    check = validate.check_numbers("GMV was 9,999,999", [4418169887.0])
    assert not check.passed and "9999999" in check.detail


def test_a_rounded_number_passes_and_a_changed_one_does_not() -> None:
    assert validate.check_numbers("about 482.3", [482.29]).passed
    assert not validate.check_numbers("about 482.9", [482.29]).passed


def test_a_percentage_of_a_share_passes() -> None:
    assert validate.check_numbers("61.56%", [0.6156]).passed


def test_small_counts_are_allowed_in_prose() -> None:
    assert validate.check_numbers("the top 5 cities", []).passed


def test_citations_must_be_retrieved() -> None:
    assert validate.check_citations(["a"], ["a", "b"]).passed
    assert not validate.check_citations(["z"], ["a"]).passed


def test_an_empty_result_fails() -> None:
    assert not validate.check_not_empty([]).passed


def test_sql_is_checked_again_after_the_fact() -> None:
    assert validate.check_sql_passes_guard("SELECT city FROM marts.dim_city").passed
    assert not validate.check_sql_passes_guard("DROP TABLE x").passed


def test_values_come_from_numbers_and_from_text() -> None:
    assert validate.values_in([{"a": 5, "b": "held 1,000 rows", "c": True}]) == [5.0, 1000.0]


def test_expensive_means_a_huge_table_with_no_date_limit() -> None:
    assert tools.is_expensive("SELECT COUNT(*) FROM marts.fct_order_items")
    assert not tools.is_expensive("SELECT city FROM marts.dim_city")
    assert not tools.is_expensive(
        "SELECT COUNT(*) FROM marts.fct_orders WHERE order_date = '2026-06-01'"
    )


def test_rendering_one_value_reads_as_a_sentence() -> None:
    assert render_rows([{"n": 10000000}]) == "The answer is 10,000,000."


def test_rendering_many_rows_says_how_many_were_left_out() -> None:
    text = render_rows([{"i": n} for n in range(13)])
    assert "(3 more rows.)" in text


def test_ids_keep_their_plain_form_and_big_decimals_stay_readable() -> None:
    assert render_rows([{"id": 292634, "orders": 37000}]) == "id: 292634; orders: 37,000"
    assert render_rows([{"a": 12345.678, "b": 0.0612}]) == "a: 12,345.68; b: 0.0612"


def test_a_rendered_big_decimal_still_traces_to_its_source() -> None:
    assert validate.check_numbers(render_rows([{"a": 12345.678}]), [12345.678]).passed


def test_forecast_questions_need_no_model(settings: Settings) -> None:
    copilot, _ = copilot_with(settings)
    result = copilot.ask("What are the forecast orders for next week in Pune?")
    assert result["tool"] == "forecast" and result["routed_by"] == "keywords"
    assert result["source"] == "forecast.next_days"
    assert "1,234" in result["answer"] and "00:00:00" not in result["answer"]
    assert all(check["passed"] for check in result["checks"])


def test_forecast_accuracy_and_anomalies_are_answered(settings: Settings) -> None:
    copilot, _ = copilot_with(settings)
    assert "0.64" in copilot.ask("How accurate is the forecast?")["answer"]
    assert copilot.ask("Were there anomalies in Pune?")["source"] == "forecast.anomalies"


def test_graph_questions_need_no_model(settings: Settings) -> None:
    copilot, _ = copilot_with(settings)
    chains = copilot.ask("Which chains were found?")["answer"]
    assert "Pizza Hut" in chains and "Badmash" not in chains
    shared = copilot.ask("Which brands share a kitchen?")["answer"]
    assert "Badmash" in shared and "Pizza Hut" not in shared
    assert "Chapter 2" in copilot.ask("Who is most central?")["answer"]
    assert "Faasos" in copilot.ask("Recommend something")["answer"]


def test_a_sql_question_says_plainly_when_the_model_is_missing(settings: Settings) -> None:
    copilot, _ = copilot_with(settings)
    assert copilot.ask("How many cities are there?")["answer"] == NEEDS_MODEL


def test_a_rag_question_says_plainly_when_the_model_is_missing(settings: Settings) -> None:
    copilot, _ = copilot_with(settings)
    copilot.context.retriever = SimpleNamespace(  # type: ignore[misc]
        search=lambda *_: ["a"], chunks={"a": Chunk("a", "d", "T", "text")}
    )
    assert copilot.ask("Why was clustering left off?")["answer"] == NEEDS_MODEL


def test_cheap_sql_runs_without_asking(settings: Settings) -> None:
    copilot, _ = copilot_with(
        settings, route_and_sql("sql", "SELECT restaurants FROM marts.dim_city")
    )
    result = copilot.ask("How many restaurants?")
    assert not result["needs_approval"]
    assert result["answer"] == "The answer is 12."
    assert result["routed_by"] == "llm"


def test_an_expensive_query_waits_for_approval(settings: Settings) -> None:
    sql = "SELECT COUNT(*) AS n FROM marts.fct_orders"
    copilot, _ = copilot_with(settings, route_and_sql("sql", sql))
    paused = copilot.ask("How many orders?")
    assert paused["needs_approval"] and "fct_orders" in paused["pending"]["sql"]
    assert "answer" not in paused or not paused.get("answer")
    done = copilot.resume(paused["run_id"], True)
    assert done["answer"] == "The answer is 5."


def test_a_denied_query_never_runs(settings: Settings) -> None:
    sql = "SELECT COUNT(*) AS n FROM marts.fct_orders"
    copilot, _ = copilot_with(settings, route_and_sql("sql", sql))
    paused = copilot.ask("How many orders?")
    done = copilot.resume(paused["run_id"], False)
    assert done["answer"] == "Not run: the query was declined."
    assert "rows" not in done or not done["rows"]


def test_unsafe_sql_is_never_run_and_is_explained(settings: Settings) -> None:
    copilot, _ = copilot_with(settings, route_and_sql("sql", "DROP TABLE marts.dim_city"))
    result = copilot.ask("Delete everything")
    assert "No safe query" in result["answer"]
    con = duckdb.connect(str(settings.duckdb_path("full")), read_only=True)
    assert con.execute("select count(*) from marts.dim_city").fetchone() == (1,)
    con.close()


def test_an_empty_result_is_blocked(settings: Settings) -> None:
    sql = "SELECT city FROM marts.dim_city WHERE city = 'Nowhere'"
    copilot, _ = copilot_with(settings, route_and_sql("sql", sql))
    result = copilot.ask("Any?")
    assert "failed its checks" in result["answer"]


def test_a_model_answer_with_an_invented_number_is_blocked(settings: Settings) -> None:
    def answer(instructions: str, _: str) -> str:
        if instructions.startswith("You send a question"):
            return json.dumps({"tool": "rag", "reason": "test"})
        return json.dumps({"found": True, "answer": "GMV was 987,654.", "citations": [1]})

    copilot, _ = copilot_with(settings, answer)
    copilot.context.retriever = SimpleNamespace(  # type: ignore[misc]
        search=lambda *_: ["a"], chunks={"a": Chunk("a", "d", "Title", "Some text")}
    )
    result = copilot.ask("What was GMV?")
    assert "failed its checks" in result["answer"] and "987654" in result["answer"]


def test_a_supported_rag_answer_is_shown(settings: Settings) -> None:
    def answer(instructions: str, _: str) -> str:
        if instructions.startswith("You send a question"):
            return json.dumps({"tool": "rag", "reason": "test"})
        return json.dumps({"found": True, "answer": "It held 1,500 rows.", "citations": [1]})

    copilot, _ = copilot_with(settings, answer)
    copilot.context.retriever = SimpleNamespace(  # type: ignore[misc]
        search=lambda *_: ["a"], chunks={"a": Chunk("a", "d", "T", "It held 1,500 rows")}
    )
    result = copilot.ask("How big?")
    assert result["answer"] == "It held 1,500 rows."
    assert result["cited"] == ["a"]


def test_a_tool_failure_becomes_a_message(settings: Settings) -> None:
    copilot, _ = copilot_with(settings)
    copilot.context.graph_warehouse = None
    result = copilot.ask("Which chains were found?")
    assert "graph tool failed" in result["answer"]


def test_every_step_is_traced(settings: Settings) -> None:
    copilot, resolved = copilot_with(settings)
    result = copilot.ask("Which chains were found?")
    steps = read_trace(resolved.artifacts_dir / "trace.jsonl", result["run_id"])
    assert [s["node"] for s in steps] == ["route", "plan", "execute", "validate", "answer"]
    assert steps[2]["output"]["rows"] == "1 rows"  # the chain, not the multi-brand group


def test_no_trace_file_means_no_steps(tmp_path: Path) -> None:
    assert read_trace(tmp_path / "none.jsonl", "x") == []


def test_the_page_answers_a_free_question(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("WAREHOUSE_DIR", str(settings.warehouse_dir))
    monkeypatch.setenv("ARTIFACTS_DIR", str(settings.artifacts_dir))
    monkeypatch.setenv("LLM_REPLAY_DIR", str(settings.llm_replay_dir))
    get_settings.cache_clear()
    page = AppTest.from_file(str(REPO_ROOT / "app/views/7_Copilot.py"), default_timeout=120).run()
    assert not page.exception
    examples = [button.label for button in page.main.button]
    assert "Which restaurant chains did the graph find?" in examples

    page.chat_input[0].set_value("Which chains were found?").run()
    assert not page.exception
    assert any("Pizza Hut" in m.value for m in page.markdown)  # one row reads as text
    assert any("passed: result is not empty" in c.value for c in page.caption)
    assert not page.main.button  # the examples make way for the conversation

    page.chat_input[0].set_value("Show the restaurant communities").run()
    assert not page.exception
    assert len(page.dataframe) == 1  # several rows become a table

    page.sidebar.button[0].click().run()  # New chat
    get_settings.cache_clear()
    assert not page.exception
    assert not page.dataframe
    assert [button.label for button in page.main.button] == examples
