"""Small pieces of HTML the pages share. Everything shown is escaped first."""

from __future__ import annotations

from html import escape

import streamlit as st


def _pills(labels: list[str]) -> str:
    body = "".join(f'<span class="zap-pill">{escape(label)}</span>' for label in labels)
    return f'<div class="zap-pills">{body}</div>'


def hero(
    eyebrow: str,
    headline: str,
    detail: str = "",
    pills: list[str] | None = None,
    aside_title: str = "",
    aside: str = "",
) -> None:
    """The dark banner at the top of a page."""
    side = ""
    if aside:
        side = (
            f'<div class="zap-hero-aside"><div class="zap-eyebrow">{escape(aside_title)}</div>'
            f"{escape(aside)}</div>"
        )
    st.html(
        f'<div class="zap-hero"><div class="zap-hero-main">'
        f'<div class="zap-eyebrow">{escape(eyebrow)}</div>'
        f'<div class="zap-headline">{escape(headline)}</div>'
        f'<div class="zap-detail">{escape(detail)}</div>'
        f"{_pills(pills or [])}</div>{side}</div>"
    )


def note(text: str, small: str = "", tone: str = "") -> None:
    """A short card of text. `tone` is "good" or "bad", which also says it in words nearby."""
    extra = f" zap-note-{tone}" if tone else ""
    under = f"<small>{escape(small)}</small>" if small else ""
    st.html(f'<div class="zap-note{extra}">{under}{escape(text)}</div>')


def stat(label: str, value: str) -> None:
    """One figure with its label, in a card."""
    st.html(
        f'<div class="zap-note"><small>{escape(label)}</small>'
        f'<div class="zap-stat">{escape(value)}</div></div>'
    )


def feed_badge(running: bool, line: str, under: str = "") -> None:
    """The feed's state in the sidebar, on every page."""
    dot = "zap-dot" if running else "zap-dot zap-dot-off"
    small = f"<small>{escape(under)}</small>" if under else ""
    st.sidebar.html(f'<div class="zap-feed"><span class="{dot}"></span>{escape(line)}{small}</div>')
