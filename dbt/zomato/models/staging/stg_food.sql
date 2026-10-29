-- One row per food item.
with source as (
    select * from {{ source('raw', 'food') }}
)

select
    f_id                                                      as food_id,
    trim(item)                                                as food_name,
    veg_or_non_veg                                            as veg_flag_raw,
    case veg_or_non_veg when 'Veg' then true when 'Non-veg' then false end as is_veg,
    _batch_id,
    _loaded_at
from source
