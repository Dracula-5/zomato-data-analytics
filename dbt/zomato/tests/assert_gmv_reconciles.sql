-- GMV must be identical whether computed from the fact, the city mart or the national mart.
with fact as (
    select sum(case when is_delivered then sales_amount else 0 end) as gmv
    from {{ ref('fct_orders') }}
),
city as (
    select sum(gmv) as gmv from {{ ref('mart_city_daily') }}
),
national as (
    select sum(gmv) as gmv from {{ ref('mart_daily_kpis') }}
)
select f.gmv as fact_gmv, c.gmv as city_gmv, n.gmv as national_gmv
from fact f, city c, national n
where f.gmv <> c.gmv or f.gmv <> n.gmv
