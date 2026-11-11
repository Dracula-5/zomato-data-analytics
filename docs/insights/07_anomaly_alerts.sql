-- Anomaly alerts: share of planted incidents each detector catches, by size (SYNTHETIC)
-- Incidents are planted in copies of the 22 daily series (national and top cities, orders and
-- GMV), never in the warehouse. A daily drop of 20% in one series is the smallest planted.
select
    s.detector,
    round(100 * max(e.recall) filter (where e.event = 'drop_20pct'), 1) as caught_20pct_drops_pct,
    round(100 * max(e.recall) filter (where e.event = 'drop_30pct'), 1) as caught_30pct_drops_pct,
    round(100 * max(e.recall) filter (where e.event = 'drop_50pct'), 1) as caught_50pct_drops_pct,
    round(100 * max(e.recall) filter (where e.event = 'outage_40pct_3days'), 1) as caught_3day_outages_pct,
    round(100 * s.recall, 1) as caught_all_planted_pct,
    s.false_alarms_per_1000 as false_alarms_per_1000_normal_days
from forecast.detector_scores as s
inner join forecast.event_recall as e on e.detector = s.detector
group by s.detector, s.recall, s.false_alarms_per_1000
order by caught_all_planted_pct desc
