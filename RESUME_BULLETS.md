# Resume bullets

Every number under "Bullets" comes from [docs/METRICS.md](docs/METRICS.md), and a test fails if one
doesn't. If a metric changes, change the bullet. Pick the 3 or 4 that fit each application.

## Bullets

**Data engineering**

- Built a local Python, Parquet, DuckDB and dbt pipeline that moved 35,098,217 rows in 237.3 s;
  orders and reviews are synthetic, and the source dimensions appear scraped.
- Added 26 business rules alongside 66 dbt tests and 32 Great Expectations checks, flagging
  1,068,344 records and quarantining 273.
- Benchmarked incremental loads against a full refresh: fact models saved 89.9% of runtime and the
  whole project saved 40.1%, with reconciliation confirming identical results.

**Streaming**

- Built a SIMULATED replay of 218,691 events at 5,507 events/s; on held-out days it detected 100.0%
  of planted total outages in 16.0 simulated minutes, with 0.07 false alarms per day, while
  detecting only 7.1% of planted 30% outages.

**Machine learning**

- Compared ETS and LightGBM using walk-forward validation: ETS reached 0.64% MAPE against a 1.16%
  seasonal-naive baseline, close to the estimated 0.66% noise floor.
- Measured same-weekday anomaly detection at 85.4% recall on SYNTHETIC planted events, against
  7.8% for a plain rolling z-score.
- Audited recommendation signal with temporal and shuffled controls: matrix factorization reached
  0.00077 Recall@10 on real orders, 0.00039 on shuffled orders and 0.01542 on planted taste.

**GenAI**

- Evaluated document retrieval, reaching 0.737 MRR for hybrid search against 0.648 for dense search;
  the reranker improves Recall@5 but adds measured latency, so it is not the default.
- Built guarded text-to-SQL and a LangGraph router; golden evaluations measured 88.9% SQL execution
  accuracy and 96.8% routing accuracy against a 74.2% keyword baseline, while tests refused 92 SQL
  attacks.
- Measured 86.0% RAG faithfulness, then documented why evidence support is not the same as answer
  correctness.

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

**Don't claim these:**

- That results describe real customer behavior; the orders and reviews are synthetic.
- That RAG faithfulness means the answer is correct.
- That sentiment agreement on templated reviews establishes real-world sentiment accuracy.
- That the feed is live; it replays historical orders and injects SYNTHETIC outages.
- That the current validation executed Snowflake/AWS, Airflow scheduling or Neo4j/GDS; those runs
  are not run in this environment.
- That menu groups prove shared ownership or that a recommender identifies genuine preferences.
- Any revenue or savings figure; no such business impact was measured.
