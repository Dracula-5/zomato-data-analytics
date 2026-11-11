-- How concentrated delivered GMV is across cities, over the last 365 days of data
with by_city as (
    select city, sum(gmv) as gmv
    from marts.mart_city_daily
    where order_date > (select max(order_date) from marts.mart_city_daily) - interval 365 day
    group by city
),

ranked as (
    select
        city,
        row_number() over (order by gmv desc, city) as rank,
        sum(gmv) over (order by gmv desc, city rows unbounded preceding) / sum(gmv) over () as cumulative_share
    from by_city
)

select
    count(*) as cities_with_orders,
    round(100 * max(cumulative_share) filter (where rank = 10), 1) as top_10_cities_gmv_pct,
    min(rank) filter (where cumulative_share >= 0.8) as cities_holding_80pct_of_gmv
from ranked
