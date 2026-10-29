-- One row per city, rolled up from restaurant locations ("Koramangala,Bangalore" -> Bangalore).
select
    city,
    count(distinct locality)                                  as localities,
    count(*)                                                  as restaurants,
    round(avg(rating), 2)                                     as avg_restaurant_rating,
    round(avg(cost_for_two), 0)                               as avg_cost_for_two
from {{ ref('stg_restaurants') }}
group by city
