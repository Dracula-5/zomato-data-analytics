-- Demand by day of week, over the last 365 days of data, all cities
-- Late and cancelled shares are weighted by order counts, so busy days count for more.
with recent as (
    select *
    from marts.mart_daily_kpis
    where order_date > (select max(order_date) from marts.mart_daily_kpis) - interval 365 day
),

by_day as (
    select
        iso_day_of_week,
        any_value(dayname(order_date)) as day,
        avg(orders) as avg_orders,
        avg(gmv) as avg_gmv_inr,
        sum(sla_breach_rate * delivered_orders) / sum(delivered_orders) as late_share,
        sum(cancel_rate * orders) / sum(orders) as cancelled_share
    from recent
    group by iso_day_of_week
)

select
    day,
    round(avg_orders) as avg_orders,
    round(avg_gmv_inr) as avg_gmv_inr,
    round(100 * (avg_orders / min(avg_orders) over () - 1), 1) as orders_above_quietest_day_pct,
    round(100 * late_share, 1) as late_delivery_pct,
    round(100 * cancelled_share, 1) as cancelled_pct
from by_day
order by iso_day_of_week
