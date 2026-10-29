{#- Use the custom schema as-is (`marts`, not `main_marts`) so the schema names
    match the Snowflake layout: RAW / STAGING / MARTS / GOVERNANCE / SNAPSHOTS. -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
