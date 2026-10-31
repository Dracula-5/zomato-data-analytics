# Business case

## The problem

A food-delivery business runs on data that arrives from many sources, is often wrong, and is
needed daily by people who don't write SQL. Three questions keep coming up:

1. **Can we trust the numbers?** A dashboard is only useful if someone can say why a figure is
   right, which records were excluded, and whether yesterday's load repeated cleanly.
2. **Can the business get answers without a queue?** Analysts spend time on repeat questions, while
   decision-makers wait.
3. **How much demand should we plan for?** Staffing and rider supply depend on knowing tomorrow's
   orders, by city.

## What this platform provides

| Need | What it does | Who benefits |
|---|---|---|
| Trusted data | Contracts stop bad files at the door. 26 rules count every defect, and bad records are quarantined rather than silently lost | Finance, analytics |
| Repeatable loads | Daily runs are idempotent and alert on failure or lateness, so a repeat can't double-count | Data engineering |
| Self-service answers | A dashboard, plus a copilot that answers in plain English with its checks shown | Operations, leadership |
| Demand planning | 14-day forecasts by city, tested on eight past periods | Operations, supply |
| Early warning | Anomaly detection that respects the weekly pattern | Operations |
| Partner insight | Finds chains and shared-kitchen brands from menu overlap | Sales, partnerships |
| Controlled AI | Budget caps, a SQL guard, and evaluation against known answers | Risk, leadership |

## What the data can and can't support

This matters more than any feature, so it comes first in any conversation about value.

The orders in this dataset are **synthetic**, while the restaurant and menu data looks real. That
shapes what the platform can honestly claim:

| Question | Finding | What a business should do |
|---|---|---|
| How will demand grow? | Strong, stable growth and a weekly peak. Forecasts sit at the noise floor | Plan capacity on these forecasts |
| Which restaurants are chains? | Found from menus, without using names | Use for partner management |
| Which brands share kitchens? | Groups with one menu under several names | Check with partner records before acting |
| Which restaurants cancel too much? | None, after correcting for chance | Don't act on a raw ranking: it would punish noise |
| Are some cities late on delivery? | No: delivery time is flat by city and hour | No action on this dataset |
| What will a customer order next? | No personal taste is present | Don't invest in personalization on this data |

The cancellation row shows why this matters. A raw league table would name thousands of "problem"
restaurants, and each conversation with a partner would be based on chance.

## Where the value is on real data

The platform is built to work on real data, where more of these signals would exist:

- **Trust:** the same contracts, rules and reconciliation apply to any source.
- **Personalization:** the audit and baselines would show whether real taste is present, and how
  much it's worth, before a costly model is built.
- **SLA and cancellations:** the marts and tests are ready to surface a real outlier if one exists.

## Cost

The whole platform runs free on a laptop with DuckDB. The cloud path costs cents a month on S3,
with Snowflake usage capped by a resource monitor and LLM spending capped in code.
[CLOUD_SETUP.md](CLOUD_SETUP.md) shows how the S3 cost is worked out, and
[METRICS.md](METRICS.md) shows what the LLM has cost so far.

## What this doesn't claim

There's no revenue or savings figure here, because nothing measured supports one. On synthetic
orders, any such number would be invented. The honest value claim is about capability: what the
platform checks, how it behaves when something goes wrong, and what it refuses to conclude.
