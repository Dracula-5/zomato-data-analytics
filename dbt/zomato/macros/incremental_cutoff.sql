{#- First date an incremental run reprocesses: max(<column>) already in the target table
    minus `lookback_days`. Computed when the model compiles, so the SQL contains a literal
    date. A literal can be pushed down to the scan (and below the staging dedupe window);
    a scalar subquery cannot, and would make the "incremental" run read every row. -#}
{% macro incremental_cutoff(column) -%}
    {%- set max_value = none -%}
    {%- if execute -%}
        {%- set result = run_query('select max(' ~ column ~ ') from ' ~ this) -%}
        {%- set max_value = result.columns[0].values()[0] -%}
    {%- endif -%}
    {%- set start = (max_value | string) if max_value is not none else '1900-01-01' -%}
    {{ dbt.dateadd('day', -var('lookback_days'), "cast('" ~ start ~ "' as date)") }}
{%- endmacro %}
