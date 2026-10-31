"""Forecasts: walk-forward accuracy, the next 14 days, and anomaly detection."""

import plotly.graph_objects as go
import streamlit as st

from zap.dashboard.theme import series_color, style
from zap.dashboard.ui import load, page_setup, table_view, theme_mode

MODEL_NAMES = {
    "seasonal_naive": "Seasonal naive (baseline)",
    "ets": "ETS (Holt-Winters)",
    "lightgbm": "LightGBM",
}
METRIC_NAMES = {"orders": "Orders", "gmv": "GMV"}
DETECTOR_NAMES = {
    "seasonal_robust_z": "Same-weekday robust z-score",
    "plain_rolling_z": "Plain rolling z-score",
}
# Biggest first, top to bottom. Plotly draws horizontal bars from the bottom up.
EVENT_NAMES = {
    "drop_50pct": "50% drop",
    "outage_40pct_3days": "40% outage, 3 days",
    "spike_50pct": "50% spike",
    "drop_30pct": "30% drop",
    "spike_30pct": "30% spike",
    "drop_20pct": "20% drop",
    "spike_20pct": "20% spike",
}

db = page_setup("Forecasts")
if db is not None:
    if not load("has_forecasts", db):
        st.info("No forecasts for this dataset yet. Run `make forecast`, which uses the full data.")
        st.stop()

    mode = theme_mode()
    left, right = st.columns([1, 2])
    metric = left.radio(
        "Measure", list(METRIC_NAMES), horizontal=True, format_func=METRIC_NAMES.get
    )
    scope = right.selectbox("Where", load("forecast_scopes", db))

    accuracy = load("forecast_accuracy", db, metric, scope)
    best = load("best_forecast_model", db, metric)
    st.caption(
        f"Eight 14-day folds, each using only earlier data. Lowest error across all series: "
        f"{MODEL_NAMES[best]}."
    )
    tiles = st.columns(len(accuracy))
    for tile, row in zip(tiles, accuracy.itertuples(), strict=True):
        tile.metric(
            MODEL_NAMES[row.model],
            f"{row.mape:.2f}%",
            help="MAPE: the average size of the error, as a percentage of the actual value.",
        )

    floor = load("noise_floor", db, scope)
    if metric == "orders" and floor is not None:
        st.caption(
            f"If daily orders vary at random (Poisson), even a perfect forecast would score "
            f"about {floor:.2f}% here. A model near that has nothing left to learn."
        )

    model = st.selectbox(
        "Model on the chart",
        accuracy["model"].tolist(),
        index=accuracy["model"].tolist().index(best),
        format_func=MODEL_NAMES.get,
    )
    lines = load("forecast_lines", db, metric, scope, model)
    tested = lines[lines["kind"] == "backtest"]
    future = lines[lines["kind"] == "next"]
    unit = "₹" if metric == "gmv" else ""
    fig = go.Figure()
    fig.add_scatter(
        x=tested["target_date"],
        y=tested["actual"],
        name="Actual",
        mode="lines",
        line={"width": 2, "color": series_color(mode, 0)},
        hovertemplate=f"Actual: {unit}%{{y:,.0f}}<extra></extra>",
    )
    fig.add_scatter(
        x=tested["target_date"],
        y=tested["forecast"],
        name="Forecast, backtest",
        mode="lines",
        line={"width": 2, "color": series_color(mode, 1)},
        hovertemplate=f"Forecast: {unit}%{{y:,.0f}}<extra></extra>",
    )
    fig.add_scatter(
        x=future["target_date"],
        y=future["forecast"],
        name="Forecast, next 14 days",
        mode="lines",
        line={"width": 2, "color": series_color(mode, 1), "dash": "dot"},
        hovertemplate=f"Forecast: {unit}%{{y:,.0f}}<extra></extra>",
    )
    fig.update_layout(title=f"{METRIC_NAMES[metric]} per day, {scope}", hovermode="x unified")
    st.plotly_chart(style(fig, mode, height=400), width="stretch")
    table_view(accuracy, "Accuracy as a table")

    st.subheader("Anomaly detection")
    st.caption(
        "Scored on SYNTHETIC events injected into a copy of each series. The warehouse is "
        "never changed, and the thresholds were set before seeing any result."
    )
    scores = load("detector_scores", db)
    cells = st.columns(len(scores))
    for cell, row in zip(cells, scores.itertuples(), strict=True):
        cell.metric(f"{DETECTOR_NAMES[row.detector]}: recall", f"{row.recall:.1%}")
        cell.metric("Precision", f"{row.precision:.1%}")
        cell.metric("False alarms per 1,000 normal days", f"{row.false_alarms_per_1000:.1f}")

    st.caption(
        "Precision depends on how often anomalies happen: here, 2.3% of days are injected. "
        "False alarms per 1,000 normal days don't, so compare detectors on that."
    )

    events = load("event_recall", db)
    chosen = events[events["detector"] == "seasonal_robust_z"].set_index("event")
    chosen = chosen.loc[list(EVENT_NAMES)[::-1]]
    bars = go.Figure(
        go.Bar(
            x=chosen["recall"],
            y=[EVENT_NAMES[event] for event in chosen.index],
            orientation="h",
            marker={"color": series_color(mode), "cornerradius": 4},
            text=[f"{v:.1%}" for v in chosen["recall"]],
            textposition="outside",
            hovertemplate="%{y}<br>Recall: %{x:.1%}<extra></extra>",
        )
    )
    bars.update_layout(
        title="Same-weekday detector: share of injected days found",
        xaxis={"range": [0, 1.12], "tickformat": ".0%", "tickvals": [0, 0.25, 0.5, 0.75, 1]},
    )
    st.plotly_chart(style(bars, mode, height=340), width="stretch")

    flagged = load("real_anomalies", db)
    st.write(
        f"Days flagged in the real, unchanged series: **{len(flagged):,}**. That's about the "
        "false-alarm rate, so nothing here points to a real incident."
    )
    table_view(flagged, "Flagged days as a table")
