# Learning notes

Plain-language explanations of every major component, written so you can defend each
choice in an interview. One section per milestone, added as the project grows.

---

## M0: Project scaffold and engineering practice

### Why a Python package (`src/zap`) instead of loose scripts?

Loose scripts copy code into each other and are hard to test. A package is one importable
unit: `from zap.config import get_settings` works the same in tests, in the Airflow
container and in the Streamlit app.

The **src layout** (`src/zap/`, not `zap/` at the root) has one job. Tests import the
*installed* package, not whatever happens to sit in the current folder, so "works on my
machine" import bugs show up locally.

### uv and `uv.lock`

`uv` installs Python and the dependencies. `pyproject.toml` declares the *allowed* ranges,
such as `duckdb>=1.1`. `uv.lock` records the *exact* versions that were resolved.

- CI runs `uv sync --locked`, which fails if the lock file is out of date. That way CI tests
  exactly what you tested.
- **Dependency groups** (`dev` now; `dbt`, `ai` and others later) keep heavy libraries
  apart, so a change in one area doesn't pull a conflicting version into another.

*Interview line:* "Ranges in pyproject, exact pins in the lock file, and `--locked` in CI,
so builds are reproducible."

### Configuration (`config.py`)

There's one `Settings` class built on `pydantic-settings`. Each field is read from an
environment variable of the same name (`data_dir` comes from `DATA_DIR`), or from the
`.env` file.

- **Why env vars:** the same code runs locally, in Docker and in CI with different settings
  and no code changes. This is the "config in the environment" rule from the Twelve-Factor App.
- **Validation:** `sample_pct` must be between 0 and 100, and `target` must be `duckdb` or
  `snowflake`. A typo fails at start-up instead of halfway through a pipeline.
- **Secrets** are typed `SecretStr`, which prints as `**********`. `zap info` shows only
  whether a secret is set, never its value.
- `.env` is gitignored. `.env.example` documents every variable with empty values.

### The synthetic fixture (`fixtures.py`)

The real dataset is 1.4 GB and can't go on GitHub, but CI still has to run the pipeline. So
the code generates a small fake dataset (`make fixtures`) with the **same file names,
columns and quirks** as the real one:

- dimension files start with an unnamed pandas index column and use Windows CRLF line endings;
- fact files use LF line endings;
- restaurant fields look like the scraped source (`--` ratings, `₹ 200` costs, `Area,City`),
  and one quoted address contains a newline.

It's **deterministic**: the same seed always produces byte-identical files, and a test
checks this. Deterministic tests don't flake.

It also **injects known defects on purpose**: a duplicate order, a total that doesn't add
up, an order for a restaurant that doesn't exist, reviews dated before their order,
reviews pointing at the wrong restaurant, and so on. Because the fixture knows exactly how
many it injected, later data-quality code can be tested with "did it catch exactly these?"

*Interview line:* "CI never touches real data. It runs on a seeded synthetic fixture that
reproduces the real files' quirks and plants known defects, so the quality checks get a
precise pass/fail test."

### Tests, coverage and the 80% gate

- `pytest` runs the tests. `pytest-cov` measures which lines and branches ran.
- `fail_under = 80` in `pyproject.toml` makes the test run **fail** if coverage drops below
  80%, so the target is enforced, not aspirational.
- Coverage is measured on `src/zap` only. Any exclusions will be listed in METRICS.md.
- An autouse test fixture clears environment variables and changes into a temp folder, so
  your real `.env` can never leak into a test.

### Lint, format and type check

- **ruff** is both the linter and the formatter. It runs extra rule sets: `S` (bandit
  security checks such as SQL built from strings), `B` (likely bugs), `UP` (modern syntax).
- **mypy `--strict`** requires every function to be fully typed. It caught real mistakes in
  M0, for example a variable reused with two different types.
- `make check` runs all three. It's the same command CI runs.

### pre-commit hooks

These are git hooks that run before each commit:

- trailing whitespace, a final newline, valid YAML and TOML, no merge-conflict markers;
- a block on files over 800 KB, so data can't be committed by accident;
- private-key detection;
- **gitleaks**, which scans for API keys and tokens;
- ruff and mypy.

Ruff and mypy run from the project's own venv (`language: system`), so their versions match
`uv.lock` exactly.

### Makefile as a thin wrapper

Each target is one `uv run ...` line, so it behaves the same in PowerShell, Git Bash and on
Linux CI. You can always run the command by hand without `make`. `make help` lists every
target.

### CI (GitHub Actions)

On every push or PR, CI checks out the code with full history (gitleaks needs it),
installs the exact locked versions, runs the generic hooks and the secret scan, then runs
`make check`. Test reports are uploaded as build artifacts.

### Things that went wrong (and what they teach)

- **Windows console encoding:** printing a table with box-drawing characters crashed under
  cp1252. The fix is `PYTHONUTF8=1`, set in the Makefile and in CI.
- **DuckDB 2.0 API change:** `Relation.fetchone()` was removed, because relations now re-run
  on every fetch. The tests use `fetchall()` on relations, or `execute(...).fetchone()` on a
  connection. The lesson: pin versions with a lock file and read changelogs on major upgrades.

---

## M1: Ingestion, the lake, schema contracts and drift detection

### The flow

```
CSV files ──► drift check (contract) ──► typed Parquet in the lake ──► manifest + drift log
              stops on breaking drift     raw/<table>/...               artifacts/
```

### Schema contracts (`contracts/*.yml`)

A **contract** is a written agreement about what a source file looks like: its columns,
their types, a description of each, the primary key, how the table is partitioned and
sampled, and a **classification** for every column (`public`, `internal`, `pii`, `secret`).
It is kept in git, so every schema change is reviewed and versioned.

