-- Every distinct source order is either in fct_orders or in quarantine: nothing is lost
-- and nothing is invented. Returns a row (= failure) when the counts disagree.
with source_orders as (
    select count(distinct order_id) as n from {{ source('raw', 'orders') }}
),
fact_orders as (
    select count(*) as n from {{ ref('fct_orders') }}
),
quarantined as (
    select count(distinct record_key) as n
    from {{ ref('dq_quarantine') }}
    where table_name = 'orders'
)
select s.n as source_orders, f.n as fact_orders, q.n as quarantined_orders
from source_orders s, fact_orders f, quarantined q
where s.n <> f.n + q.n
