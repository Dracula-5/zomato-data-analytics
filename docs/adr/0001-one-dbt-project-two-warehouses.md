# ADR 1: One dbt project, two warehouses

**Status:** accepted

## Context

The platform has to run for free on a laptop, for development, tests and CI. It also has to show
real cloud work on Snowflake. Maintaining two sets of models would mean they drift apart.

## Decision

One dbt project targets both DuckDB and Snowflake. SQL that differs between them lives in
`macros/cross_db.sql` behind `adapter.dispatch`, so the models stay identical.

## Consequences

**Good:** every change is tested for free before it reaches Snowflake, and CI needs no cloud
account. Snowflake credits go on real work rather than on finding typos.

**Bad:** "the same SQL" doesn't mean "the same answer". Comparing the two engines found that
Snowflake's `NUMBER` division kept 6 decimals, so a rate was rounded twice: 753 of 12,314 orders
came out as 0.0612 rather than 0.0611. All 88 dbt tests passed on both engines at the time. Shared
rounding macros fixed it.

**What this changes:** a green test run is not proof of portability. `zap cloud compare` now
profiles every table on both engines, and that check is part of the definition of done.

## Alternatives

- **Snowflake only:** simpler, but every test run costs credits and CI needs credentials.
- **Two projects:** free of compromise, but they would drift within weeks.