Each contract does three jobs: the drift check reads it, the lake writer uses it to pick and
cast columns, and later the data dictionary and PII masking come from it. It's a single
source of truth.

*Interview line:* "Contracts make the schema explicit and versioned. Producers can't change
it silently; the pipeline notices at the door."

### Schema drift: what counts as breaking?

| Change in the incoming file | Severity | Why |
|---|---|---|
| A contract column is missing or renamed | **breaking** | downstream models would fail or produce NULLs |
| A typed column has values that won't cast (e.g. `abc` in `order_id`) | **breaking** | silent NULLs would corrupt metrics |
| An extra, unknown column | warn | ignored safely, because columns are picked by name |
| Columns in a different order | warn | safe for the same reason |

The reference repo loaded columns **by position**, so a reordered file would silently put
values in the wrong columns. Here they are read **by name**.

The type check runs `try_cast` over every value in one DuckDB scan. On the real data that's
all 10M order rows, checked in about 8 seconds.

On breaking drift, **nothing is written**, even for tables that were fine. A half-updated
lake would be worse than a stale but consistent one. Every check is still logged to
`artifacts/governance/schema_drift_log.jsonl`, the audit trail.

### Facts are typed, dimensions stay as text

- **Facts** (orders, reviews, order_items) are machine-generated and clean, so the lake
  stores them strongly typed (BIGINT, DATE, DECIMAL for money). Typed Parquet is much smaller
  and faster.
- **Dimensions** are scraped display text (`₹ 200`, `--`, `Area,City`). The raw layer
  keeps them exactly as delivered, and cleaning happens in dbt staging, where bad values can
  be quarantined and counted. *Raw = what we received*, which makes it possible to replay.

### The lake layout

```
lake/<dataset>/raw/food/food.parquet
lake/<dataset>/raw/orders/order_date=2025-06-11/data_0.parquet
```

- `raw/<table>/` is the same layout the S3 bucket will use in M5.
- **Hive partitioning** by day (`order_date=...`) means a query or load for one day reads
  one folder, not 10M rows. It also mirrors daily batch arrival, which the Airflow DAG
  replays in M4.
- `order_items` has no date of its own, so it takes its parent order's date through a
  **LEFT JOIN**. With an inner join, a line whose order is missing would vanish silently.
  The LEFT JOIN keeps it, with an empty date, so quality checks can count it.
- **Lineage columns** on every row: `_batch_id` (which run), `_source_file`, `_loaded_at`.
  `_loaded_at` is what source-freshness checks use, because business dates in this static
  dataset stop at 2026-06-30.

### Idempotency: safe to run twice

Each table is written to a temporary folder, its row count is checked against the source,
and only then does it replace the old folder. Running ingest twice gives the same lake, not
double the rows. If anything fails, the temp folder is deleted and the old data is untouched.

### Sampling by user, with a portable hash

`make local` uses a 5% sample. Rows aren't sampled at random. **Users** are:
`bucket = (user_id × 2654435761) mod 2³² mod 10000`, and a user is kept if `bucket < 500`.

- A sampled user keeps their **whole** order history, which the graph and recommender
  work needs.
- The arithmetic gives the same answer in Python, DuckDB and Snowflake, and a test proves
  Python and SQL agree. Built-in `hash()` functions can change between versions.
- Samples are **nested**: the 1% sample is a subset of the 5% sample.
- Reviews and order lines are kept only when their order is in the sample, so the sample
  has no orphans by construction.

### Real results (from `artifacts/ingest/*/manifest.json`, 2026-10-30)

| Dataset | Rows written | Lake size | Time |
|---|---|---|---|
| full (4 of 7 files present) | 10,820,102 | 276.6 MB (from 1.42 GB of CSV) | 52.1 s |
| 5% sample | 1,023,172 (488,455 orders from 4,993 users) | 38.0 MB | 23.5 s |

Dimensions are kept whole in the sample, which is why it holds 1.0M rows, not 5% of 10.8M.

### Gotchas met in M1

- DuckDB names the pandas index column `column0` in one file and `column00` in another,
  depending on how many columns there are. The code never relies on that name; it selects
  contract columns by name.
- A docstring containing `"""` ended the docstring early. Small bug, but a reminder that
  tests run on every change for a reason.

---

## M2: The warehouse, dbt, data quality and governance

### The layers (medallion architecture)

```
raw (bronze)        staging (silver)            marts (gold)                   governance
-------------       -----------------           ----------------------         -------------
7 source tables --> 7 cleaned views --+------> dims + incremental facts --> dq_results
as delivered        typed, renamed,   |         business marts               dq_quarantine
                    de-duplicated     +> int_dq_violations (every rule) --> gx_results,
                                                                            dbt_test_results
snapshots: snap_restaurants (SCD2 history)
```

- **raw**: exactly what arrived (loaded from the lake). Never edited.
- **staging**: one view per source. It cleans and types the data and renames columns
  (`r_id` becomes `restaurant_id`), and that's all: no joins, no business logic.
- **marts**: what people query. Dimensions (who, what, where), facts (events) and business
  marts (pre-aggregated answers).
- **governance**: data about the data, meaning which records broke which rule.

### Why dbt?

dbt turns SQL `select` statements into tables and views, in dependency order (`ref()`
builds the lineage graph), with tests and docs alongside. Transformations become version
controlled, testable and documented, like application code.

### Dimensions, facts and natural keys

- **Facts** are events: `fct_orders` has one row per order with measures such as amount
  and delivery time. **Dimensions** describe the who, what and where: restaurants,
  customers, food, dates, cities.
