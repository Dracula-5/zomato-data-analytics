-- One row per order line.
with source as (
    select * from {{ source('raw', 'order_items') }}
)

select
    order_item_id,
    order_id,
    r_id                                                      as restaurant_id,
    f_id                                                      as food_id,
    price,
    quantity,
    line_amount,
    order_date,
    _batch_id,
    _loaded_at
from source
qualify row_number() over (partition by order_date, order_item_id order by _loaded_at desc) = 1
