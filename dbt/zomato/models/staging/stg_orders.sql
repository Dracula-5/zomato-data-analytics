-- One row per order. Exact duplicate rows are collapsed here; the governance layer counts
-- how many extra copies arrived (rule ORD_DUPLICATE_ID).
with source as (
    select * from {{ source('raw', 'orders') }}
),

deduped as (
    select *
    from source
    -- Partitioning by the date as well lets an incremental date filter be pushed below
    -- this window, so incremental runs read only recent days (exact copies share a date).
    qualify row_number() over (partition by order_date, order_id order by _loaded_at desc) = 1
)

select
    order_id,
    order_timestamp,
    order_date,
    extract(hour from order_timestamp)                        as order_hour,
    user_id                                                   as customer_id,
    r_id                                                      as restaurant_id,
    restaurant_city                                           as location_raw,
    case when restaurant_city like '%,%'
        then trim(split_part(restaurant_city, ',', 1)) end    as locality,
    {{ last_part('restaurant_city') }}                        as city,
    cuisine                                                   as cuisines,
    items_count,
    sales_qty,
    subtotal,
    discount,
    delivery_fee,
    gst,
    sales_amount,
    currency,
    payment_method,
    order_status,
    order_status = 'Delivered'                                as is_delivered,
    order_status = 'Cancelled'                                as is_cancelled,
    order_status = 'Refunded'                                 as is_refunded,
    customer_rating,
    delivery_time_min,
    _batch_id,
    _loaded_at
from deduped