- This project uses **natural keys** (the source's `restaurant_id`) instead of surrogate
  keys. The source ids are stable and unique, and that keeps the SQL readable. With
  multiple source systems you would add surrogate keys.

### Incremental facts with MERGE and a lookback window

On each run, `fct_orders` reprocesses only the last `lookback_days` (3) of order dates and
**MERGEs** them on `order_id`: matching rows are updated and new rows inserted.

- **Why a lookback window?** Orders from yesterday can arrive late or be corrected today.
  The reference repo used `order_timestamp > max(...)`, which silently misses them.
- **What I measured:** the first version saved only **2.3%** on the fact models. Two
  things prevented DuckDB from skipping old data:
  1. The staging de-duplication window was `partition by order_id`. A date filter can't be
     pushed below a window unless it filters on a partition column, so I changed it to
     `partition by order_date, order_id`. Exact duplicates share a date, so the result is
     the same.
  2. The cutoff was a scalar subquery (`select max(order_date) from this`). The optimiser
     treats that as unknown until runtime. The `incremental_cutoff` macro computes it when
     the model compiles, so the SQL contains a literal date.
- **After the fix** (median of 3 runs, full 10M rows, one new day added): fact models went
  from 7.56 s to 1.53 s (**79.7% saved**); the whole project from 17.36 s to 12.51 s
  (**28.0%**). The project figure is lower because the marts and the 23 quality rules are
  still rebuilt in full every run. The benchmark also checks that the incremental result
  has exactly the rows a full refresh would.

*Interview line:* "My first incremental model wasn't really incremental. The benchmark
showed 2% savings, I traced it to predicate pushdown being blocked, fixed it, and it went
to 80%. Always measure: 'incremental' in the config doesn't mean incremental in practice."

### SCD Type 2 snapshot

`snap_restaurants` keeps history. When a restaurant's rating, cost, name, cuisines or
location changes, dbt closes the old row (`dbt_valid_to` set) and opens a new current row.
Analysts can then see what a restaurant looked like when an order was placed.
`dim_restaurant` exposes only the current rows.

The source data is static, so there are no real changes to observe. A test simulates one:
it edits two ratings in a copy of the warehouse, re-runs `dbt snapshot`, and checks that
exactly two rows were closed and two new current rows opened.

### Data quality, three layers deep

| Layer | Tool | Runs | Checks |
|---|---|---|---|
| 1. Schema gate | contracts + drift check | at ingestion | columns and types (M1) |
| 2. Value gate | Great Expectations | before loading | each file: keys, allowed values, ranges |
| 3. Warehouse | dbt tests + rule catalogue | after the build | relationships, reconciliation, business rules |

- **Severity.** A failed `error` stops the pipeline (unknown order status, negative
  money). A failed `warn` is logged and the pipeline continues (duplicates, which staging
  removes and the rules count).
- **Rule catalogue** (`seeds/dq_rules.csv`): 23 rules, each with a severity and an action.
  - `quarantine`: excluded from facts, kept in `dq_quarantine` with the reason.
  - `fix`: corrected by a documented rule. Example: reviews whose restaurant disagrees
    with their order take the order's restaurant.
  - `flag`: kept, marked and counted.
- **Reconciliation tests:** every source order is in the fact table or in quarantine
  (nothing lost, nothing invented), and GMV is identical whether computed from the fact,
  the city mart or the national mart.
- **dbt unit tests** check SQL logic on hand-made input rows: `1,200` with a rupee sign
  becomes 1200, `Sector 5,Salt Lake,Kolkata` becomes city Kolkata, an email becomes
  `r***@example.com`.

**What it caught on the real data (full 10M orders, 2026-10-30):** 9 of 23 rules fired,
flagging 885,016 records. Every review points at a different restaurant than its order
(300,000) and almost every one at a different user (299,998); 172,401 reviews are dated
before their order; 25,380 are on cancelled or refunded orders. No records were
quarantined: the order facts themselves are internally consistent.

### PII governance

- Each source column is classified in its contract (`public`, `internal`, `pii`, `secret`).
- The plain-text **password** (`secret`) is never selected in staging, so no model can
  ever expose it. A test scans every schema to prove it.
- **Emails** reach the marts only masked (`r***@example.com`) and as a **salted SHA-256
  hash**, which still works as a join key. The salt (`PII_HASH_SALT`) stops anyone hashing
  a list of known emails and matching them.
- A test scans every text column in the marts for a raw email and must find none.

### Model contracts

`mart_city_daily` and `mart_daily_kpis` have **enforced contracts**: dbt refuses to build
them unless the column names and types match the YAML. That's a promise to the dashboard
and the forecasting code that the shape won't change silently.

### Source freshness

Freshness is checked on `_loaded_at` (ingestion time): warn after 26 hours, error after 50.
Business dates in this static dataset end in June 2026, so a freshness check on
`order_date` would always fail and tell you nothing.

### Aggregate marts: are they worth it?

The same question answered from the 10M-row fact table and from the pre-aggregated mart
(median of 7 runs, answers verified identical):

| Question | Fact table | Mart | Speed-up |
|---|---|---|---|
| GMV by city, last 30 days | 75.6 ms | 39.1 ms | 1.9x |
| National GMV by month | 70.6 ms | 7.0 ms | 10.1x |

DuckDB is already fast on 10M rows, so the absolute gains are small locally. The point
of a mart is that a dashboard reads a few thousand rows instead of millions, which matters
more as data grows and on a warehouse that bills per second of compute (Snowflake, M5).

### Portability (DuckDB today, Snowflake in M5)

Where the SQL differs, a macro uses `adapter.dispatch`. For example, `regexp_replace` needs
a `'g'` flag in DuckDB but not in Snowflake, and the last element of a split string is
`string_split(x, ',')[-1]` in DuckDB but `split_part(x, ',', -1)` in Snowflake. The models
themselves stay the same.

### Gotchas met in M2

- **GX + DuckDB 2.0:** Great Expectations' SQL engine failed (`list index out of range`)
  through the duckdb-engine SQLAlchemy driver. Instead, DuckDB reads only the needed
  columns into pandas and GX validates that. It's fast enough for the 5% sample (6.5 s)
  and for a single daily partition.
- **`uv remove` re-synced only the default dependency group** and uninstalled dbt and GX.
  Fixed by making every group a default in `pyproject.toml`.
- **dbt start-up on Windows** costs about 20-30 s per invocation, which is why wall times
  are much longer than dbt's own `elapsed_time`. Benchmarks report both.

---

## M3: The Streamlit dashboard

### Structure: thin pages, testable data layer

```
app/Home.py, app/pages/*.py      what to draw (thin; no SQL)
src/zap/dashboard/data.py        every query, as plain functions returning DataFrames
src/zap/dashboard/theme.py       palette + one `style()` for every chart
src/zap/dashboard/ui.py          dataset picker, caching, "show as table"
```

Keeping SQL out of the page files means each query can be unit-tested against the fixture
warehouse. Every page is also smoke-tested headlessly with Streamlit's `AppTest`, which
runs the page script in-process and fails on any exception.

### Reading DuckDB safely from a dashboard

- **Read-only, short-lived connections.** DuckDB allows one writing *process* at a time. If
  the dashboard held a connection open, a `dbt build` would fail to get its lock. So each
  query opens a read-only connection and closes it straight away.
- **Caching that knows when data changed.** Results are cached with `st.cache_data`, and
  the cache key includes the warehouse file's modification time, so a rebuild invalidates
  it automatically.
- **Replacement scans off.** If a table is missing, DuckDB would otherwise look for a Python
  variable with that name in the calling code (`gx_results` matched a *function*). Turning
  this off means a missing table is a clear "not found" error, and queries can never read
  a Python object by accident.

### Chart design choices (and why)

- **One job per colour.** Each chart uses a single series colour. The heatmaps use one
  blue ramp (light means few, dark means many). There is no rainbow, because hue doesn't
  read as "more".
- **No dual axes.** Orders and GMV sit in two side-by-side charts. Two y-scales on one chart
  invite false conclusions about where the lines cross.
- **Validated palette.** The colours come from a palette checked for colour-blind
  separation and contrast in light and dark mode. One light-mode colour is below 3:1
  contrast, so every chart has a "show the data as a table" view. That is also an
  accessibility feature for screen readers.
- **Hover tooltips** carry exact, formatted values (₹ amounts, percentages), so the chart
  stays clean.
- **Honest captions.** The Delivery SLA page prints the actual spread across all city-hour
  cells and states that this synthetic dataset has no SLA hot-spot. A dashboard that
  quietly shows a flat heatmap invites someone to invent a story.

### Security

By default Streamlit listens on every network interface: the first run printed an
"External URL" with the machine's public IP. `.streamlit/config.toml` binds it to
`localhost` and turns off usage telemetry.

---

## M2b: When the last three files arrived (users, menu, order_items)

This is a small case study in why the safeguards exist.

1. **The drift gate blocked `users.csv`.** The contract had been written from the reference
   repo's DDL (`age`, `marital_status`, ...), but the real file says `Age`, `Marital Status`,
   `Educational Qualifications`. The check reported it as *breaking* and nothing was loaded.
   The contract now matches the real file, and staging renames the columns to snake_case.
   A positional `COPY INTO`, as in the reference, would have loaded it silently.
2. **Profiling corrected a wrong assumption about the key.** `menu_id` looked like the key of
   `menu.csv`, but there are 1,179,936 rows and only 1,048,575 distinct ids. That is exactly
   Excel's row limit minus one, which suggests (not proven) that the file once went through
   Excel. The real key is `(menu_id, f_id)`. A `unique` test on `menu_id` would have failed
   for the wrong reason.
3. **Root cause before quarantine.** 49,065 order lines have `line_amount` ≠
   `price × quantity`. Quarantining them blindly would have thrown away valid data. The
   differences are always ₹1–3, and every order's subtotal equals the sum of its line
   amounts **exactly** (0 mismatches across 10M orders). So `price` is a rounded display
   value. The rule was split: within ₹1 per unit it's `ITM_PRICE_ROUNDED` (info, flagged);
   anything beyond is `ITM_LINE_AMOUNT_MISMATCH` (quarantined). On real data that's 49,065
   flagged and 0 quarantined.
4. **A performance cliff that only full-size data could show.** With 23M order lines the dbt
   build went from 39 s to **441 s**, and one run failed with *out of memory*.
   - Cause: `int_dq_violations` was a **view** read by two tables. dbt built both in
     parallel, so all 26 rules over 33M rows ran **twice at once**. Inside, the CTE over the
     de-duplicated orders was referenced about ten times and could be re-computed each time.
   - Fix: make it a **table** (rules run once), mark the heavy CTEs `MATERIALIZED` (DuckDB
     syntax, emitted through a dispatch macro), and set `memory_limit` so DuckDB spills to
     disk instead of failing.
   - Result: the full build takes **71.5 s** (6.2× faster), and the rules step takes 22.8 s.

New rules came from profiling: `MENU_DUPLICATE_ITEM` (118,459 extra listings),
`USR_DUPLICATE_EMAIL` (14,338 users share an email), and `MENU_ORPHAN_RESTAURANT` (273
menu lines for restaurants that don't exist, quarantined). Cost-for-two text such as
"₹200 FOR TWO" turned up in the menu price column 1,193 times.

*Interview line:* "Every assumption I made from documentation was wrong in at least one
place once the real files arrived. The contracts and profiling caught all of them before
they reached a dashboard."

---

## M4: Orchestration with Airflow 3 (in Docker)

### What runs where

```
airflow/docker-compose.yml
  postgres          Airflow's own metadata (runs, task states). Not our data.
  airflow-apiserver UI + REST API + the Execution API that tasks talk to (127.0.0.1 only)
  airflow-scheduler decides what runs next; LocalExecutor runs the tasks
  dag-processor     parses DAG files (a separate service since Airflow 3)
  triggerer         deferrable tasks and async callbacks
```

- **Custom image:** Airflow 3.3.2, plus the project's tools in a **separate virtualenv**
  (`/opt/zap-venv`) installed from `uv.lock`. dbt, DuckDB and Great Expectations never
  share packages with Airflow itself, and the container runs exactly the versions CI tests.
- **The code is mounted, not copied:** the repo is at `/opt/project`, and the CSVs are
  mounted read-only at `/data`. A code fix is live in the next task run, no rebuild needed.
- Every task is a `BashOperator` calling the same `zap` CLI used locally. Airflow only
  orchestrates, so all the logic stays unit-tested outside Airflow.

### One day per run

The source is a static dump, so to exercise a *daily* pipeline the history is split by
date. `make airflow-bootstrap` loads everything up to 2026-06-23 and builds the warehouse
once. The DAG then replays one day per run (`{{ ds }}` = the run's logical date):

| Task | What it does |
|---|---|
| drift_check | source headers and types vs the contracts; stops on breaking drift |
| extract_partition | that day's facts into the lake (the day's folder is replaced) |
| gx_validate | Great Expectations on that day only; stops on error-level failures |
| load_raw | `DELETE` the day + `INSERT` it again, in one transaction |
| dbt_snapshot | SCD2 history of restaurants |
| dbt_build | incremental MERGE of the facts (3-day lookback), marts, every dbt test |
| quality_report | rules + GX + dbt test results into the governance tables |

`max_active_runs=1` because DuckDB allows one writer at a time. Past days are replayed
with **`airflow backfill create`**, which runs through the real scheduler, one day after
another.

### Idempotency, and how it is proven

Every step *replaces* data and never appends, so a retry or a manual re-run can't
duplicate anything:

- lake: the day's partition folder is written to a temp folder, then swapped in;
- warehouse: delete-then-insert of that day in one transaction;
- dbt: `MERGE` on the primary key; the snapshot only adds a row when a value changed.

"Should be idempotent" isn't evidence, so there are three checks:

1. `tests/test_daily_replay.py` (SYNTHETIC fixture, runs in CI): bootstrap, replay two
   days, replay the last day again → every mart identical; then a full rebuild →
   identical again. *Incremental day by day = full rebuild.* It also plants a review
   dated before its order, across the cutoff (see bug 2 below).
2. `zap fingerprint`: the row count and an order-independent checksum of every table,
   skipping columns that only record *when* something ran (`_loaded_at`, `dbt_*`,
   `valid_from`/`valid_to`). Floats are rounded to 6 decimals, because a parallel SUM can
   add the same numbers in a different order. Its own tests prove it catches a changed
   value, a NULL, a duplicate row, a deleted row and a new table.
3. On the real 5% sample: fingerprint after the 7-day backfill, re-run one day through
   the scheduler, fingerprint again (results below).

### Results (from `artifacts/airflow/verification.json`, 2026-10-30)

SAMPLED data: 5% of users, from the synthetic-looking source (see M2b).

| Check | Result |
|---|---|
| 7 daily runs (2026-06-24 to 06-30) through the scheduler | 7/7 succeeded, no retries, 84–92 s per run* |
| Re-run 2026-06-27 through the scheduler | 21/21 tables identical (raw, marts, snapshot) |
| Day-by-day raw layer vs one ingest of the same sample | 7/7 tables identical |
| Day-by-day warehouse vs a full dbt rebuild | 21/21 tables identical |
| `alerts_smoke` | failure alert and deadline alert both recorded, with DAG and run ids |

\* Airflow's start and end times. The Docker VM clock was being stepped by up to ~13 s
(see bug 4), so treat these as approximate.

After the backfill the warehouse holds 488,455 orders, 1,124,385 order lines and 14,615
reviews. These figures were only reached after fixing the first two bugs below: before
that, the day-by-day result was 160 reviews short of a one-shot load, and then 159 short
of a full rebuild.

### Reliability features

- 2 retries with exponential backoff, a timeout on every task, and `on_failure_callback`
  once retries are used up.
- **Deadline Alerts** replace SLAs, which Airflow 3 removed: if a run isn't finished 30
  minutes after it was queued, a callback fires. (Still marked experimental in 3.3.)
- Alerts go to `artifacts/airflow/alerts.jsonl`, and to a webhook if `ALERT_WEBHOOK_URL`
  is set (https only; a webhook outage never fails the pipeline).
- `alerts_smoke` (`make airflow-smoke`) proves both callbacks on the real scheduler: one
  task fails on purpose, and another sleeps past a 20-second deadline.

### What went wrong (and what each one teaches)

1. **The backfill comparison found a real bug.** The Airflow-built raw layer matched a
   one-shot 5% ingest table for table, except for **160 reviews**. Root cause: in a sample, a
   review was kept only if its order was *already in the lake*. Some reviews are dated
   **before** their order (a known defect in this data). If the order falls after the
   bootstrap cutoff, the review was skipped at bootstrap, and it was never picked up on a
   later day because its own date had already passed. Fix: decide membership from the
   *source* orders file (by the order's user), which doesn't depend on load progress. A
   regression test recreates the exact case. Full-data runs were never affected (no
   sampling).
2. **The same bug one layer up, in dbt, and this one also affects full data.** With
   the raw layer fixed, the marts built day by day still differed from a full rebuild:
   `fct_reviews` was 159 rows short. `fct_reviews` keeps a review only if its order is in
   `fct_orders`, and incremental runs only re-check *recently dated* reviews. So a review
   dated before its order was dropped at bootstrap (no order yet) and never re-checked when
   the order arrived. Fix: incremental runs also re-check reviews whose **order** is in the
   new window. This is the classic *late-arriving parent* problem: the incremental filter
   must cover every way a row can become valid, not just its own date. The replay test now
   plants exactly this case. It failed before the fix, and the fixture had simply never
   contained it.
3. **A quiet day would have crashed the DAG.** A partitioned `COPY` of zero rows writes
   no file, and the row count then failed on an empty folder. Real days always had rows,
   so only the sampled fixture showed it. The count is now guarded.
4. **`403 Invalid auth token`, on and off.** The API server rejected task tokens as "not
   yet valid (iat)". Docker Desktop's WSL 2 clock was 12.6 s behind Windows and was being
   *stepped backwards* every few seconds (`dmesg`: "Time jumped backwards"), beyond JWT's
   10 s leeway. Retries recovered every run; the fix is `AIRFLOW__API_AUTH__JWT_LEEWAY=60`
   in the local compose file. *Retries are not just for flaky networks.*
5. **`airflow dags test` crashes with a Deadline Alert** (after all tasks succeed): it
   passes the alert as a dict where the scheduler expects a stored id. It's an Airflow bug
   in an experimental feature, so replays use `backfill create`, the real scheduler path.
6. **The deadline callback got JSON, not objects.** Airflow 3.3 sends
   `{"dag_run": <REST API dict>, "deadline": {...}}`, and calls the run id `dag_run_id`.
   The first smoke run logged `?` for every field; the callback now reads both shapes, and
   the test uses the real shape copied from Airflow's source.
7. **A CI check that could never pass.** Airflow 3 lists DAGs from its *database*, so in a
   fresh container `dags list` is empty and "no import errors" proves nothing. The check
   (`airflow/check_dags.sh`) runs `dags reserialize` first, and was itself tested against
   a deliberately broken DAG.
8. **Shared dbt `target/` folder.** Local runs and Airflow share the checkout, so they
   overwrote each other's `run_results.json`. dbt now writes to `target/<dataset>/`.
9. **Port 8080 was taken** by another program on this laptop, so the host port is
   configurable (`AIRFLOW_HOST_PORT`).
10. **Disk.** Docker's virtual disk grew to 44.7 GB and never shrinks by itself (C: fell to
   258 MB free). Fix: prune, `fstrim`, then compact the VHDX (needs admin).

*Interview line:* "I didn't trust 'it's idempotent'. I fingerprinted the warehouse, re-ran
a day and compared, and checked day-by-day loads against a one-shot load and a full
rebuild. Those comparisons found two late-arriving-data bugs (one in sampling, one in
an incremental dbt model) that no unit test had caught. Each now has a regression test."

## M5: The cloud path (Terraform, S3, Snowflake, dbt, Airflow)

### What runs where

```
lake/ (Parquet) --boto3--> S3 --COPY INTO--> Snowflake RAW --dbt--> STAGING, MARTS, GOVERNANCE
```

The same contracts and the same dbt project drive both engines. Only `macros/cross_db.sql`
knows they differ. `src/zap/cloud/` holds the cloud code and imports its SDKs inside each
command, so `make local` needs no cloud packages or credentials.

### Keyless access, and why Terraform runs twice

Snowflake reads S3 by assuming an IAM role, so no AWS key is stored in Snowflake. There's a
chicken-and-egg problem: the storage integration needs the role's ARN, but the role's trust
policy needs a user and external ID that Snowflake generates only when the integration is
created. So: Terraform creates the role with a placeholder trust, `zap cloud setup` creates
the integration and saves its two values, and Terraform runs again to narrow the trust.
The integration uses `IF NOT EXISTS` because re-creating it changes those values.

### Least privilege, three times

- **AWS:** `zomato-terraform` can touch only `zomato-ai-dp-*` buckets and `zomato-snowflake-*`
  roles. Terraform refuses any account but the one in `terraform.tfvars`. Every tool names
  the `zomato` profile, so stray `AWS_*` keys in the environment are ignored.
- **Snowflake user:** `ZAP_SVC` is `TYPE = SERVICE`: key pair only, no password, no web UI.
- **Snowflake grants:** future grants normally need the account-wide `MANAGE GRANTS`. Managed
  access schemas let the schema owner set them instead. It must be set at `CREATE`: turning
  it on later needs `MANAGE GRANTS` too, which is how this was found.

### PII: what each layer does

| Layer | Protects against |
|---|---|
| `secret` columns dropped at ingestion | passwords reaching the lake, S3 or any warehouse |
| masked email and salted hash in marts | raw emails in analytics tables |
| masking policy (Enterprise) | analysts seeing even those two columns |
| `ZAP_ANALYST` has no grant on `RAW` or `STAGING` | reading raw PII at all |

dbt rebuilds tables, which removes a masking policy, so a post-hook re-applies it each build.

### Results (from `artifacts/cloud/` and `artifacts/bench/snowflake_full/`, 2026-10-30 UTC)

Full data: all 7 source files, unsampled, from the synthetic-looking source (see M2b).
X-Small warehouse.

| Check | Result |
|---|---|
| S3 lake | 2,740 files, 0.533 GB (a further 2,740 files, 0.061 GB, for the 5% sample) |
| `COPY INTO` all 7 tables | 35,098,217 rows in 58.7 s |
| `dbt build --full-refresh` | 88/88 passed in 44.9 s (DuckDB on this laptop: 88/88 in 93.7 s) |
| Snowflake vs DuckDB | 16/16 tables identical across 424 measures, tolerance 1e-9 |
| Analyst role | 7/7 checks passed; readable emails: deployer 100,000 rows, analyst 0 |
| Airflow, 2 days through the scheduler | 2/2 runs, 14/14 tasks on the first try, about 345 s each* |
| Files sent to S3 by that replay | 3 of 2,740 |
| Snowflake vs DuckDB after the replay | still 16/16 |

\* Docker VM clock caveat as in M4: treat as approximate.

The replay re-processed days already in the warehouse, so "still 16/16" is the idempotency
proof on Snowflake.

**Credits used: not measured.** `ZAP_DEPLOYER` can't read metering views or resource monitors.
Snowsight shows them to ACCOUNTADMIN under Admin → Cost Management.

### Two benchmarks that didn't flatter the project

**Incremental vs full refresh (median of 3 cycles, 43,420 new rows):**

| | Full refresh | Incremental | Saved |
|---|---|---|---|
| whole `dbt run` | 31.3 s | 29.0 s | 7.3% |
| the 3 fact models | 23.1 s | 18.8 s | 18.9% |

Fact totals were identical after all 3 cycles. The saving is small for two measured reasons:
the fact models are only about 19 s of the run, because every other model rebuilds either
way, and an X-Small warehouse rebuilds 10M rows in about 23 s anyway. Incremental models pay
off when the rebuild is the slow part, and on this data it isn't.

**Clustering (same rows, result cache off, median of 5):**

| Layout | Partitions scanned | Median time |
|---|---|---|
| as built by dbt | 26 of 26 | 262 ms |
| shuffled | 24 of 24 | 303 ms |
| sorted by `order_date` | 2 of 21 | 263 ms |

Sorting cut the partitions scanned by 90% and saved no measurable time: at this size the scan
is too small to matter next to the rest of the query. **Decision: no `cluster_by`.** Automatic
clustering costs credits for a gain that doesn't show here. It's worth revisiting if the table
grows by an order of magnitude. The times include the network round trip from this laptop.

### What went wrong (and what each one teaches)

1. **Comparing two engines found a real numbers bug that 88 passing tests didn't.** Both
   builds were green, but 3 of 16 tables differed. On 2025-01-19, 753 of 12,314 orders were
   cancelled: 0.06114991, which is 0.0611. Snowflake said 0.0612. `NUMBER` division kept 6
   decimals (0.061150), and rounding that again went the wrong way, so it **rounded twice**.
   Fix: `ratio`, `round_avg` and `round_double` in `cross_db.sql` divide and average in
   `DOUBLE` and round in `DECIMAL`, and every rounded column uses them. A singular dbt test
   runs on both engines. *Green tests prove your rules hold, not that your numbers are right.*
2. **My first comparison was too loose to see all of it.** A tolerance of 1e-6 on a large sum
   hides many rows each off by 0.0001, and opposite errors cancel in a plain sum. It's now
   1e-9, with a sum of squares added for each numeric column.
3. **The PII salt never reached dbt.** The macro fell back to the public `local-dev-salt`
   because nothing passed `.env` to dbt, which doesn't read that file. A hash with a known
   salt can be reversed with a list of emails. Settings now hold it as a secret, `dbt_env`
   passes it on, and `zap cloud dbt` refuses to run with the fallback.
4. **A role test can pass for the wrong reason, in two ways.** Snowflake's default
   `DEFAULT_SECONDARY_ROLES = ALL` leaves other roles active after `USE ROLE`, so the check
   sets `USE SECONDARY ROLES NONE`. And "does not exist" looks the same as "not authorized",
   so a typo would count as denied. Every statement runs as the deployer first, and only
   authorization errors count as denied.
5. **Going from sample to full needs `--full-refresh`.** Incremental models held sample rows,
   and a normal run merges only recent days on top. The snapshot was safe because restaurants
   aren't sampled: its row count was the same on both datasets.

## M6: Forecasting and anomaly detection

### Look at the data before choosing a model

912 days with none missing, and three facts that decided everything else:

- **Steady growth:** about 6,100 orders a day in January 2024, 15,500 in June 2026.
- **A fixed weekly shape:** Saturday is 60% above Tuesday, with the same shares every year.
- **Nothing else:** no holiday effect (Diwali and New Year look normal), no month-of-year
  pattern, and no day more than about 9% from its weekday's recent median.

So the series is growth × weekday × noise. That's why this data can't reward a clever model,
and it's a sign of generated data: real food delivery has festivals and rainy days.

### Three models, simplest first

| Model | Idea | Why it's here |
|---|---|---|
| Seasonal naive | repeat last week | the baseline; a model that can't beat it is useless |
| ETS (Holt-Winters) | damped trend × weekly pattern | built for exactly this structure |
| LightGBM | one model across all series | can learn things ETS can't, if there's anything to learn |

**Trees can't extrapolate.** A tree predicts an average of values it saw in training, so on a
growing series it can never predict a new high. So LightGBM never sees raw volumes: it predicts
`log(target / recent 28-day level)`, and the level carries the growth. A test checks that its
forecast exceeds anything in its training data.

**Direct forecasting, no feedback.** Each row means "standing at this day, predict N days
ahead". A recursive model would feed its own predictions back in, so errors would compound.

### Walk-forward backtest, and how leakage is ruled out

8 folds × 14 days, covering 2026-03-11 to 2026-06-30. Each fold cuts the history before any
model runs. Two tests guard this: multiplying every value after the cut by 50 must change no
forecast, for all three models, and `direct_rows` only ever shifts data backwards. Model
settings were fixed in advance, not tuned on these folds.

### Results (from `artifacts/forecast_backtest.json`, 2026-10-30 UTC)

Full data, from the synthetic-looking source (see M2b). 22 series: national and the top 10
cities, for orders and GMV. MAPE, lower is better; sMAPE is within 0.1 of it throughout.

| Orders | National MAPE | Mean city MAPE | Gain over baseline, all series |
|---|---|---|---|
| Seasonal naive | 1.16% | 4.43% | baseline |
| **ETS** | **0.64%** | **3.21%** | **28.1%** |
| LightGBM | 0.70% | 3.29% | 26.0% |
| *Poisson noise floor* | *0.66%* | *3.05%* | |

| GMV | National MAPE | Mean city MAPE | Gain over baseline, all series |
|---|---|---|---|
| Seasonal naive | 1.40% | 6.38% | baseline |
| **ETS** | **0.96%** | **4.56%** | **28.5%** |
| LightGBM | 1.00% | 4.64% | 27.2% |

ETS beat the baseline in 8 of 8 national folds and 75 of 80 city folds on orders, and 76 of
80 on GMV. 4 of the ETS fits raised a convergence warning.

**The noise floor is the most useful number here.** If a day's orders are Poisson, even a
perfect forecast has a MAPE of about `sqrt(2 / (pi × mean))`. ETS sits on that floor, so it
has taken out all the signal there is. (0.64% below 0.66% isn't a miracle: the floor is an
approximation, and 112 test days is a small sample.) So LightGBM can't win here, however well
it's tuned, and more tuning would only fit noise. **The simple model is the right choice**,
and that's a better interview answer than "I used gradient boosting".

Repeating last week has a bias of −0.84% nationally, because it's always a week behind
the growth. ETS removes most of that, at −0.22%.

### Anomaly detection

The data has no labelled incidents, so real days can't be scored. Instead, events are injected
into a copy of each series (never the warehouse): spikes and drops of 20, 30 and 50%, and a
3-day 40% outage. Each series gets every event twice, for 20 seeds, which makes 7,920 injected
days and 341,000 normal days. Thresholds are the textbook values, fixed before any result.

Each day is compared with the median of the same weekday over the previous 8 weeks. The size of
that gap is then scored with a median-based z-score over the previous 56 days. Medians matter
because an earlier anomaly would inflate a mean and a standard deviation and hide the next one.
Only earlier days are used, so it could run live, and a test proves that.

| SYNTHETIC injected events | Same-weekday robust z | Plain rolling z |
|---|---|---|
| Recall | **85.4%** | 7.8% |
| Precision | 90.5% | 89.3% |
| F1 | 0.879 | 0.143 |
| False alarms per 1,000 normal days | 2.08 | 0.22 |

The plain z-score finds almost nothing, and no 20% or 30% drop at all, because the weekly swing
inflates its standard deviation. On quiet weekdays a real drop still looks normal.

| Recall by event | | Recall by series | |
|---|---|---|---|
| 50% drop | 99.9% | National | 100% |
| 3-day 40% outage | 97.3% | Bangalore (largest city) | 96.9% |
| 50% spike | 93.5% | Mumbai | 80.0% |
| 30% drop | 89.9% | Ahmedabad | 67.4% |
| 30% spike | 75.4% | Lucknow (smallest of the 10) | 60.3% |
| 20% drop | 63.7% | | |
| 20% spike | 53.8% | | |

Small cities miss more because their daily noise is larger: a 20% move in Lucknow is close to
normal variation. That's a limit of the data, not a bug.

**Read the precision with care.** 90.5% depends on how often anomalies happen, and here 2.3% of
days are injected. The false-alarm rate doesn't depend on that. If real incidents hit 1 day in
1,000, the same detector would have about 29% precision: 0.85 true alarms for every 2.08 false
ones. On the real, unchanged series it flags 48 of 20,064 days (2.4 per 1,000), which is about
the false-alarm rate, so there's no sign of real incidents in this data. A team using this
would want to look at a few alerts a month, or raise the threshold and accept lower recall.

### What went wrong (and what each one teaches)

1. **`lightgbm`'s `LGBMRegressor` needs scikit-learn.** The native `lgb.train` API does the same
   job with one dependency fewer.
2. **A test failed because my hand calculation was wrong.** I expected sMAPE 10.276; the true
   value is (20/210 + 40/380) / 2 = 10.025%. Work the expected value out properly before
   doubting either side.
3. **`def f(seeds=SEEDS)` binds at definition time.** A test that patched `SEEDS` to speed up
   changed nothing. The caller now passes it explicitly.
4. **Looking at the page found a bug that had been there since M3.** Every test passed, but in
   dark mode the charts were unreadable on a white page. One line in `.streamlit/config.toml`,
   `[theme] font = ...`, fixed Streamlit to a light theme, while the chart code still followed
   the browser's dark setting. Removing it fixed all pages, and a test now keeps it out. It only
   showed up by rendering the real page in both modes: *a passing test can't tell you a chart
   is unreadable.*
