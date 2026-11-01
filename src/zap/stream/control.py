"""What the dashboard may ask the feed to do: start, stop, or suffer a SYNTHETIC outage."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from zap.stream.simulator import TIME_FORMAT
from zap.stream.store import Store


def start_feed(dataset: str, log_path: Path) -> None:
    """Start the feed in the background. It keeps running after the page closes."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log:
        subprocess.Popen(  # noqa: S603 - our own interpreter and module, no user input
            [sys.executable, "-m", "zap.cli", "stream", "run", "--dataset", dataset],
            stdout=log,
            stderr=log,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )


def stop_feed(path: Path) -> None:
    store = Store(path)
    store.set_status(stop="1")
    store.close()


def inject_outage(path: Path, city: str, clock: datetime, minutes: int, drop_share: float) -> None:
    """From now on, that share of the city's orders never reach the feed."""
    store = Store(path)
    store.add_incident(
        city,
        clock.strftime(TIME_FORMAT),
        (clock + timedelta(minutes=minutes)).strftime(TIME_FORMAT),
        drop_share,
    )
    store.close()
