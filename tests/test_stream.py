"""The SIMULATED live feed: the log, the windows, the detector and what the dashboard reads."""

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest
from typer.testing import CliRunner

from zap.cli import app
from zap.config import Settings, get_settings
from zap.fixtures import FixtureResult
from zap.pipeline import PipelineRun
from zap.stream import detector, read
from zap.stream.control import inject_outage, stop_feed
from zap.stream.processor import (
    LATENESS_MINUTES,
    WINDOW_MINUTES,
    Processor,
    Rates,
    load_rates,
    load_watch,
)
from zap.stream.runner import evaluate, found, plan_incidents, run_live, stop_requested
from zap.stream.simulator import Simulator, load_orders, replay_days
from zap.stream.store import ALL_CITIES, Store, stream_path

Pipeline = tuple[Settings, PipelineRun, FixtureResult]
START = datetime(2026, 6, 1, 12, 0)  # a Monday
CITY = "Pune"


def event(stamp: str, city: str = CITY, status: str = "Delivered", late: int = 0) -> tuple:  # type: ignore[type-arg]
    return (stamp, city, "A kitchen", status, 100.0, late)


def steady_rates(per_minute: float = 2.0) -> Rates:
    table = {
        (weekday, hour, city): per_minute
        for weekday in range(1, 8)
        for hour in range(24)
        for city in (CITY, ALL_CITIES)
    }
    return Rates(table)


def orders_every_minute(minutes: int, per_minute: int = 2, city: str = CITY) -> pd.DataFrame:
    times = [
        START + timedelta(minutes=minute, seconds=5 * index)
        for minute in range(minutes)
        for index in range(per_minute)
    ]
    frame = pd.DataFrame(
        {
            "order_timestamp": times,
            "city": city,
            "restaurant_name": "A kitchen",
            "order_status": "Delivered",
            "sales_amount": 100.0,
            "is_sla_breach": False,
        }
    )
    frame["arrival"] = frame["order_timestamp"]
    frame["keep"] = 0.5
    return frame


def test_the_log_keeps_order_and_remembers_the_offset() -> None:
    store = Store(":memory:")
    store.append([event("2026-06-01 12:00:01"), event("2026-06-01 12:00:02")])
    first, second = store.read_after(0)
    assert first.position < second.position
    assert store.read_after(first.position) == [second]
    assert store.offset() == 0
    store.save_offset(second.position)
    assert store.offset() == second.position


def test_a_failed_transaction_leaves_nothing_behind() -> None:
    store = Store(":memory:")
    with pytest.raises(RuntimeError), store.transaction():
        store.save_offset(5)
        raise RuntimeError
    assert store.offset() == 0


def test_a_late_event_counts_in_the_minute_it_happened() -> None:
    store = Store(":memory:")
    processor = Processor(store, steady_rates(), [CITY], START)
    store.append([event("2026-06-01 12:05:10")])
    processor.consume()
    store.append([event("2026-06-01 12:01:30", late=1)])  # arrives after a later order
    processor.consume()
    assert store.window_orders("2026-06-01 12:01", "2026-06-01 12:01") == {CITY: 1, ALL_CITIES: 1}
    assert store.window_orders("2026-06-01 12:05", "2026-06-01 12:05")[CITY] == 1


def test_restarting_neither_loses_nor_double_counts() -> None:
    store = Store(":memory:")
    store.append([event("2026-06-01 12:00:01")])
    Processor(store, steady_rates(), [CITY], START).consume()
    store.append([event("2026-06-01 12:00:02")])
    restarted = Processor(store, steady_rates(), [CITY], START)
    assert restarted.consume() == 1  # only the new one
    assert store.window_orders("2026-06-01 12:00", "2026-06-01 12:00")[CITY] == 2


def test_delivered_orders_count_as_gmv_and_cancelled_ones_dont() -> None:
    store = Store(":memory:")
    store.append([event("2026-06-01 12:00:01"), event("2026-06-01 12:00:02", status="Cancelled")])
    Processor(store, steady_rates(), [CITY], START).consume()
    row = store.con.execute(
        "select orders, gmv, cancelled from minute_stats where city = ?", (CITY,)
    ).fetchone()
    assert row == (2, 100.0, 1)


