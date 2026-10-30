{#- PII handling used by marts. Raw personal values never leave the staging schema.

    mask_email: keeps the first character and the domain, e.g. 'r***@example.com'. Readable
                enough for support staff, useless for spam or re-identification.
    hash_pii:   salted SHA-256, a stable join key with no readable value. The salt comes
                from PII_HASH_SALT so hashes can't be reversed with a public email list.
    apply_masking: post-hook. Snowflake only: attaches the role-based masking policy from
                infra/snowflake/03_masking.sql. DuckDB has no such feature, so it does nothing. -#}

{% macro mask_email(col) -%}
    case
        when {{ col }} is null then null
        when position('@' in {{ col }}) > 1
            then left({{ col }}, 1) || '***' || substr({{ col }}, position('@' in {{ col }}))
        else '***'
    end
{%- endmacro %}

{% macro hash_pii(col) -%}
    {{ return(adapter.dispatch('hash_pii')(col)) }}
{%- endmacro %}
{% macro default__hash_pii(col) -%}
    sha2('{{ env_var("PII_HASH_SALT", "local-dev-salt") }}' || lower(trim({{ col }})), 256)
{%- endmacro %}
{% macro duckdb__hash_pii(col) -%}
    sha256('{{ env_var("PII_HASH_SALT", "local-dev-salt") }}' || lower(trim({{ col }})))
{%- endmacro %}

{% macro apply_masking(columns) -%}
    {{ return(adapter.dispatch('apply_masking')(columns)) }}
{%- endmacro %}
{% macro default__apply_masking(columns) -%}
{%- endmacro %}
{% macro snowflake__apply_masking(columns) -%}
    alter table {{ this }} modify
    {%- for col in columns %}
        column {{ col }} set masking policy {{ target.database }}.governance.pii_text
        {{- "," if not loop.last }}
    {%- endfor %}
{%- endmacro %}
