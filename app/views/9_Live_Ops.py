"""Live Ops: past orders replayed as a feed, with alerts. All of it is SIMULATED.

No real-time order feed is publicly available, and this dataset ends in June 2026. So the feed
replays recent orders as events. The engineering is real, and the data is plainly labelled.
"""

import time
from pathlib import Path

import streamlit as st

from zap.config import get_settings
from zap.dashboard.charts import city_map, live_window
from zap.dashboard.components import hero, note
from zap.dashboard.theme import Mode
from zap.dashboard.ui import page_setup, theme_mode
from zap.stream import read
from zap.stream.control import inject_outage, start_feed, stop_feed
from zap.stream.processor import WINDOW_MINUTES
from zap.stream.store import ALL_CITIES, stream_path

REFRESH = "2s"
OUTAGE_MINUTES = 60
OUTAGE_SHARE = 0.8
START_WAIT_SECONDS = 25


def change_against(now: float, before: float) -> str | None:
    return f"{now / before - 1:+.1%}" if before else None


def alert_line(row) -> tuple[str, str, str]:  # type: ignore[no-untyped-def]
    word = "fell" if row.kind == "drop" else "jumped"
    text = f"{row.city} {word}: {row.observed:,.0f} orders, against {row.expected:,.0f} expected"
    if row.resolved_at:
        return text, f"{row.raised_at[11:]} to {row.resolved_at[11:]}, back to normal", "good"
    return text, f"Raised at {row.raised_at[11:]}, still open", "bad"


@st.fragment(run_every=REFRESH)
def live_view(path: Path, mode: Mode) -> None:
    state = read.feed_state(path)
    if state.clock is None:
        st.info("The feed has no data yet.")
        return
    now = read.hour_summary(path, state.clock)
    before = read.hour_summary(path, state.clock, hours_back=1)
    alerts = read.recent_alerts(path)
    still_open = int(alerts["resolved_at"].isna().sum()) if len(alerts) else 0

    notes = read.recent_notes(path, 3)
    hero(
        "Simulated live feed" if state.running else "Simulated feed, stopped",
        f"{state.clock:%A %d %b, %H:%M}",
        "Past orders replayed as events. No real orders are arriving.",
        ["SIMULATED", f"{state.speed // 60} simulated minute a second"],
        "Latest hourly analysis",
        notes["text"].iloc[0] if len(notes) else "The first note comes at the end of the hour.",
    )

    orders = now["orders"] or 1
    tiles = st.columns(5)
    tiles[0].metric(
        "Orders, last hour",
        f"{now['orders']:,.0f}",
        change_against(now["orders"], before["orders"]),
        delta_color="off",  # the hour before isn't the fair comparison: normal is the chart's job
        delta_description="vs last hour",
    )
    tiles[1].metric(
        "GMV, last hour",
        f"₹{now['gmv'] / 1e5:,.2f} L",
        change_against(now["gmv"], before["gmv"]),
        delta_color="off",
        delta_description="vs last hour",
    )
    tiles[2].metric("Cancelled", f"{now['cancelled'] / orders:.1%}")
    tiles[3].metric("Delivered late", f"{now['late'] / orders:.1%}")
    tiles[4].metric("Open alerts", still_open)

    main, side = st.columns([2.2, 1])
    with main:
        cities = read.watched_cities(path)
        if not cities:
            st.info(f"Waiting for the first {WINDOW_MINUTES}-minute window to fill.")
        else:
            city = st.selectbox("City on the chart", cities, key="live_city")
            history = read.window_history(path, city)
            title = f"Orders in the last {WINDOW_MINUTES} minutes, {city}"
            st.plotly_chart(live_window(history, title, mode), width="stretch")
    with side:
        st.subheader("Alerts")
        if alerts.empty:
            st.caption("Nothing unusual so far.")
        for row in alerts.itertuples():
            text, small, tone = alert_line(row)
            note(text, small, tone)

    left, right = st.columns(2)
    with left:
        activity = read.city_activity(path, state.clock)
        if len(activity):
            figure = city_map(activity, "orders", "Orders in the last hour", mode, height=440)
            st.plotly_chart(figure, width="stretch")
    with right:
        st.subheader("Latest orders")
        st.dataframe(
            read.recent_orders(path),
            hide_index=True,
            width="stretch",
            column_config={
                "Time": st.column_config.TextColumn(width="small"),
                "City": st.column_config.TextColumn(width="small"),
                "Status": st.column_config.TextColumn(width="small"),
                "Amount": st.column_config.NumberColumn("₹", format="%.0f", width="small"),
            },
        )


db = page_setup("Live Ops", "What's happening now, and whether anything looks wrong.")
if db is not None:
    settings = get_settings()
    path = stream_path(settings.warehouse_dir)
    state = read.feed_state(path)
    if not state.running:
        st.info(
            "No public real-time order feed exists, so this replays the last 14 days of orders "
            "as a stream. It's free and writes only to its own file."
        )
        if st.button("Start the live feed", type="primary"):
            start_feed(db.stem, settings.artifacts_dir / "stream.log")
            with st.spinner("Loading orders and the baseline..."):
                for _ in range(START_WAIT_SECONDS):
                    if read.feed_state(path).running:
                        break
                    time.sleep(1)
            st.rerun()
    else:
        city_box, inject, stop = st.columns([2, 1.3, 0.9], vertical_alignment="bottom")
        targets = [city for city in read.watched_cities(path) if city != ALL_CITIES]
        target = city_box.selectbox("Test the alerts in", targets or [None])
        if inject.button(
            "Inject a SYNTHETIC outage",
            disabled=not targets,
            width="stretch",
            help=f"{OUTAGE_SHARE:.0%} of this city's orders vanish for {OUTAGE_MINUTES} "
            "simulated minutes. Nothing real is affected.",
        ):
            clock = read.feed_state(path).clock
            if clock is not None and target is not None:
                inject_outage(path, target, clock, OUTAGE_MINUTES, OUTAGE_SHARE)
                st.toast(f"SYNTHETIC outage started in {target}. Watch for the alert.")
        if stop.button("Stop the feed", width="stretch"):
            stop_feed(path)
            time.sleep(2)
            st.rerun()
    if path.exists():
        live_view(path, theme_mode())
