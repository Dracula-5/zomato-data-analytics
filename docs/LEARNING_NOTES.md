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
