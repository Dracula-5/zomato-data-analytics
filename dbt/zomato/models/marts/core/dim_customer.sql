-- One row per customer: everyone who ordered, plus registered users who never ordered.
-- PII policy for marts: name and other personal attributes are excluded; email appears only
-- masked (for display) and as a salted hash (for joining). Raw values stay in staging.
with ordering as (
    select
        customer_id,
        min(order_date)                                       as first_order_date,
        max(order_date)                                       as last_order_date,
        count(*)                                              as lifetime_orders
    from {{ ref('stg_orders') }}
    group by customer_id
),

registered as (
    select * from {{ ref('stg_users') }}
),

customers as (
    select customer_id from ordering
    union
    select user_id from registered
)

select
    c.customer_id,
    {{ mask_email('u.email') }}                               as email_masked,
    {{ hash_pii('u.email') }}                                 as email_hash,
    case
        when u.age is null then 'unknown'
        when u.age < 25 then '18-24'
        when u.age < 35 then '25-34'
        when u.age < 45 then '35-44'
        else '45+'
    end                                                       as age_band,
    u.occupation,
    u.education,
    u.family_size,
    u.user_id is not null                                     as is_registered,
    o.first_order_date,
    o.last_order_date,
    coalesce(o.lifetime_orders, 0)                            as lifetime_orders
from customers c
left join registered u on c.customer_id = u.user_id
left join ordering o on c.customer_id = o.customer_id
