{{
    config(
        materialized='incremental',
        unique_key='review_id',
        incremental_strategy='merge',
        on_schema_change='append_new_columns'
    )
}}
-- One row per valid review. The ORDER is the source of truth for who reviewed what:
-- in this dataset the recorded user and restaurant almost never match the order
-- (rules REV_USER_MISMATCH / REV_RESTAURANT_MISMATCH), so they are resolved from it.
-- Joining fct_orders (not staging) also drops reviews of quarantined orders.

with reviews as (
    select * from {{ ref('stg_reviews') }}
    {% if is_incremental() %}
    -- Recent reviews, plus older ones whose ORDER only just arrived: some reviews are dated
    -- before their order, and the inner join below drops them until the order exists.
    -- Filtering on review_date alone would never look at them again.
    where review_date >= {{ incremental_cutoff('review_date') }}
       or order_id in (
           select order_id from {{ ref('fct_orders') }}
           where order_date >= {{ incremental_cutoff('order_date') }}
       )
    {% endif %}
),

quarantined as (
    select distinct cast(record_key as bigint) as review_id
    from {{ ref('dq_quarantine') }}
    where table_name = 'reviews'
)

select
    v.review_id,
    v.order_id,
    v.review_date,
    o.order_date,
    o.customer_id,
    o.restaurant_id,
    o.city,
    v.rating,
    v.comment,
    v.recorded_user_id <> o.customer_id                       as had_user_mismatch,
    v.recorded_restaurant_id <> o.restaurant_id               as had_restaurant_mismatch,
    v.review_date < o.order_date                              as is_before_order,
    not o.is_delivered                                        as is_on_undelivered_order,
    v._loaded_at
from reviews v
inner join {{ ref('fct_orders') }} o on v.order_id = o.order_id
left join quarantined q on v.review_id = q.review_id
where q.review_id is null
