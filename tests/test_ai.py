"""Text-to-SQL, RAG and enrichment, with a fake model API: nothing here can spend money."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import pytest

from helpers import REPO_ROOT
from zap.config import Settings
from zap.enrich import enrich_reviews
from zap.llm.client import InvalidReply, LlmClient
from zap.rag import answer as rag_answer
from zap.rag import evaluate as rag_eval
from zap.rag.corpus import Chunk, build_corpus, markdown_chunks, rule_chunks
from zap.rag.retrieve import Retriever, reciprocal_rank_fusion
from zap.text2sql import evaluate as sql_eval
from zap.text2sql.generate import answer_question as ask_sql
from zap.text2sql.guard import check_sql
from zap.text2sql.schema_prompt import schema_prompt


class ScriptedResponses:
    """Answers from a list, or from a function of the prompt."""

    def __init__(self, answer: Any) -> None:
        self.answer = answer
        self.calls: list[dict[str, Any]] = []

    def _reply(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        if callable(self.answer):
            text = self.answer(kwargs["input"])
        else:
            text = self.answer[min(len(self.calls) - 1, len(self.answer) - 1)]
        return SimpleNamespace(
            status="completed",
            incomplete_details=None,
            output_text=text,
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )

    create = _reply
    parse = _reply


def client_with(tmp_path: Path, answer: Any) -> LlmClient:
    api = SimpleNamespace(responses=ScriptedResponses(answer))
    return LlmClient(Settings(llm_mode="live", llm_replay_dir=tmp_path / "replay"), api=api)


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    db = tmp_path / "w.duckdb"
    con = duckdb.connect(str(db))
    con.execute("CREATE SCHEMA marts")
    con.execute(
        "CREATE TABLE marts.dim_city AS "
        "SELECT * FROM (VALUES ('Pune', 10), ('Goa', 4)) t(city, restaurants)"
    )
    con.execute(
        "CREATE TABLE marts.fct_reviews AS SELECT * FROM (VALUES "
        "('Tasty', 5), ('Tasty', 4), ('Cold food', 1), ('It was okay', 3)) t(comment, rating)"
    )
    con.close()
    return db


def frame(**columns: Any) -> pd.DataFrame:
    return pd.DataFrame(columns)


def test_the_same_rows_match_whatever_the_columns_are_called() -> None:
    gold = frame(city=["Pune", "Goa"], gmv=[10, 20])
    answer = frame(town=["Goa", "Pune"], total=[20, 10])
    assert sql_eval.rows_match(gold, answer, ordered=False)


def test_a_ranking_must_be_in_the_right_order() -> None:
    gold = frame(city=["Pune", "Goa"], gmv=[20, 10])
    answer = frame(city=["Goa", "Pune"], gmv=[10, 20])
    assert sql_eval.rows_match(gold, answer, ordered=False)
    assert not sql_eval.rows_match(gold, answer, ordered=True)


def test_extra_label_columns_are_fine() -> None:
    gold = frame(gmv=[5.0])
    answer = frame(city=["Pune"], gmv=[5.0])
    assert sql_eval.rows_match(gold, answer, ordered=False)


def test_a_missing_column_fails() -> None:
    assert not sql_eval.rows_match(frame(a=[1], b=[2]), frame(a=[1]), ordered=False)


def test_a_different_row_count_fails() -> None:
    assert not sql_eval.rows_match(frame(a=[1]), frame(a=[1, 1]), ordered=False)


def test_values_paired_wrongly_fail_even_when_each_column_matches() -> None:
    gold = frame(city=["Pune", "Goa"], gmv=[10, 20])
    swapped = frame(city=["Pune", "Goa"], gmv=[20, 10])
    assert not sql_eval.rows_match(gold, swapped, ordered=False)


def test_numbers_compare_to_two_decimals() -> None:
    assert sql_eval.rows_match(frame(a=[1.2345]), frame(a=[1.2300]), ordered=False)
    assert not sql_eval.rows_match(frame(a=[1.25]), frame(a=[1.29]), ordered=False)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        (float("nan"), None),
        (pd.NaT, None),
        (True, True),
        (-0.001, 0.0),
        (pd.Timestamp("2026-01-05"), "2026-01-05"),
        (pd.Timestamp("2026-01-05 10:30"), "2026-01-05 10:30:00"),
        (" Pune ", "Pune"),
    ],
)
def test_values_have_one_form(value: Any, expected: Any) -> None:
    assert sql_eval.normal_value(value) == expected


def test_the_golden_sql_set_is_big_enough_and_safe() -> None:
    questions = sql_eval.load_golden(REPO_ROOT / "eval" / "text2sql_golden.yml")
    assert len(questions) >= 30
    assert len({q.id for q in questions}) == len(questions)
    assert {q.level for q in questions} == {"easy", "medium", "hard"}
    for question in questions:
        assert check_sql(question.gold_sql)


def test_good_sql_is_answered_first_time(warehouse: Path, tmp_path: Path) -> None:
    client = client_with(tmp_path, ['{"sql": "SELECT city FROM marts.dim_city ORDER BY city"}'])
    answer = ask_sql(client, warehouse, "schema", "List the cities")
    assert answer.ok and answer.attempts == 1
    assert answer.rows is not None and answer.rows["city"].tolist() == ["Goa", "Pune"]


def test_refused_sql_gets_one_repair_that_is_guarded_too(warehouse: Path, tmp_path: Path) -> None:
    client = client_with(
        tmp_path,
        ['{"sql": "SELECT * FROM raw.users"}', '{"sql": "SELECT city FROM marts.dim_city"}'],
    )
    answer = ask_sql(client, warehouse, "schema", "List the cities")
    assert answer.ok and answer.attempts == 2
    assert "not allowed" in answer.errors[0]
    assert "not allowed" in client._api.responses.calls[1]["input"]


def test_an_execution_error_is_sent_back(warehouse: Path, tmp_path: Path) -> None:
    client = client_with(
        tmp_path,
        [
            '{"sql": "SELECT nope FROM marts.dim_city"}',
            '{"sql": "SELECT city FROM marts.dim_city"}',
        ],
    )
    assert ask_sql(client, warehouse, "schema", "List the cities").attempts == 2


def test_it_stops_after_one_repair(warehouse: Path, tmp_path: Path) -> None:
    client = client_with(tmp_path, ['{"sql": "DROP TABLE marts.dim_city"}'])
    answer = ask_sql(client, warehouse, "schema", "Delete everything")
    assert not answer.ok and len(answer.errors) == 2
    con = duckdb.connect(str(warehouse), read_only=True)
    assert con.execute("select count(*) from marts.dim_city").fetchone() == (2,)
    con.close()


def test_sql_evaluation_scores_right_and_wrong_answers(warehouse: Path, tmp_path: Path) -> None:
    questions = [
        sql_eval.GoldQuestion("a", "easy", "right", "SELECT city FROM marts.dim_city"),
        sql_eval.GoldQuestion("b", "hard", "wrong", "SELECT restaurants FROM marts.dim_city"),
    ]

    def reply(prompt: str) -> str:
        return '{"sql": "SELECT city FROM marts.dim_city"}'

    report = sql_eval.evaluate(client_with(tmp_path, reply), warehouse, "schema", questions)
    assert report["execution_accuracy"] == 0.5
    assert report["by_level"] == {
        "easy": {"correct": 1, "questions": 1},
        "hard": {"correct": 0, "questions": 1},
    }


def test_schema_prompt_uses_real_columns_and_dbt_docs(warehouse: Path, tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    node = {
        "resource_type": "model",
        "schema": "marts",
        "name": "dim_city",
        "description": "One row per city.",
        "columns": {"city": {"description": "City name."}},
    }
    manifest.write_text(json.dumps({"nodes": {"model.x": node}}), encoding="utf-8")
    text = schema_prompt(warehouse, manifest)
    assert "marts.dim_city: One row per city." in text
    assert "  city VARCHAR: City name." in text
    assert "  restaurants INTEGER" in text
    assert "fct_reviews" in text  # exists and allowed, though undocumented here


def test_schema_prompt_leaves_out_tables_that_are_not_allowed(
    warehouse: Path, tmp_path: Path
) -> None:
    con = duckdb.connect(str(warehouse))
    con.execute("CREATE SCHEMA raw")
    con.execute("CREATE TABLE raw.users AS SELECT 'x' AS password")
    con.close()
    assert "password" not in schema_prompt(warehouse, tmp_path / "missing.json")


def test_markdown_keeps_the_path_of_headings(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(
        "# A\n\nintro\n\n## B\n\nbody\n\n### C\n\ndeep\n\n## D\n\nend\n", encoding="utf-8"
    )
    titles = {c.title: c.text for c in markdown_chunks(doc, "d")}
    assert titles == {"A": "intro", "A > B": "body", "A > B > C": "deep", "A > D": "end"}


def test_a_heading_inside_a_code_block_is_not_a_heading(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text("# A\n\n```\n# not a heading\n```\n", encoding="utf-8")
    assert [c.title for c in markdown_chunks(doc, "d")] == ["A"]


def test_long_sections_are_split_within_the_limit(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    table = "\n".join(f"| row {i} | {'x' * 60} |" for i in range(40))
    doc.write_text(f"# A\n\n{table}\n", encoding="utf-8")
    chunks = markdown_chunks(doc, "d", limit=300)
    assert len(chunks) > 3 and all(len(c.text) <= 300 for c in chunks)
    assert len({c.id for c in chunks}) == len(chunks)


def test_rules_become_chunks(tmp_path: Path) -> None:
    rules = tmp_path / "r.csv"
    rules.write_text(
        "rule_id,table_name,description,severity,action\nR1,orders,Bad thing,error,drop\n",
        encoding="utf-8",
    )
    chunk = rule_chunks(rules)[0]
    assert chunk.id == "dq_rules#R1" and "Severity: error" in chunk.text


def test_the_real_corpus_has_unique_ids() -> None:
    chunks = build_corpus(REPO_ROOT)
    assert len(chunks) > 100 and len({c.id for c in chunks}) == len(chunks)


def test_the_golden_rag_set_points_at_real_text() -> None:
    questions = rag_eval.load_golden(REPO_ROOT / "eval" / "rag_golden.yml")
    assert len(questions) >= 50
    assert {q.kind for q in questions} == {"exact", "paraphrase"}
    assert rag_eval.check_golden(questions, build_corpus(REPO_ROOT)) == []


def test_fusion_rewards_being_found_by_both() -> None:
    assert reciprocal_rank_fusion([["a", "b"], ["b", "c"]])[0] == "b"


def test_fusion_keeps_everything_and_is_stable() -> None:
    assert sorted(reciprocal_rank_fusion([["a"], ["b"]])) == ["a", "b"]
    assert reciprocal_rank_fusion([["a"], ["b"]]) == reciprocal_rank_fusion([["b"], ["a"]])


def test_recall_and_mrr_on_known_rankings() -> None:
    assert rag_eval.recall_at(["x", "y", "a"], {"a"}, 2) == 0.0
    assert rag_eval.recall_at(["x", "y", "a"], {"a"}, 3) == 1.0
    assert rag_eval.reciprocal_rank(["x", "a"], {"a"}) == 0.5
    assert rag_eval.reciprocal_rank(["x"], {"a"}) == 0.0


class HashEmbedder:
    """Stable bag-of-words vectors: shared words give close vectors, with no model download."""

    @staticmethod
    def _vector(text: str) -> list[float]:
        vector = np.zeros(64)
        for word in text.lower().split():
            digest = hashlib.sha256(word.strip(".,?").encode()).digest()
            vector[digest[0] % 64] += 1.0
        return (vector / (np.linalg.norm(vector) or 1.0)).tolist()

    def passages(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def query(self, text: str) -> list[float]:
        return self._vector(text)


class LengthReranker:
    """Prefers shorter texts, which is visibly different from what retrieval returns."""

    def scores(self, query: str, texts: list[str]) -> list[float]:
        return [-float(len(text)) for text in texts]


CHUNKS = [
    Chunk("a", "d", "Payments", "Cash on delivery is the oldest way to pay for an order"),
    Chunk("b", "d", "Rules", "ORD_AMOUNT_MISMATCH compares the sales amount with its parts"),
    Chunk("c", "d", "Graph", "Louvain groups restaurants that share a menu"),
    Chunk("d", "d", "Forecast", "ETS beat the baseline on daily orders"),
]


@pytest.fixture
def retriever() -> Retriever:
    return Retriever(CHUNKS, HashEmbedder(), LengthReranker())


def test_bm25_finds_an_exact_identifier(retriever: Retriever) -> None:
    assert retriever.search("ORD_AMOUNT_MISMATCH", "bm25")[0] == "b"


def test_bm25_returns_nothing_when_no_word_is_shared(retriever: Retriever) -> None:
    assert retriever.bm25("zzz qqq") == []


def test_dense_search_returns_ids(retriever: Retriever) -> None:
    assert set(retriever.search("daily orders baseline", "dense", 2)) <= {"a", "b", "c", "d"}


def test_hybrid_contains_both_kinds_of_result(retriever: Retriever) -> None:
    assert "b" in retriever.search("ORD_AMOUNT_MISMATCH", "hybrid", 4)


def test_reranking_changes_the_order(retriever: Retriever) -> None:
    ranked = retriever.search("order menu sales", "hybrid_rerank", 4)
    lengths = [len(retriever.chunks[c].searchable) for c in ranked]
    assert lengths == sorted(lengths)


def test_an_unknown_mode_is_refused(retriever: Retriever) -> None:
    with pytest.raises(ValueError, match="unknown search mode"):
        retriever.search("x", "magic")


def test_retrieval_report_scores_each_mode(retriever: Retriever) -> None:
    questions = [
        rag_eval.GoldQuestion("q1", "exact", "ORD_AMOUNT_MISMATCH", "sales amount"),
        rag_eval.GoldQuestion("q2", "paraphrase", "Which model won on orders?", "ETS beat"),
    ]
    report = rag_eval.evaluate_retrieval(retriever, questions)
    assert set(report["modes"]) == {"dense", "bm25", "hybrid", "hybrid_rerank"}
    assert report["modes"]["hybrid"]["recall_at_5"] == 1.0
    assert "recall_at_5_exact" in report["modes"]["dense"]


def good_answer(_: str) -> str:
    return '{"found": true, "answer": "ETS won.", "citations": [1]}'


def test_an_answer_keeps_its_citation(retriever: Retriever, tmp_path: Path) -> None:
    client = client_with(tmp_path, good_answer)
    answer = rag_answer.answer_question(client, retriever, "daily orders baseline")
    assert answer.found and len(answer.cited) == 1 and answer.cited[0] in retriever.chunks


def test_a_made_up_citation_is_rejected(retriever: Retriever, tmp_path: Path) -> None:
    client = client_with(tmp_path, ['{"found": true, "answer": "x", "citations": [99]}'])
    with pytest.raises(InvalidReply, match="aren't among"):
        rag_answer.answer_question(client, retriever, "orders")


def test_an_answer_with_no_citation_is_rejected(retriever: Retriever, tmp_path: Path) -> None:
    client = client_with(tmp_path, ['{"found": true, "answer": "x", "citations": []}'])
    with pytest.raises(InvalidReply, match="must cite"):
        rag_answer.answer_question(client, retriever, "orders")


def test_saying_not_found_needs_no_citation(retriever: Retriever, tmp_path: Path) -> None:
    client = client_with(tmp_path, ['{"found": false, "answer": "Not in the documents."}'])
    assert not rag_answer.answer_question(client, retriever, "share price?").found


def test_answer_evaluation_counts_what_matters(retriever: Retriever, tmp_path: Path) -> None:
    def reply(prompt: str) -> str:
        if prompt.startswith("Answer:"):
            return '{"supported": false, "unsupported_claims": ["ETS won"]}'
        if "share price" in prompt:
            return '{"found": false, "answer": "Not covered."}'
        return good_answer(prompt)

    questions = [rag_eval.GoldQuestion("q", "exact", "daily orders baseline", "ETS beat")]
    report = rag_answer.evaluate_answers(
        client_with(tmp_path, reply), retriever, questions, ["What is the share price?"]
    )
    assert report["answered"] == 1 and report["faithful_rate"] == 0.0
    assert report["declined_unanswerable"] == 1
    assert report["results"][0]["unsupported_claims"] == ["ETS won"]


@pytest.mark.parametrize(
    ("rating", "sentiment"),
    [(1.0, "negative"), (2.0, "negative"), (3.0, "neutral"), (4.5, "positive")],
)
def test_rating_gives_the_expected_sentiment(rating: float, sentiment: str) -> None:
    assert enrich_reviews.rating_sentiment(rating) == sentiment


def labels_for(prompt: str) -> str:
    items = []
    for line in prompt.splitlines():
        number, text = line.split(". ", 1)
        if "Your last" in line or not number.isdigit():
            continue
        sentiment = {"Tasty": "positive", "Cold food": "negative"}.get(text, "neutral")
        items.append(
            {
                "number": int(number),
                "sentiment": sentiment,
                "topic": "food",
                "aspects": ["food"],
                "key_issue": None,
            }
        )
    return json.dumps({"items": items})


def test_each_distinct_text_is_labelled_once(warehouse: Path, tmp_path: Path) -> None:
    client = client_with(tmp_path, labels_for)
    report = enrich_reviews.enrich(client, warehouse)
    assert report["reviews"] == 4 and report["distinct_texts"] == 3
    assert report["agreement_with_rating_reviews"] == 1.0
    assert len(client._api.responses.calls) == 1
    con = duckdb.connect(str(warehouse), read_only=True)
    assert con.execute("select count(*) from ai.review_labels").fetchone() == (3,)
    con.close()


def test_disagreement_with_the_rating_is_reported(warehouse: Path, tmp_path: Path) -> None:
    def all_positive(prompt: str) -> str:
        return labels_for(prompt).replace("negative", "positive").replace("neutral", "positive")

    report = enrich_reviews.enrich(client_with(tmp_path, all_positive), warehouse)
    assert report["agreement_with_rating_reviews"] == 0.5
    assert {row["comment"] for row in report["disagreements"]} == {"Cold food", "It was okay"}


def test_a_batch_that_skips_a_review_is_rejected(tmp_path: Path) -> None:
    reply = (
        '{"items": [{"number": 1, "sentiment": "neutral", "topic": "food", "aspects": ["food"]}]}'
    )
    with pytest.raises(InvalidReply, match="no valid answer"):
        enrich_reviews.label_batch(client_with(tmp_path, [reply]), ["one", "two"])


def test_an_invented_label_is_rejected(tmp_path: Path) -> None:
    reply = '{"items": [{"number": 1, "sentiment": "angry", "topic": "food", "aspects": ["food"]}]}'
    with pytest.raises(InvalidReply):
        enrich_reviews.label_batch(client_with(tmp_path, [reply]), ["one"])