def test_a_window_is_only_judged_once_late_events_have_had_time() -> None:
    store = Store(":memory:")
    processor = Processor(store, steady_rates(), [CITY], START)
    clock = START + timedelta(minutes=WINDOW_MINUTES + 1)
    processor.step(clock)
    judged = store.con.execute("select count(*) from window_scores").fetchone()
    assert judged == (0,)  # the watermark hasn't reached a full window yet
    processor.step(clock + timedelta(minutes=LATENESS_MINUTES))
    assert store.con.execute("select count(*) from window_scores").fetchone()[0] > 0


def test_the_chance_of_a_window_is_exact_at_low_counts() -> None:
    assert detector.tail_chance(0, 14) < detector.RAISE_CHANCE  # z is only -3.7 here
    assert detector.tail_chance(14, 14) > 0.1
    assert detector.tail_chance(60, 30) < detector.RAISE_CHANCE


def test_an_alert_opens_once_and_closes_when_things_recover() -> None:
    assert detector.next_state(False, 60, 60) is None
    assert detector.next_state(False, 0, 60) == detector.Change("raise", "drop")
    assert detector.next_state(False, 120, 60) == detector.Change("raise", "spike")
    assert detector.next_state(True, 0, 60) is None  # one incident, one alert
    assert detector.next_state(True, 59, 60) == detector.Change("resolve")


def test_a_tiny_city_cant_raise_an_alert() -> None:
    assert detector.next_state(False, 0, 2) is None


def test_the_normal_range_holds_the_expected_count() -> None:
    low, high = detector.normal_range(100)
    assert low < 100 < high


def test_an_outage_is_caught_and_a_recovery_clears_it() -> None:
    store = Store(":memory:")
    minutes = 150
    outage_start = START + timedelta(minutes=45)
    outage_end = outage_start + timedelta(minutes=45)
    store.add_incident(
        CITY,
        outage_start.strftime("%Y-%m-%d %H:%M:%S"),
        outage_end.strftime("%Y-%m-%d %H:%M:%S"),
        1.0,
    )
    simulator = Simulator(store, orders_every_minute(minutes))
    processor = Processor(store, steady_rates(), [CITY], START)
    for minute in range(1, minutes + 1):
        clock = START + timedelta(minutes=minute)
        simulator.advance(clock)
        processor.step(clock)
    alerts = store.con.execute("select city, kind, resolved_at from alerts").fetchall()
    assert {(city, kind) for city, kind, _ in alerts} == {(CITY, "drop"), (ALL_CITIES, "drop")}
    assert all(resolved for _, _, resolved in alerts)


def test_a_steady_feed_raises_nothing_and_writes_an_hourly_note() -> None:
    store = Store(":memory:")
    simulator = Simulator(store, orders_every_minute(80))
    processor = Processor(store, steady_rates(), [CITY], START)
    for minute in range(1, 81):
        clock = START + timedelta(minutes=minute)
        simulator.advance(clock)
        processor.step(clock)
    assert store.con.execute("select count(*) from alerts").fetchone() == (0,)
    (note,) = store.con.execute("select text from insights").fetchone()
    assert "orders" in note and CITY in note


def test_an_incident_only_removes_its_own_city() -> None:
    store = Store(":memory:")
    both = pd.concat([orders_every_minute(5), orders_every_minute(5, city="Delhi")])
    both = both.sort_values("arrival").reset_index(drop=True)
    store.add_incident(CITY, "2026-06-01 12:00:00", "2026-06-01 13:00:00", 1.0)
    sent = Simulator(store, both).advance(START + timedelta(minutes=10))
    assert sent == 10
    assert {event.city for event in store.read_after(0)} == {"Delhi"}


def test_skipping_ahead_sends_nothing_from_before_the_start() -> None:
    store = Store(":memory:")
    simulator = Simulator(store, orders_every_minute(10))
    simulator.skip_to(START + timedelta(minutes=5))
    assert simulator.advance(START + timedelta(minutes=5)) == 0
    assert simulator.advance(START + timedelta(minutes=10)) == 9


def test_the_live_loop_stops_when_asked_and_reports_its_clock() -> None:
    store = Store(":memory:")
    simulator = Simulator(store, orders_every_minute(30))
    processor = Processor(store, steady_rates(), [CITY], START)
    ticks = iter([False, False, True])
    clock = run_live(store, simulator, processor, START, 60, lambda: next(ticks), tick=0.01)
    assert clock == START + timedelta(seconds=1.2)
    assert store.status()["speed"] == "60"


