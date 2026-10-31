# Slide outline

Six slides for a 10-minute walkthrough. The numbers come from [METRICS.md](METRICS.md), so check
them there before presenting.

## 1. What this is

**Title:** A data platform that knows what it can't conclude

- From raw CSV files to a warehouse, dashboards, forecasts, a knowledge graph and an AI copilot
- It runs for free on a laptop, and the same project runs on S3 and Snowflake
- Say early that the orders are synthetic, and that this shaped the results

**Visual:** the architecture diagram from ARCHITECTURE.md

## 2. Trustworthy data

**Title:** Bad data is counted, not lost

- Contracts stop breaking changes before anything loads
- 26 quality rules, tests and a quarantine make defects visible
- Repeating a day changes nothing, and fingerprints prove it

**Visual:** the Data Quality page

## 3. Two engines, one bug

**Title:** Passing tests didn't mean the numbers were right

- Every dbt test passed on DuckDB and Snowflake, yet 3 of 16 tables differed
- Snowflake rounded a rate twice: 0.0612 instead of 0.0611
- Comparing the two engines found what the tests couldn't

**Visual:** the 753 / 12,314 example, shown as the two results side by side

## 4. Knowing when a result is real

**Title:** Three findings that weren't

- Off-menu orders: missing data, not bad orders
- 9,791 dish pairs: confounded by restaurant, and none remain
- Cancel-heavy restaurants: chance, and none remain after correction
- What was real: chains and shared-kitchen brands, found from menus alone

**Visual:** the Knowledge Graph page

## 5. Simple models won

**Title:** The best model was often the simple one

- Forecasting: ETS reaches the noise floor, so LightGBM has nothing left to learn
- Recommendations: "15 times better than random" turned out to be popularity
- Search: hybrid ranks best, and the reranker didn't earn its latency

**Visual:** the table of real, shuffled and planted recommender results

## 6. AI with guardrails

**Title:** The model doesn't mark its own work

- The SQL guard refuses 92 attacks, and a read-only connection is the second layer
- Trying to break it found a real leak, which led to a view with no email at all
- Replay by default, with a hard budget cap
- Close with what would change on real data

**Visual:** the Copilot page, showing the checks under an answer
