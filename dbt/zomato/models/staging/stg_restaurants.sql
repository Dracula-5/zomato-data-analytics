-- One row per restaurant, with display text parsed into typed columns.
with source as (
    select * from {{ source('raw', 'restaurant') }}
)

select
    cast(id as bigint)                                        as restaurant_id,
    trim(name)                                                as restaurant_name,
    trim(city)                                                as location_raw,
    case when city like '%,%' then trim(split_part(city, ',', 1)) end as locality,
    {{ last_part('city') }}                                   as city,
    try_cast(nullif(trim(rating), '--') as double)            as rating,
    rating_count                                              as rating_count_bucket,
    {{ rating_count_floor('rating_count') }}                  as rating_count_floor,
    {{ text_to_int('cost') }}                                 as cost_for_two,
    trim(cuisine)                                             as cuisines,
    trim(split_part(cuisine, ',', 1))                         as primary_cuisine,
    lic_no                                                    as licence_number,
    link                                                      as source_url,
    {{ squish('address') }}                                   as address,
    menu                                                      as menu_path,
    _batch_id,
    _loaded_at
from source
