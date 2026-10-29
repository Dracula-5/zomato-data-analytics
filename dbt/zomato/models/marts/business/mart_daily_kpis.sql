-- National daily KPIs (input to forecasting and the dashboard overview). Contract enforced.
with daily as (
    select
        f.order_date,
        count(*)                                                    as orders,
        sum(case when f.is_delivered then 1 else 0 end)             as delivered_orders,
        sum(case when f.is_cancelled then 1 else 0 end)             as cancelled_orders,
        sum(case when f.is_sla_breach then 1 else 0 end)            as sla_breaches,
        sum(case when f.is_delivered then f.sales_amount else 0 end) as gmv,
        count(distinct f.customer_id)                               as active_customers
    from {{ ref('fct_orders') }} f
    group by f.order_date
)

select
    cast(k.order_date as date)                                      as order_date,
    cast(d.iso_day_of_week as integer)                              as iso_day_of_week,
    cast(d.is_weekend as boolean)                                   as is_weekend,
    cast(k.orders as bigint)                                        as orders,
    cast(k.delivered_orders as bigint)                              as delivered_orders,
    cast(k.gmv as bigint)                                           as gmv,
    cast(round(k.gmv * 1.0 / nullif(k.delivered_orders, 0), 2) as double) as aov,
    cast(round(k.cancelled_orders * 1.0 / k.orders, 4) as double)   as cancel_rate,
    cast(round(k.sla_breaches * 1.0 / nullif(k.delivered_orders, 0), 4) as double)
                                                                    as sla_breach_rate,
    cast(k.active_customers as bigint)                              as active_customers
from daily k
inner join {{ ref('dim_date') }} d on k.order_date = d.date_day
