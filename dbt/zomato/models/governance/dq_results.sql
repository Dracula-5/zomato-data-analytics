-- The data-quality report: for every rule, how many records were checked and how many
-- failed, in this build. Rules that caught nothing still appear (rows_failed = 0).
with checked as (
    select 'orders' as table_name, count(*) as rows_checked from {{ source('raw', 'orders') }}
    union all select 'order_items', count(*) from {{ source('raw', 'order_items') }}
    union all select 'reviews', count(*) from {{ source('raw', 'reviews') }}
    union all select 'restaurant', count(*) from {{ source('raw', 'restaurant') }}
    union all select 'food', count(*) from {{ source('raw', 'food') }}
    union all select 'menu', count(*) from {{ source('raw', 'menu') }}
    union all select 'users', count(*) from {{ source('raw', 'users') }}
),

failed as (
    select rule_id, count(*) as rows_failed
    from {{ ref('int_dq_violations') }}
    group by rule_id
)

select
    r.rule_id,
    r.table_name,
    r.description,
    r.severity,
    r.action,
    c.rows_checked,
    coalesce(f.rows_failed, 0)                                           as rows_failed,
    case when c.rows_checked > 0
        then {{ ratio('100.0 * coalesce(f.rows_failed, 0)', 'c.rows_checked', 4) }} end as failed_pct,
    current_timestamp                                                    as checked_at
from {{ ref('dq_rules') }} r
inner join checked c on r.table_name = c.table_name
left join failed f on r.rule_id = f.rule_id
