"""One JSON line for each step, so any answer can be traced back to how it was reached."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

Node = Callable[[Any], dict[str, Any]]


class Tracer:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, run_id: str, node: str, seconds: float, output: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "at": datetime.now(UTC).isoformat(),
            "run": run_id,
            "node": node,
            "seconds": round(seconds, 4),
            "output": output,
        }
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")

    def wrap(self, name: str, node: Node) -> Node:
        @wraps(node)
        def traced(state: Any) -> dict[str, Any]:
            started = time.perf_counter()
            output = node(state)
            # Rows can be large and are saved in the checkpoint, so the trace keeps their count.
            summary = {k: f"{len(v)} rows" if k == "rows" else v for k, v in output.items()}
            self.record(state.get("run_id", ""), name, time.perf_counter() - started, summary)
            return output

        return traced


def read_trace(path: Path, run_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    entries = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return [entry for entry in entries if entry["run"] == run_id]
