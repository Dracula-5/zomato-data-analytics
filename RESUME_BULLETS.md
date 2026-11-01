# Resume bullets

Every number under "Bullets" comes from [docs/METRICS.md](docs/METRICS.md), and a test fails if one
doesn't. If a metric changes, change the bullet. Pick the 3 or 4 that fit each application.

## Bullets

**Data engineering**

- Built an end-to-end data platform in Python, dbt and Airflow that moves 35,098,217 rows from CSV
  through a Parquet lake to dashboards in 289.2 s on a laptop, with the same project running on
  Snowflake.
- Designed data governance with schema contracts, 26 quality rules, 66 dbt tests and 32 Great
  Expectations checks, flagging 1,068,344 records and quarantining 273.
- Made daily Airflow loads idempotent, proving that re-running a day left 21/21 tables identical
  and that day-by-day runs matched a full rebuild.

**Streaming**

- Built a SIMULATED live order feed with an event log, consumer offsets, a watermark for late events
  and Poisson alerting, finding 100.0% of total city outages at a median of 16.0 minutes with 0.07
  false alarms a day on held-out days, and showing that a 30% loss stays undetectable.

**Cloud**

- Deployed S3 and Snowflake with Terraform and a keyless IAM integration, loading 35,098,217 rows
  in 58.7 s and enforcing PII masking, with 0 readable emails for the analyst role.
- Compared Snowflake and DuckDB across 424 measures and found a rounding bug that every dbt test
  had passed, ending at 16 of 16 tables identical.

**Machine learning**

- Built walk-forward forecasts for orders and GMV, reaching 0.64% MAPE against 1.16% for the
  baseline, at the 0.66% noise floor, and showing that LightGBM couldn't improve on simpler ETS.
- Built anomaly detection with 85.4% recall on SYNTHETIC events, against 7.8% for a plain z-score.
- Showed through a signal audit and shuffled controls that a recommender's apparent lift was
  popularity: 0.00077 Recall@10 on real orders against 0.00039 on shuffled ones and 0.01542 on
  planted taste, which ruled out a costly graph neural network.

**Graph and statistics**

- Built a Neo4j knowledge graph of 1,014,522 nodes and 3,446,015 relationships. Community detection
  on menu overlap found 71 chains at 69.1% name purity against 18.4% by chance, plus 11 groups that
  look like virtual brands sharing kitchens.
- Used stratified tests and false discovery rate correction to reject false findings, taking 9,791
  apparent dish pairs to 0, and clearing all 148,103 restaurants tested for cancellations.

**GenAI**

- Built hybrid retrieval over project documents and measured each stage, reaching 0.737 MRR against
  0.648 for dense search, and dropped a reranker that lowered MRR and added seconds to every search.
- Built a LangGraph copilot with human approval and deterministic validation, plus a text-to-SQL
  guard that refuses 92 attacks, with a read-only connection as a second layer.
- Evaluated the AI on golden sets, reaching 88.9% text-to-SQL execution accuracy, 86.0% answer
  faithfulness and 96.8% routing accuracy against a 74.2% keyword baseline, and found that results
  written into a searched document were contaminating the test.

## How to talk about these

**Say early that the orders are synthetic.** It makes the rest more credible, and most of the good
stories depend on it. The strongest thing here isn't a score. It's that you caught results that
looked right and weren't.

**Good interview stories, best first:**

1. *"All my tests passed and the numbers were still wrong."* The double rounding between engines.
2. *"My first answer was wrong, and I said so."* The off-menu orders that were really missing data.
3. *"15 times better than random was just popularity."* Why the baseline decides what a result means.
4. *"I tried to break my own guard."* The whole-row query that would have leaked emails.
5. *"Writing up my results changed my results."* Why evaluation commentary can't live in the index.

**Don't claim these:**

- That 86.0% of answers are true. Faithfulness means supported by the retrieved passages, and a
  wrong answer can pass it when the wrong passages come back.
- A text-to-SQL score above 88.9%. Two of the four misses were correct answers, which is worth
  saying out loud, but 88.9% is the number.
- That 100% sentiment agreement means anything for real reviews. The texts are templates.
- That this ran on live data. The feed is a replay of past orders, and the outages are SYNTHETIC.
- That the shared-kitchen groups are proven. The data shows one menu under several names, and
  doesn't show who owns them.
- Any revenue or savings figure. Nothing measured supports one.
- That incremental models sped up the whole project. Only the fact models clearly got faster.
