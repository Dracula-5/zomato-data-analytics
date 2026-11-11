-- Real days the main detector flagged, against what its false-alarm rate alone predicts
with series as (
    select count(*) as n from (select distinct metric, scope from forecast.backtest)
),

days as (
    select count(*) as n from marts.mart_daily_kpis
)

select
    (select count(*) from forecast.anomalies) as real_days_flagged,
    series.n * days.n as series_days_checked,
    round(100.0 * (select count(*) from forecast.anomalies) / (series.n * days.n), 2) as flagged_pct,
    round(
        series.n * days.n
        * (select false_alarms_per_1000 from forecast.detector_scores where detector = 'seasonal_robust_z')
        / 1000
    ) as expected_from_false_alarms_alone
from series, days
