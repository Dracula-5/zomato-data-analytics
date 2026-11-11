-- The 10 largest cities by delivered GMV, over the last 365 days of data
select
    city,
    round(100 * sum(gmv) / sum(sum(gmv)) over (), 1) as gmv_share_pct,
    sum(orders) as orders,
    round(sum(gmv) / sum(delivered_orders)) as aov_inr,
    round(100 * sum(cancelled_orders) / sum(orders), 1) as cancelled_pct
from marts.mart_city_daily
where order_date > (select max(order_date) from marts.mart_city_daily) - interval 365 day
group by city
qualify row_number() over (order by sum(gmv) desc, city) <= 10
order by gmv_share_pct desc, city
