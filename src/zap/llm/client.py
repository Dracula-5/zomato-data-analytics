"""The one door to the LLM, built so that it can't overspend or spend by accident.

* Replay is the default: answers come from the files in `eval/replay/`, so tests and CI never
  reach the API and need no key. A run must set `LLM_MODE=live` to spend anything.
* Every live answer is saved, so a repeat costs nothing and an evaluation can be re-scored.
* The budget is checked before each call against the worst case for that call, using a ledger
  kept in the repo, so it holds across runs and not just within one.
* A model with no known price is refused, because then the budget means nothing.

`LLM_BUDGET_USD` guards what this project calls; the provider's own limits are a separate thing.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from zap.config import Settings

Schema = TypeVar("Schema", bound=BaseModel)

# USD per 1M tokens (input, output), standard tier, from OpenAI's pricing page on 2026-10-31.
PRICES: dict[str, tuple[float, float]] = {"gpt-5.6-luna": (0.20, 1.20)}
CHARS_PER_TOKEN = 3  # deliberately low, so the worst-case estimate runs high
LEDGER = "spend.jsonl"


class LlmError(RuntimeError):
    pass


class ReplayMiss(LlmError):
    """Replay mode has no saved answer for this exact prompt."""


class BudgetExceeded(LlmError):
    pass


class InvalidReply(LlmError):
    """The model's answer didn't match the schema, even after retrying."""


@dataclass(frozen=True)
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    cached: bool


def call_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if model not in PRICES:
        raise LlmError(f"no price for {model}, so the budget can't be enforced")
    price_in, price_out = PRICES[model]
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


def prompt_key(model: str, system: str, user: str, schema: str) -> str:
    return hashlib.sha256("\x00".join((model, system, user, schema)).encode()).hexdigest()[:24]


class LlmClient:
    def __init__(self, settings: Settings, api: Any = None) -> None:
        self.settings = settings
        self.folder = settings.llm_replay_dir
        self._api = api
        self._saved: dict[str, dict[str, Any]] | None = None

    def _replies(self) -> dict[str, dict[str, Any]]:
        if self._saved is None:
            self._saved = {}
            for path in sorted(self.folder.glob("*.jsonl")):
                if path.name == LEDGER:
                    continue
                for line in path.read_text(encoding="utf-8").splitlines():
                    entry = json.loads(line)
                    self._saved[entry["prompt_hash"]] = entry
        return self._saved

    def spent_usd(self) -> float:
        ledger = self.folder / LEDGER
        if not ledger.exists():
            return 0.0
        lines = ledger.read_text(encoding="utf-8").splitlines()
        return sum(float(json.loads(line)["cost_usd"]) for line in lines)

    def _append(self, name: str, entry: dict[str, Any]) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        with (self.folder / name).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _check_budget(self, system: str, user: str, max_output: int) -> None:
        likely_input = (len(system) + len(user)) // CHARS_PER_TOKEN
        worst = call_cost(self.settings.llm_model, likely_input, max_output)
        spent = self.spent_usd()
        if spent + worst > self.settings.llm_budget_usd:
            raise BudgetExceeded(
                f"spent ${spent:.4f} of ${self.settings.llm_budget_usd:.2f}; "
                f"this call could cost ${worst:.4f}"
            )

    def _api_client(self) -> Any:
        if self._api is None:
            from openai import OpenAI

            key = self.settings.openai_api_key
            if key is None or not key.get_secret_value().strip():
                raise LlmError("OPENAI_API_KEY is not set (see .env.example)")
            self._api = OpenAI(api_key=key.get_secret_value().strip(), max_retries=3, timeout=60)
        return self._api

    def _call_api(
        self, system: str, user: str, schema: type[BaseModel] | None, max_output: int
    ) -> tuple[str, int, int]:
        request: dict[str, Any] = {
            "model": self.settings.llm_model,
            "instructions": system,
            "input": user,
            "max_output_tokens": max_output,
            "reasoning": {"effort": self.settings.llm_reasoning_effort},
        }
        api = self._api_client()
        if schema is None:
            response = api.responses.create(**request)
        else:
            response = api.responses.parse(**request, text_format=schema)
        if response.status != "completed":
            detail = getattr(response.incomplete_details, "reason", None)
            raise InvalidReply(f"the reply stopped early: {response.status} ({detail})")
        return response.output_text, response.usage.input_tokens, response.usage.output_tokens

    def complete(
        self,
        system: str,
        user: str,
        purpose: str,
        schema: type[BaseModel] | None = None,
        max_output: int | None = None,
    ) -> Reply:
        """Answer from the saved replies if possible, otherwise pay for a live call."""
        model = self.settings.llm_model
        limit = max_output or self.settings.llm_max_output_tokens
        shape = json.dumps(schema.model_json_schema(), sort_keys=True) if schema else ""
        key = prompt_key(model, system, user, shape)
        saved = self._replies().get(key)
        if saved is not None:
            return Reply(saved["text"], saved["input_tokens"], saved["output_tokens"], cached=True)
        if self.settings.llm_mode != "live":
            raise ReplayMiss(f"no saved answer for this {purpose} prompt, and LLM_MODE isn't live")

        self._check_budget(system, user, limit)
        started = time.perf_counter()
        text, input_tokens, output_tokens = self._call_api(system, user, schema, limit)
        cost = call_cost(model, input_tokens, output_tokens)
        entry = {
            "prompt_hash": key,
            "model": model,
            "text": text,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        self._append(f"{purpose}.jsonl", entry)
        self._replies()[key] = entry
        self._append(
            LEDGER,
            {
                "at": datetime.now(UTC).isoformat(),
                "purpose": purpose,
                "model": model,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": round(cost, 8),
                "seconds": round(time.perf_counter() - started, 3),
            },
        )
        return Reply(text, input_tokens, output_tokens, cached=False)

    def complete_as(
        self,
        system: str,
        user: str,
        purpose: str,
        schema: type[Schema],
        check: Callable[[Schema], None] | None = None,
        attempts: int = 2,
        max_output: int | None = None,
    ) -> Schema:
        """Return a validated object. A bad answer is sent back once, with the reason."""
        prompt = user
        problem = ""
        for _ in range(attempts):
            reply = self.complete(system, prompt, purpose, schema, max_output)
            try:
                parsed = schema.model_validate_json(reply.text)
                if check is not None:
                    check(parsed)
                return parsed
            except (ValidationError, ValueError) as err:
                problem = str(err)[:500]
                prompt = f"{user}\n\nYour last answer was rejected: {problem}\nAnswer again."
        raise InvalidReply(f"{purpose}: no valid answer in {attempts} attempts: {problem}")


def usage_summary(folder: Path) -> dict[str, Any]:
    """Totals from the ledger, for METRICS.md."""
    ledger = folder / LEDGER
    lines = ledger.read_text(encoding="utf-8").splitlines() if ledger.exists() else []
    summary: dict[str, Any] = {}
    for line in lines:
        entry = json.loads(line)
        row = summary.setdefault(
            entry["purpose"], {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        )
        row["calls"] += 1
        row["input_tokens"] += entry["input_tokens"]
        row["output_tokens"] += entry["output_tokens"]
        row["cost_usd"] = round(row["cost_usd"] + entry["cost_usd"], 8)
    return summary
