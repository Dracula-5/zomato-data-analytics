-- dim_customer with no email at all, not even masked. This is the only customer table that
-- generated SQL may read: a whole-row reference such as `SELECT c FROM dim_customer c` would
-- otherwise return the email columns without ever naming them.
{{ config(materialized='view') }}

select
    customer_id,
    age_band,
    occupation,
    education,
    family_size,
    is_registered,
    first_order_date,
    last_order_date,
    lifetime_orders
from {{ ref('dim_customer') }}
