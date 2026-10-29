with spine as (
    {{ dbt_utils.date_spine(
        datepart="day",
        start_date="cast('" ~ var('date_spine_start') ~ "' as date)",
        end_date="cast('" ~ var('date_spine_end') ~ "' as date)"
    ) }}
)

select
    cast(date_day as date)                                    as date_day,
    extract(year from date_day)                               as year,
    extract(month from date_day)                              as month,
    extract(day from date_day)                                as day_of_month,
    {{ iso_day_of_week('date_day') }}                         as iso_day_of_week,
    {{ iso_day_of_week('date_day') }} in (6, 7)               as is_weekend,
    cast({{ dbt.date_trunc('week', 'date_day') }} as date)    as week_start,
    cast({{ dbt.date_trunc('month', 'date_day') }} as date)   as month_start
from spine
