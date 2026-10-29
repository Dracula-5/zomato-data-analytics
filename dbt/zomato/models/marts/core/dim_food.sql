select
    food_id,
    food_name,
    is_veg
from {{ ref('stg_food') }}
