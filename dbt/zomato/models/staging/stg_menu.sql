-- One row per restaurant x food menu line.
with source as (
    select * from {{ source('raw', 'menu') }}
)

select
    menu_id,
    try_cast(r_id as bigint)                                  as restaurant_id,
    f_id                                                      as food_id,
    trim(cuisine)                                             as cuisine,
    try_cast(price as decimal(12, 2))                         as price,
    _batch_id,
    _loaded_at
from source
