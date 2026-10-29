{{ config(materialized='table') }}
-- Every data-quality rule in seeds/dq_rules.csv, evaluated per record.
-- One row = one record that broke one rule. Severity and action come from the seed.
-- A TABLE, not a view: dq_quarantine and dq_results both read it, and as a view the 26
-- rules over 33M rows ran twice, in parallel, and ran out of memory on the full data.
-- The staging CTEs are materialized so each 10M/23M-row source is de-duplicated once.

with orders as {{ materialized_cte() }} (select * from {{ ref('stg_orders') }}),
items as {{ materialized_cte() }} (select * from {{ ref('stg_order_items') }}),
reviews as {{ materialized_cte() }} (select * from {{ ref('stg_reviews') }}),
restaurants as {{ materialized_cte() }} (select * from {{ ref('stg_restaurants') }}),
foods as (select * from {{ ref('stg_food') }}),
menu as {{ materialized_cte() }} (select * from {{ ref('stg_menu') }}),
users as (select * from {{ ref('stg_users') }}),

item_totals as (
    select order_id, sum(line_amount) as line_total
    from items
    group by order_id
),

violations as (
    select 'ORD_DUPLICATE_ID' as rule_id, cast(order_id as varchar) as record_key,
           cast(count(*) - 1 as varchar) || ' extra copies' as detail
    from {{ source('raw', 'orders') }}
    group by order_id
    having count(*) > 1

    union all
    select 'ORD_AMOUNT_MISMATCH', cast(order_id as varchar),
           'sales_amount ' || cast(sales_amount as varchar) || ' vs computed '
           || cast(subtotal - discount + delivery_fee + gst as varchar)
    from orders
    where sales_amount <> subtotal - discount + delivery_fee + gst

    union all
    select 'ORD_NEGATIVE_AMOUNT', cast(order_id as varchar), 'negative money column'
    from orders
    where least(subtotal, discount, delivery_fee, gst, sales_amount) < 0

    union all
    select 'ORD_ORPHAN_RESTAURANT', cast(o.order_id as varchar),
           'restaurant ' || cast(o.restaurant_id as varchar)
    from orders o
    left join restaurants r on o.restaurant_id = r.restaurant_id
    where r.restaurant_id is null

    union all
    select 'ORD_ORPHAN_CUSTOMER', cast(o.order_id as varchar),
           'customer ' || cast(o.customer_id as varchar)
    from orders o
    left join users u on o.customer_id = u.user_id
    where u.user_id is null
      and exists (select 1 from users)   -- skip while users.csv is not loaded

    union all
    select 'ORD_DELIVERED_NO_TIME', cast(order_id as varchar), 'delivery_time_min is null'
    from orders
    where is_delivered and delivery_time_min is null

    union all
    select 'ORD_SUBTOTAL_MISMATCH', cast(o.order_id as varchar),
           'subtotal ' || cast(o.subtotal as varchar) || ' vs lines ' || cast(t.line_total as varchar)
    from orders o
    inner join item_totals t on o.order_id = t.order_id
    where o.subtotal <> t.line_total

    union all
    select 'ITM_ORPHAN_ORDER', cast(i.order_item_id as varchar),
           'order ' || cast(i.order_id as varchar)
    from items i
    left join orders o on i.order_id = o.order_id
    where o.order_id is null

    union all
    select 'ITM_LINE_AMOUNT_MISMATCH', cast(order_item_id as varchar),
           cast(line_amount as varchar) || ' vs ' || cast(price * quantity as varchar)
    from items
    where abs(line_amount - price * quantity) > quantity

    union all
    select 'ITM_PRICE_ROUNDED', cast(order_item_id as varchar),
           cast(line_amount as varchar) || ' vs ' || cast(price * quantity as varchar)
    from items
    where line_amount <> price * quantity
      and abs(line_amount - price * quantity) <= quantity

    union all
    select 'REV_ORPHAN_ORDER', cast(v.review_id as varchar), 'order ' || cast(v.order_id as varchar)
    from reviews v
    left join orders o on v.order_id = o.order_id
    where o.order_id is null

    union all
    select 'REV_RESTAURANT_MISMATCH', cast(v.review_id as varchar),
           'recorded ' || cast(v.recorded_restaurant_id as varchar)
           || ', order has ' || cast(o.restaurant_id as varchar)
    from reviews v
    inner join orders o on v.order_id = o.order_id
    where v.recorded_restaurant_id <> o.restaurant_id

    union all
    select 'REV_USER_MISMATCH', cast(v.review_id as varchar),
           'recorded ' || cast(v.recorded_user_id as varchar)
           || ', order has ' || cast(o.customer_id as varchar)
    from reviews v
    inner join orders o on v.order_id = o.order_id
    where v.recorded_user_id <> o.customer_id

    union all
    select 'REV_BEFORE_ORDER', cast(v.review_id as varchar),
           cast(v.review_date as varchar) || ' < ' || cast(o.order_date as varchar)
    from reviews v
    inner join orders o on v.order_id = o.order_id
    where v.review_date < o.order_date

    union all
    select 'REV_ON_UNDELIVERED', cast(v.review_id as varchar), o.order_status
    from reviews v
    inner join orders o on v.order_id = o.order_id
    where not o.is_delivered

    union all
    select 'RST_UNPARSEABLE_COST', cast(restaurant_id as varchar), 'cost has no digits'
    from restaurants
    where cost_for_two is null

    union all
    select 'RST_COST_OUTLIER', cast(restaurant_id as varchar), cast(cost_for_two as varchar)
    from restaurants
    where cost_for_two > {{ var('cost_outlier_inr') }}

    union all
    select 'RST_NO_RATING', cast(restaurant_id as varchar), coalesce(rating_count_bucket, 'null')
    from restaurants
    where rating is null

    union all
    select 'FOOD_INVALID_VEG_FLAG', food_id, coalesce(veg_flag_raw, 'null')
    from foods
    where is_veg is null

    union all
    select 'FOOD_MISSING_NAME', food_id, 'item is null'
    from foods
    where food_name is null or food_name = ''

    union all
    select 'MENU_ORPHAN_RESTAURANT', m.menu_id || '/' || m.food_id,
           'restaurant ' || cast(m.restaurant_id as varchar)
    from menu m
    left join restaurants r on m.restaurant_id = r.restaurant_id
    where r.restaurant_id is null

    union all
    select 'MENU_ORPHAN_FOOD', m.menu_id || '/' || m.food_id, 'food ' || m.food_id
    from menu m
    left join foods f on m.food_id = f.food_id
    where f.food_id is null

    union all
    select 'MENU_UNPARSEABLE_PRICE', menu_id || '/' || food_id, 'price is not a number'
    from menu
    where price is null

    union all
    select 'MENU_DUPLICATE_ITEM', menu_id || '/' || food_id,
           'restaurant ' || cast(restaurant_id as varchar) || ' lists ' || food_id || ' again'
    from menu
    qualify row_number() over (partition by restaurant_id, food_id order by menu_id) > 1

    union all
    select 'USR_INVALID_EMAIL', cast(user_id as varchar), coalesce(email, 'null')
    from users
    where email is null or email not like '%_@_%._%'

    union all
    select 'USR_DUPLICATE_EMAIL', cast(user_id as varchar), 'email used by another user'
    from users
    where email is not null
    qualify row_number() over (partition by email order by user_id) > 1
)

select
    v.rule_id,
    r.table_name,
    v.record_key,
    v.detail,
    r.severity,
    r.action
from violations v
inner join {{ ref('dq_rules') }} r on v.rule_id = r.rule_id
