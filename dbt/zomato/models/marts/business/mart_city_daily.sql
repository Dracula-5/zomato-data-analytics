-- Daily KPIs per city. GMV = value of DELIVERED orders (cancelled and refunded excluded);
-- AOV = GMV / delivered orders. Columns are cast explicitly because the model contract
-- is enforced: consumers get stable types on DuckDB and Snowflake alike.
with daily as (
    select
        order_date,
        city,
        count(*)                                                  as orders,
        sum(case when is_delivered then 1 else 0 end)             as delivered_orders,
        sum(case when is_cancelled then 1 else 0 end)             as cancelled_orders,
        sum(case when is_refunded then 1 else 0 end)              as refunded_orders,
        sum(case when is_delivered then sales_amount else 0 end)  as gmv,
        sum(discount)                                             as discount,
        sum(subtotal)                                             as subtotal,
        count(distinct customer_id)                               as active_customers
    from {{ ref('fct_orders') }}
    group by order_date, city
)

select
    cast(order_date as date)                                      as order_date,
    cast(city as varchar)                                         as city,
    cast(orders as bigint)                                        as orders,
    cast(delivered_orders as bigint)                              as delivered_orders,
    cast(cancelled_orders as bigint)                              as cancelled_orders,
    cast(refunded_orders as bigint)                               as refunded_orders,
    cast(gmv as bigint)                                           as gmv,
    {{ ratio('gmv', 'nullif(delivered_orders, 0)', 2) }}          as aov,
    {{ ratio('cancelled_orders', 'orders', 4) }}                  as cancel_rate,
    {{ ratio('discount', 'nullif(subtotal, 0)', 4) }}             as discount_rate,
    cast(active_customers as bigint)                              as active_customers
from daily
