"""Streamlit helpers shared by every page: dataset picker, theme mode, caching, table view."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

from zap.config import get_settings
from zap.dashboard import data
from zap.dashboard.theme import Mode

DATASET_NOTE = {
    "full": "Full dataset (10M SYNTHETIC orders).",
    "fixture": "SYNTHETIC test fixture, not the real dataset.",
}


def theme_mode() -> Mode:
    theme = getattr(st.context, "theme", None)
    return "dark" if getattr(theme, "type", None) == "dark" else "light"


def page_setup(title: str) -> Path | None:
    """Page config + sidebar dataset picker. Returns the warehouse file, or None if none built."""
    st.set_page_config(page_title=f"{title} | Zomato AI Data Platform", layout="wide")
    st.title(title)
    settings = get_settings()
    datasets = data.available_datasets(settings.warehouse_dir)
    if not datasets:
        st.warning("No warehouse found. Run `make local` (or `make demo`) first.")
        return None
    chosen = st.sidebar.selectbox("Dataset", datasets, key="dataset")
    note = DATASET_NOTE.get(chosen, "Deterministic user-level SAMPLE of the synthetic orders.")
    st.sidebar.caption(note)
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
        st.dataframe(frame, hide_index=True, use_container_width=True)
