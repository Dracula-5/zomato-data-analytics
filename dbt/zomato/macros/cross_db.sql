{#- Small helpers whose SQL differs between DuckDB and Snowflake. Each uses
    adapter.dispatch, so models stay identical on both warehouses. -#}

{#- Digits only, as an integer: '₹ 1,200' -> 1200; no digits -> null. -#}
{% macro text_to_int(col) -%}
    {{ return(adapter.dispatch('text_to_int')(col)) }}
{%- endmacro %}
{% macro default__text_to_int(col) -%}
    try_cast(nullif(regexp_replace({{ col }}, '[^0-9]', ''), '') as integer)
{%- endmacro %}
{% macro duckdb__text_to_int(col) -%}
    try_cast(nullif(regexp_replace({{ col }}, '[^0-9]', '', 'g'), '') as integer)
{%- endmacro %}

{#- Last comma-separated part: 'HSR,Bangalore' -> 'Bangalore'. -#}
{% macro last_part(col) -%}
    {{ return(adapter.dispatch('last_part')(col)) }}
{%- endmacro %}
{% macro default__last_part(col) -%}
    trim(split_part({{ col }}, ',', -1))
{%- endmacro %}
{% macro duckdb__last_part(col) -%}
    trim(string_split({{ col }}, ',')[-1])
{%- endmacro %}

{#- Collapse runs of whitespace (including newlines) into one space. -#}
{% macro squish(col) -%}
    {{ return(adapter.dispatch('squish')(col)) }}
{%- endmacro %}
{% macro default__squish(col) -%}
    trim(regexp_replace({{ col }}, '\\s+', ' '))
{%- endmacro %}
{% macro duckdb__squish(col) -%}
    trim(regexp_replace({{ col }}, '\s+', ' ', 'g'))
{%- endmacro %}

{#- ISO day of week: Monday = 1 ... Sunday = 7. -#}
{% macro iso_day_of_week(col) -%}
    {{ return(adapter.dispatch('iso_day_of_week')(col)) }}
{%- endmacro %}
{% macro default__iso_day_of_week(col) -%}
    dayofweekiso({{ col }})
{%- endmacro %}
{% macro duckdb__iso_day_of_week(col) -%}
    isodow({{ col }})
{%- endmacro %}

{#- Bucketed text to a lower bound: '1K+ ratings' -> 1000, 'Too Few Ratings' -> 0. -#}
{% macro rating_count_floor(col) -%}
    case
        when {{ col }} is null then null
        when {{ col }} like 'Too Few%' then 0
        when {{ col }} like '%K+%' then {{ text_to_int(col) }} * 1000
        else {{ text_to_int(col) }}
    end
{%- endmacro %}

{#- `as materialized (...)` makes DuckDB compute a CTE once even when it is referenced many
    times; other warehouses decide this themselves, so the keyword is emitted only on DuckDB. -#}
{% macro materialized_cte() -%}
    {{ return(adapter.dispatch('materialized_cte')()) }}
{%- endmacro %}
{% macro default__materialized_cte() -%}{%- endmacro %}
{% macro duckdb__materialized_cte() -%}materialized{%- endmacro %}

{#- Rounding that gives the same answer on both warehouses, as a DOUBLE.
    Snowflake's NUMBER division keeps about 6 decimals, so 753 * 1.0 / 12314 becomes 0.06115
    and then rounds to 0.0612, though the true 0.06114991 rounds to 0.0611. So: divide and
    average in DOUBLE, then do the final round in DECIMAL, where ties are exact. -#}
{% macro round_double(expr, digits) -%}
    cast(round(cast({{ expr }} as decimal(20, 10)), {{ digits }}) as double)
{%- endmacro %}

{% macro ratio(numerator, denominator, digits) -%}
    {{ round_double('cast(' ~ numerator ~ ' as double) / ' ~ denominator, digits) }}
{%- endmacro %}

{% macro round_avg(expr, digits) -%}
    {{ round_double('avg(cast(' ~ expr ~ ' as double))', digits) }}
{%- endmacro %}
