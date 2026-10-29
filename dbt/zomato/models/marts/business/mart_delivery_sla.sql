-- Delivery performance by city and hour of day: median (p50) and p90 delivery time, and
-- the share of delivered orders slower than the promised `delivery_sla_minutes`.
select
    city,
    order_hour,
    count(*)                                                                  as delivered_orders,
    percentile_cont(0.5) within group (order by delivery_time_min)            as p50_delivery_min,
    percentile_cont(0.9) within group (order by delivery_time_min)            as p90_delivery_min,
    sum(case when is_sla_breach then 1 else 0 end)                            as sla_breaches,
    round(sum(case when is_sla_breach then 1 else 0 end) * 1.0 / count(*), 4) as sla_breach_rate
from {{ ref('fct_orders') }}
where is_delivered
group by city, order_hour
