-- Review volume and sentiment proxies per city and month. LLM-derived sentiment and topics
-- are added in the AI layer (M8); here sentiment is approximated by star rating.
select
    city,
    cast({{ dbt.date_trunc('month', 'review_date') }} as date)               as review_month,
    count(*)                                                                  as reviews,
    round(avg(rating), 2)                                                     as avg_rating,
    round(avg(case when rating <= 2 then 1.0 else 0.0 end), 4)                as negative_share,
    round(avg(case when rating >= 4 then 1.0 else 0.0 end), 4)                as positive_share,
    sum(case when is_before_order then 1 else 0 end)                          as reviews_before_order,
    sum(case when is_on_undelivered_order then 1 else 0 end)                  as reviews_on_undelivered
from {{ ref('fct_reviews') }}
group by city, cast({{ dbt.date_trunc('month', 'review_date') }} as date)
