-- Late deliveries: how many miss the promise, and whether that changes by hour or city
-- "Late" means slower than the 40-minute promise (dbt var delivery_sla_minutes). All delivered
-- orders on all dates. The hour and city spreads come from mart_delivery_sla (city x hour).
with overall as (
    select
        count(*) as delivered,
        avg(case when is_sla_breach then 1.0 else 0.0 end) as late_share,
        quantile_cont(delivery_time_min, 0.5) as median_min,
        quantile_cont(delivery_time_min, 0.9) as p90_min
    from marts.fct_orders
    where is_delivered
),

hours as (
    select order_hour, sum(delivered_orders) as delivered, sum(sla_breaches) / sum(delivered_orders) as late_share
    from marts.mart_delivery_sla
    group by order_hour
),

top_cities as (
    select city, sum(delivered_orders) as delivered, sum(sla_breaches) / sum(delivered_orders) as late_share
    from marts.mart_delivery_sla
    group by city
    order by delivered desc, city
    limit 10
)

select
    o.delivered as delivered_orders,
    round(100 * o.late_share, 1) as late_pct,
    o.median_min as median_delivery_min,
    o.p90_min as p90_delivery_min,
    (select round(100 * min(late_share), 1) from hours) as late_pct_best_hour,
    (select round(100 * max(late_share), 1) from hours) as late_pct_worst_hour,
    (select round(max(delivered) / min(delivered)) from hours) as busiest_vs_quietest_hour_orders_x,
    (select round(100 * min(late_share), 1) from top_cities) as late_pct_best_top10_city,
    (select round(100 * max(late_share), 1) from top_cities) as late_pct_worst_top10_city
from overall as o
