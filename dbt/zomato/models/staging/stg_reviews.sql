-- One row per review, as written. The reviewer and restaurant ids are kept under
-- "as recorded" names because they often disagree with the order (see fct_reviews).
with source as (
    select * from {{ source('raw', 'reviews') }}
)

select
    review_id,
    order_id,
    user_id                                                   as recorded_user_id,
    restaurant_id                                             as recorded_restaurant_id,
    rating,
    trim(comment)                                             as comment,
    review_date,
    _batch_id,
    _loaded_at
from source
qualify row_number() over (partition by review_date, review_id order by _loaded_at desc) = 1
