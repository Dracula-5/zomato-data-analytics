-- Forecast accuracy: average daily error (MAPE) of each model, nationally
-- From the walk-forward backtest `make forecast` writes: each model forecasts 14 days at a
-- time, 8 times, without seeing those days. The seasonal-naive baseline repeats last week.
select
    metric,
    model,
    round(100 * avg(abs(forecast - actual) / actual), 2) as mape_pct,
    count(*) as days_scored
from forecast.backtest
where scope = 'National' and actual > 0
group by metric, model
order by metric desc, mape_pct