def test_stopping_goes_through_the_status_table(tmp_path: Path) -> None:
    path = tmp_path / "s.sqlite"
    store = Store(path)
    assert not stop_requested(store)
    stop_feed(path)
    assert stop_requested(store)
    store.close()


def test_the_plan_of_outages_is_fixed_in_advance() -> None:
    first = plan_incidents(["Pune", "Delhi"], date(2026, 6, 1), 5, 0.7)
    assert first == plan_incidents(["Pune", "Delhi"], date(2026, 6, 1), 5, 0.7)
    assert len(first) == 5 and all(11 <= start.hour <= 21 for _, start, _ in first)


def test_found_only_accepts_the_right_city_kind_and_time() -> None:
    incident = ("Pune", datetime(2026, 6, 1, 12, 0), 0.7)
    assert found(incident, [("2026-06-01 12:20", "Pune", "drop")]) == 20
    assert found(incident, [("2026-06-01 12:20", "Delhi", "drop")]) is None
    assert found(incident, [("2026-06-01 12:20", "Pune", "spike")]) is None
    assert found(incident, [("2026-06-01 11:50", "Pune", "drop")]) is None


def test_the_dashboard_reads_what_the_feed_wrote(tmp_path: Path) -> None:
    path = tmp_path / "s.sqlite"
    assert read.feed_state(path) == read.FeedState(False, None, 0)
    store = Store(path)
    simulator = Simulator(store, orders_every_minute(80))
    processor = Processor(store, steady_rates(), [CITY], START)
    clock = START
    for minute in range(1, 81):
        clock = START + timedelta(minutes=minute)
        simulator.advance(clock)
        processor.step(clock)
    store.raise_alert("2026-06-01 13:00", CITY, "drop", 5, 60, -7)
    store.set_status(clock="2026-06-01 13:20:00", speed="60")
    store.close()

    state = read.feed_state(path)
    assert state.clock == START + timedelta(minutes=80)
    assert not state.running  # no heartbeat
    assert read.hour_summary(path, clock)["orders"] == 118  # the feed ended a minute before
    assert read.city_activity(path, clock)["city"].tolist() == [CITY]
    history = read.window_history(path, CITY, 10)
    assert len(history) == 10 and (history["low"] < history["high"]).all()
    assert len(read.recent_orders(path, 3)) == 3
    assert read.recent_alerts(path)["city"].tolist() == [CITY]
    assert len(read.recent_notes(path)) == 1
    assert read.watched_cities(path)[0] == ALL_CITIES


def test_an_outage_can_be_added_while_the_feed_runs(tmp_path: Path) -> None:
    path = tmp_path / "s.sqlite"
    Store(path).close()
    inject_outage(path, CITY, START, 60, 0.8)
    store = Store(path)
    (incident,) = store.open_incidents()
    store.close()
    assert incident == (CITY, "2026-06-01 12:00:00", "2026-06-01 13:00:00", 0.8)


def test_a_replay_can_be_measured_on_the_warehouse(pipeline: Pipeline) -> None:
    db = pipeline[0].duckdb_path("fixture")
    first, last = replay_days(db, 3)
    assert (last - first).days == 2
    assert replay_days(db, 3, skip_days=7)[1] == last - timedelta(days=7)
    orders = load_orders(db, first, last)
    assert orders["arrival"].is_monotonic_increasing
    assert "customer_id" not in orders.columns  # the feed carries no customer fields
    assert load_watch(db, first)
    assert load_rates(db, first, 3).per_minute

    report = evaluate(db, days=3)
    assert report["events"] == len(orders)
    assert set(report["by_severity"]) == {"30%", "50%", "70%", "100%"}


def test_the_stop_command(pipeline: Pipeline, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = pipeline[0]
    monkeypatch.setenv("WAREHOUSE_DIR", str(settings.warehouse_dir))
    get_settings.cache_clear()
    result = CliRunner().invoke(app, ["stream", "stop"])
    path = stream_path(settings.warehouse_dir)
    store = Store(path)
    requested = stop_requested(store)
    store.close()
    get_settings.cache_clear()
    assert result.exit_code == 0 and requested
