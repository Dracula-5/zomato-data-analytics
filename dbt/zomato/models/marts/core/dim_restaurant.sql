-- Current version of every restaurant (the open row of the SCD2 snapshot).
select
    restaurant_id,
    restaurant_name,
    locality,
    city,
    rating,
    rating_count_bucket,
    rating_count_floor,
    cost_for_two,
    cuisines,
    primary_cuisine,
    licence_number,
    source_url,
    address,
    dbt_valid_from                                            as valid_from
from {{ ref('snap_restaurants') }}
where dbt_valid_to is null
