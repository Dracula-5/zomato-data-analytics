{{
    config(
        materialized='incremental',
        unique_key='order_item_id',
        incremental_strategy='merge',
        on_schema_change='append_new_columns'
    )
}}
-- One row per valid order line, partition-aligned with its order's date.

with items as (
    select * from {{ ref('stg_order_items') }}
    {% if is_incremental() %}
    where order_date >= {{ incremental_cutoff('order_date') }}
    {% endif %}
),

quarantined_items as (
    select distinct cast(record_key as bigint) as order_item_id
    from {{ ref('dq_quarantine') }}
    where table_name = 'order_items'
),

quarantined_orders as (
    select distinct cast(record_key as bigint) as order_id
    from {{ ref('dq_quarantine') }}
    where table_name = 'orders'
)

select
    i.order_item_id,
    i.order_id,
    i.order_date,
    i.restaurant_id,
    i.food_id,
    i.price,
    i.quantity,
    i.line_amount,
    i._loaded_at
from items i
left join quarantined_items qi on i.order_item_id = qi.order_item_id
left join quarantined_orders qo on i.order_id = qo.order_id
where qi.order_item_id is null
  and qo.order_id is null
