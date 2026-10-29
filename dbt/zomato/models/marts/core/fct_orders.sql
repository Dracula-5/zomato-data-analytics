{{
    config(
        materialized='incremental',
        unique_key='order_id',
        incremental_strategy='merge',
        on_schema_change='append_new_columns'
    )
}}
-- One row per valid order. Incremental: each run MERGEs only the last `lookback_days` of
-- order dates, so late-arriving or corrected orders from recent days are picked up.

with orders as (
    select * from {{ ref('stg_orders') }}
    {% if is_incremental() %}
    where order_date >= {{ incremental_cutoff('order_date') }}
    {% endif %}
),

quarantined as (
    select distinct cast(record_key as bigint) as order_id
    from {{ ref('dq_quarantine') }}
    where table_name = 'orders'
)

select
    o.order_id,
    o.order_timestamp,
    o.order_date,
    o.order_hour,
    o.customer_id,
    o.restaurant_id,
    o.city,
    o.locality,
    o.cuisines,
    o.payment_method,
    o.order_status,
    o.is_delivered,
    o.is_cancelled,
    o.is_refunded,
    o.items_count,
    o.sales_qty,
    o.subtotal,
    o.discount,
    o.delivery_fee,
    o.gst,
    o.sales_amount,
    o.customer_rating,
    o.delivery_time_min,
    o.is_delivered and o.delivery_time_min > {{ var('delivery_sla_minutes') }} as is_sla_breach,
    o._loaded_at
from orders o
left join quarantined q on o.order_id = q.order_id
where q.order_id is null
