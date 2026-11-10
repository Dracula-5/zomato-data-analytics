# Resume bullets

Every number under "Bullets" comes from [docs/METRICS.md](docs/METRICS.md), and a test fails if one
doesn't. If a metric changes, change the bullet. Pick the ones that fit each application.

**Title:** Zomato AI Data Platform – Data Engineering, Analytics & AI | Python, SQL, Snowflake, dbt,
Power BI

## Bullets

**Data platform**

- Built a CSV → Parquet lake → DuckDB → dbt pipeline, with S3, Snowflake and Airflow paths, that
  moved 35,098,217 rows end to end in 237.3 s on a laptop; orders and reviews are synthetic.
- Benchmarked incremental models against a full refresh: they cut fact-model runtime by 89.9% and
  whole-project runtime by 40.1%, with identical results; an aggregate mart answered 30-day
  city-GMV queries 2.7x faster than the fact table (25.2 ms against 9.2 ms).

**Data quality**

- Added 26 business rules alongside 66 dbt tests and 32 Great Expectations checks; on the full data
  they flagged 1,068,344 records and quarantined 273.

**Forecasting and anomalies**

- Compared ETS and LightGBM with walk-forward validation: ETS reached 0.64% MAPE on national
  orders against a 1.16% seasonal-naive baseline, close to the estimated 0.66% noise floor.
- Measured same-weekday anomaly detection at 85.4% recall on SYNTHETIC planted events, against
  7.8% for a plain rolling z-score.

**AI copilot**

- Evaluated hybrid retrieval over the project's documents at 0.852 Recall@5, against 0.685 for
  BM25; guarded text-to-SQL scored 88.9% execution accuracy and RAG answers 86.0% faithfulness on
  golden sets, with 625 tests passing at 92.20% line and branch coverage.
- Built a LangGraph router at 96.8% accuracy against a 74.2% keyword baseline; tests refused 92
  SQL attacks.

**Business intelligence**

- Built a two-page Power BI report on the dbt marts (star schema, DAX measures, a marked date
  table) tracking city GMV, orders and forecast against actual, for non-technical readers.
  (pending .pbix + screenshots)

**Streaming**

- Built a SIMULATED replay of 218,691 events at 5,507 events/s; on held-out days it detected 100.0%
  of planted total outages in 16.0 simulated minutes, with 0.07 false alarms per day, while
  detecting only 7.1% of planted 30% outages.

**Recommender audit**

- Audited recommendation signal with temporal and shuffled controls: matrix factorization reached
  0.00077 Recall@10 on real orders, 0.00039 on shuffled orders and 0.01542 on planted taste.

## Measured on 2026-10-30, not in METRICS.md yet

These ran in an earlier working copy. Their run files are gitignored and aren't in this one, so
METRICS.md shows them as "not run" and the test keeps them out of the bullets above. To quote
them, copy those run files into `artifacts/` and run `make metrics`.

- **Snowflake against DuckDB:** comparing the two engines caught a double-rounding bug that all 63
  dbt tests of the time missed ([ADR 0001](docs/adr/0001-one-dbt-project-two-warehouses.md)).
  After the fix, 16 of 16 tables matched across 424 measures (`artifacts/cloud/engine_compare_full.json`).
- **PII masking:** the analyst role could read 0 unmasked emails, against 100,000 for the
  deployer (`artifacts/cloud/access_check.json`).
- **Dish pairs:** a naive test found 9,791 dish pairs that survived FDR correction on the 5%
  sample. Testing within each restaurant, to remove the confounding, cut them to 0, and 0 of
  981,490 pairs on the full data. FDR correction is what cut 4,595 apparently cancel-heavy
  restaurants to 0 (`artifacts/graph_metrics.json`).

## How to talk about these

Say early that the orders and reviews are synthetic. The strongest stories are the cases where a
plausible score needed a control or an honest limitation:

1. **"Tests passed, but the data still needed rules."** dbt checks protect model invariants; business
   rules independently found records to flag or quarantine.
2. **"Fifteen times random wasn't evidence of taste."** A recommender looked strong until shuffled
   order controls and the near-zero repeat rate exposed the weak signal.
3. **"The larger model didn't win."** ETS beat LightGBM and approached the estimated noise floor;
   the simpler model was the defensible choice.
4. **"An outage detector can miss a meaningful loss."** Held-out replay found large outages but
   detected few 30% losses, so detection rates must be reported by severity.
5. **"Faithful doesn't mean true."** Retrieval can support an answer with the wrong evidence; the
   evaluation checks citations and reports that limitation instead of hiding it.
6. **"A dashboard number has to match the warehouse."** The Power BI export writes the totals and
   the forecast error to a manifest, and the report is checked against it before anyone reads it.

**Don't claim these:**

- That results describe real customer behavior; the orders and reviews are synthetic.
- That RAG faithfulness means the answer is correct.
- That sentiment agreement on templated reviews establishes real-world sentiment accuracy.
- That the feed is live; it replays historical orders and injects SYNTHETIC outages.
- Snowflake/AWS, Airflow or Neo4j/GDS results as part of the latest validation: they ran on
  2026-10-30 in an earlier working copy, and METRICS.md shows them as not run until their run
  files are restored (see above).
- That FDR correction removed the 9,791 dish pairs: they had already passed it. Testing within each
  restaurant removed them.
- The older figures from the 2026-10-31 runs (289.2 s end to end, 91.8% faster fact models, a 2.6x
  mart, 565 tests at 92.65% coverage): the 2026-11-02 runs replaced them.
- A finished Power BI report until the `.pbix` and its screenshots are committed.
- That menu groups prove shared ownership or that a recommender identifies genuine preferences.
- Any revenue or savings figure; no such business impact was measured.
