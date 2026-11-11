-- The next 14 days: ETS forecast, nationally, against the 14 days before them
with ahead as (
    select metric, min(target_date) as first_day, max(target_date) as last_day, sum(forecast) as forecast
    from forecast.next_days
    where model = 'ets' and scope = 'National'
    group by metric
),

before as (
    select sum(orders) as orders, sum(gmv) as gmv
    from marts.mart_daily_kpis
    where order_date > (select max(order_date) from marts.mart_daily_kpis) - interval 14 day
)

select
    a.metric,
    cast(a.first_day as date) as first_day,
    cast(a.last_day as date) as last_day,
    round(a.forecast) as forecast_next_14_days,
    case a.metric when 'orders' then b.orders else b.gmv end as actual_previous_14_days,
    round(100 * (a.forecast / (case a.metric when 'orders' then b.orders else b.gmv end) - 1), 1)
        as change_pct
from ahead as a
cross join before as b
order by a.metric desc
