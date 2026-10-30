-- Lifetime performance per restaurant, ranked by GMV within its city.
with orders as (
    select
        restaurant_id,
        count(*)                                                              as orders,
        sum(case when is_delivered then 1 else 0 end)                         as delivered_orders,
        sum(case when is_delivered then sales_amount else 0 end)              as gmv,
        {{ round_avg('case when is_cancelled then 1.0 else 0.0 end', 4) }}    as cancel_rate,
        {{ round_avg('customer_rating', 2) }}                                 as avg_customer_rating,
        {{ round_avg('delivery_time_min', 1) }}                               as avg_delivery_min,
        percentile_cont(0.9) within group (order by delivery_time_min)        as p90_delivery_min,
        {{ round_avg('case when is_sla_breach then 1.0 when is_delivered then 0.0 end', 4) }}
                                                                              as sla_breach_rate,
        min(order_date)                                                       as first_order_date,
        max(order_date)                                                       as last_order_date
    from {{ ref('fct_orders') }}
    group by restaurant_id
),

reviews as (
    select
        restaurant_id,
        count(*)                                                              as reviews,
        {{ round_avg('rating', 2) }}                                          as avg_review_rating
    from {{ ref('fct_reviews') }}
    group by restaurant_id
)

select
    r.restaurant_id,
    r.restaurant_name,
    r.city,
    r.locality,
    r.primary_cuisine,
    r.rating                                                                  as listed_rating,
    r.cost_for_two,
    o.orders,
    o.delivered_orders,
    o.gmv,
    {{ ratio('o.gmv', 'nullif(o.delivered_orders, 0)', 2) }}                  as aov,
    o.cancel_rate,
    o.avg_customer_rating,
    o.avg_delivery_min,
    o.p90_delivery_min,
    o.sla_breach_rate,
    coalesce(v.reviews, 0)                                                    as reviews,
    v.avg_review_rating,
    o.first_order_date,
    o.last_order_date,
    rank() over (partition by r.city order by o.gmv desc)                     as gmv_rank_in_city
from {{ ref('dim_restaurant') }} r
inner join orders o on r.restaurant_id = o.restaurant_id
left join reviews v on r.restaurant_id = v.restaurant_id
