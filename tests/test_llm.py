"""The LLM client's guarantees, with a fake API: nothing here can spend money."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import BaseModel, SecretStr

from zap.config import Settings
from zap.llm.client import (
    LEDGER,
    BudgetExceeded,
    InvalidReply,
    LlmClient,
    LlmError,
    ReplayMiss,
    call_cost,
    prompt_key,
    usage_summary,
)


class Verdict(BaseModel):
    label: str


class FakeResponses:
    def __init__(self, texts: list[str], status: str = "completed") -> None:
        self.texts = texts
        self.status = status
        self.calls: list[dict[str, Any]] = []

    def _answer(self, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(kwargs)
        return SimpleNamespace(
            status=self.status,
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output_text=self.texts[min(len(self.calls) - 1, len(self.texts) - 1)],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
        )

    create = _answer
    parse = _answer


def fake_api(*texts: str, status: str = "completed") -> SimpleNamespace:
    return SimpleNamespace(responses=FakeResponses(list(texts), status))


def live(tmp_path: Path, budget: float = 2.0) -> Settings:
    return Settings(llm_mode="live", llm_replay_dir=tmp_path, llm_budget_usd=budget)


def test_replay_is_the_default() -> None:
    assert Settings().llm_mode == "replay"


def test_replay_never_reaches_the_api(tmp_path: Path) -> None:
    api = fake_api("paid answer")
    client = LlmClient(Settings(llm_replay_dir=tmp_path), api=api)
    with pytest.raises(ReplayMiss, match="LLM_MODE"):
        client.complete("s", "u", "test")
    assert api.responses.calls == []


def test_replay_needs_no_key(tmp_path: Path) -> None:
    client = LlmClient(Settings(llm_replay_dir=tmp_path, openai_api_key=None))
    with pytest.raises(ReplayMiss):
        client.complete("s", "u", "test")


def test_live_answer_is_saved_and_never_paid_for_twice(tmp_path: Path) -> None:
    api = fake_api("answer")
    client = LlmClient(live(tmp_path), api=api)
    first = client.complete("s", "u", "test")
    second = client.complete("s", "u", "test")
    assert (first.cached, second.cached) == (False, True)
    assert second.text == "answer" and len(api.responses.calls) == 1


def test_a_saved_answer_replays_with_no_api_at_all(tmp_path: Path) -> None:
    LlmClient(live(tmp_path), api=fake_api("answer")).complete("s", "u", "test")
    replayed = LlmClient(Settings(llm_replay_dir=tmp_path)).complete("s", "u", "test")
    assert replayed.text == "answer" and replayed.cached


def test_any_change_to_the_prompt_is_a_new_question() -> None:
    base = prompt_key("m", "s", "u", "")
    assert base != prompt_key("m2", "s", "u", "")
    assert base != prompt_key("m", "s2", "u", "")
    assert base != prompt_key("m", "s", "u2", "")
    assert base != prompt_key("m", "s", "u", "{}")


def test_cost_uses_the_price_table() -> None:
    assert call_cost("gpt-5.6-luna", 1_000_000, 1_000_000) == pytest.approx(1.40)


def test_an_unpriced_model_is_refused(tmp_path: Path) -> None:
    settings = Settings(llm_mode="live", llm_replay_dir=tmp_path, llm_model="gpt-unknown")
    api = fake_api("x")
    with pytest.raises(LlmError, match="no price"):
        LlmClient(settings, api=api).complete("s", "u", "test")
    assert api.responses.calls == []


def test_a_call_that_could_cross_the_cap_is_refused_first(tmp_path: Path) -> None:
    api = fake_api("x")
    client = LlmClient(live(tmp_path, budget=0.0001), api=api)
    with pytest.raises(BudgetExceeded, match="could cost"):
        client.complete("s", "u", "test", max_output=1000)
    assert api.responses.calls == []


def test_the_cap_counts_earlier_runs(tmp_path: Path) -> None:
    (tmp_path / LEDGER).write_text(json.dumps({"cost_usd": 1.9999}) + "\n", encoding="utf-8")
    api = fake_api("x")
    with pytest.raises(BudgetExceeded, match=r"spent \$1\.9999"):
        LlmClient(live(tmp_path), api=api).complete("s", "u", "test", max_output=1000)
    assert api.responses.calls == []


def test_every_paid_call_is_in_the_ledger(tmp_path: Path) -> None:
    client = LlmClient(live(tmp_path), api=fake_api("a", "b"))
    client.complete("s", "first", "alpha")
    client.complete("s", "second", "beta")
    client.complete("s", "first", "alpha")  # cached, so not billed again
    assert client.spent_usd() == pytest.approx(2 * call_cost("gpt-5.6-luna", 1000, 500))
    summary = usage_summary(tmp_path)
    assert set(summary) == {"alpha", "beta"} and summary["alpha"]["calls"] == 1


def test_no_ledger_means_nothing_spent(tmp_path: Path) -> None:
    assert LlmClient(Settings(llm_replay_dir=tmp_path)).spent_usd() == 0.0
    assert usage_summary(tmp_path) == {}


def test_a_live_call_needs_a_key(tmp_path: Path) -> None:
    settings = Settings(llm_mode="live", llm_replay_dir=tmp_path, openai_api_key=SecretStr(" "))
    with pytest.raises(LlmError, match="OPENAI_API_KEY"):
        LlmClient(settings).complete("s", "u", "test")


def test_a_truncated_reply_is_an_error_not_an_answer(tmp_path: Path) -> None:
    client = LlmClient(live(tmp_path), api=fake_api("half an ans", status="incomplete"))
    with pytest.raises(InvalidReply, match="stopped early"):
        client.complete("s", "u", "test")


def test_valid_output_is_returned_as_the_schema(tmp_path: Path) -> None:
    client = LlmClient(live(tmp_path), api=fake_api('{"label": "good"}'))
    assert client.complete_as("s", "u", "test", Verdict).label == "good"


def test_an_invalid_answer_is_sent_back_with_the_reason(tmp_path: Path) -> None:
    api = fake_api('{"wrong": 1}', '{"label": "fixed"}')
    client = LlmClient(live(tmp_path), api=api)
    assert client.complete_as("s", "u", "test", Verdict).label == "fixed"
    assert "rejected" in api.responses.calls[1]["input"]


def test_a_custom_check_can_reject_an_answer(tmp_path: Path) -> None:
    def must_be_good(verdict: Verdict) -> None:
        if verdict.label != "good":
            raise ValueError("label must be good")

    api = fake_api('{"label": "bad"}', '{"label": "good"}')
    client = LlmClient(live(tmp_path), api=api)
    assert client.complete_as("s", "u", "test", Verdict, check=must_be_good).label == "good"


def test_it_gives_up_after_the_last_attempt(tmp_path: Path) -> None:
    client = LlmClient(live(tmp_path), api=fake_api('{"wrong": 1}'))
    with pytest.raises(InvalidReply, match="no valid answer"):
        client.complete_as("s", "u", "test", Verdict)
