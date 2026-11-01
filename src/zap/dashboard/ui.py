"""Streamlit helpers shared by every page: dataset picker, theme mode, caching, table view."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from zap.config import REPO_ROOT, get_settings
from zap.dashboard import data
from zap.dashboard.components import feed_badge
from zap.dashboard.theme import Mode, page_css
from zap.metrics.collect import Metric, collect
from zap.stream.read import feed_state
from zap.stream.store import stream_path

DATASET_NOTE = {
    "full": "Full dataset (10M SYNTHETIC orders).",
    "fixture": "SYNTHETIC test fixture, not the real dataset.",
}


def theme_mode() -> Mode:
    theme = getattr(st.context, "theme", None)
    return "dark" if getattr(theme, "type", None) == "dark" else "light"


def _show_feed_state() -> None:
    state = feed_state(stream_path(get_settings().warehouse_dir))
    if state.running:
        feed_badge(True, "Live feed running", "SIMULATED orders")
    else:
        feed_badge(False, "Live feed stopped", "Start it on Live Ops")


def page_setup(title: str, subtitle: str = "") -> Path | None:
    """Page config + sidebar dataset picker. Returns the warehouse file, or None if none built."""
    st.set_page_config(page_title=f"{title} | Zomato AI Data Platform", layout="wide")
    st.html(page_css(theme_mode()))
    st.title(title)
    if subtitle:
        st.caption(subtitle)
    settings = get_settings()
    datasets = data.available_datasets(settings.warehouse_dir)
    if not datasets:
        st.warning("No warehouse found. Run `make local` (or `make demo`) first.")
        return None
    # Each page has its own copy of this widget, so the choice is kept outside it.
    saved = st.session_state.get("chosen_dataset")
    index = datasets.index(saved) if saved in datasets else 0
    chosen = st.sidebar.selectbox("Dataset", datasets, index=index, key="dataset")
    st.session_state["chosen_dataset"] = chosen
    note = DATASET_NOTE.get(chosen, "Deterministic user-level SAMPLE of the synthetic orders.")
    st.sidebar.caption(note)
    _show_feed_state()
    return settings.duckdb_path(chosen)


@st.cache_data(ttl=600, show_spinner=False)
def _cached(name: str, db: str, mtime: float, args: tuple[Any, ...]) -> Any:
    fn: Callable[..., Any] = getattr(data, name)
    return fn(Path(db), *args)


def load(name: str, db: Path, *args: Any) -> Any:
    """Call `zap.dashboard.data.<name>(db, *args)`, cached until the warehouse file changes."""
    return _cached(name, str(db), db.stat().st_mtime, args)


def table_view(frame: pd.DataFrame, label: str = "Show the data as a table") -> None:
    with st.expander(label):
        st.dataframe(frame, hide_index=True, width="stretch")


def measured() -> list[Metric]:
    """Everything in docs/METRICS.md, read from the files real runs left."""
    settings = get_settings()
    return collect(
        settings.artifacts_dir,
        REPO_ROOT / "eval" / "results",
        settings.llm_replay_dir / "spend.jsonl",
    )
