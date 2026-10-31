# Thin wrapper around the `zap` CLI and the dev tools. Each recipe line is a plain
# `uv run ...` call, so targets behave the same under cmd.exe, PowerShell, Git Bash,
# macOS and Linux CI. Without make, run the same commands by hand.

.DEFAULT_GOAL := help
UV ?= uv
RUN := $(UV) run
export PYTHONUTF8 := 1

SAMPLE_PCT ?= 5

.PHONY: help setup lint format typecheck test check hooks fixtures info \
        drift-check ingest ingest-full ingest-fixture local local-full demo docs docs-serve screenshots metrics recsys forecast bench app \
        up-neo4j down-neo4j graph airflow-bootstrap airflow-up airflow-up-snowflake airflow-down airflow-replay \
        airflow-replay-snowflake airflow-smoke \n        cloud-keys cloud-whoami cloud-infra cloud-setup cloud-upload cloud-load cloud-dbt cloud-check cloud-bench cloud-destroy

help: ## Show this list
	@$(RUN) python scripts/make_help.py $(firstword $(MAKEFILE_LIST))

setup: ## Install all dependency groups and the git hooks
	$(UV) sync --all-groups
	$(RUN) pre-commit install

lint: ## Ruff lint + format check
	$(RUN) ruff check .
	$(RUN) ruff format --check .

format: ## Auto-fix lint issues and format the code
	$(RUN) ruff check --fix .
	$(RUN) ruff format .

typecheck: ## mypy --strict on src/
	$(RUN) mypy

test: ## pytest with the 80% coverage gate; reports go to artifacts/
	$(RUN) pytest --cov --cov-report=term --cov-report=json:artifacts/coverage.json --junitxml=artifacts/pytest.xml

check: lint typecheck test ## Everything CI runs

hooks: ## Run every pre-commit hook on every file
	$(RUN) pre-commit run --all-files

fixtures: ## Generate the SYNTHETIC fixture dataset into .cache/fixture
	$(RUN) zap fixtures --out .cache/fixture

info: ## Show resolved settings (secrets masked)
	$(RUN) zap info

drift-check: ## Compare the source CSVs in DATA_DIR with their contracts
	$(RUN) zap drift-check

ingest: ## Load DATA_DIR into the lake as a SAMPLE_PCT% sample (default 5)
	$(RUN) zap ingest --sample-pct $(SAMPLE_PCT)

ingest-full: ## Load all of DATA_DIR into the lake
	$(RUN) zap ingest --sample-pct 100

ingest-fixture: fixtures ## Load the SYNTHETIC fixture into lake/fixture
	$(RUN) zap ingest --source .cache/fixture --sample-pct 100 --dataset fixture

local: ## Whole pipeline on a SAMPLE_PCT% sample of DATA_DIR (default 5%), zero cloud cost
	$(RUN) zap local --sample-pct $(SAMPLE_PCT)

local-full: ## Whole pipeline on all of DATA_DIR
	$(RUN) zap local --sample-pct 100

demo: fixtures ## Whole pipeline on the SYNTHETIC fixture (no dataset download needed)
	$(RUN) zap local --source .cache/fixture --sample-pct 100 --dataset fixture

docs: ## dbt docs + regenerate docs/DATA_DICTIONARY.md (uses the SAMPLE_PCT warehouse)
	$(RUN) zap gen-dbt-sources
	$(RUN) zap dbt docs generate
	$(RUN) zap data-dictionary

docs-serve: ## Browse the dbt docs site (lineage graph) at http://localhost:8081
	$(RUN) zap dbt docs serve --port 8081

screenshots: ## Screenshot every page into docs/images (needs `make app` running, and Chrome)
	uv run --no-project --with playwright python scripts/screenshots.py

metrics: ## Regenerate docs/METRICS.md from the files that real runs wrote
	$(RUN) zap metrics

recsys: ## Audit the orders for taste and score recommenders on the full dataset
	$(RUN) zap recsys --dataset full

forecast: ## Backtest forecasts and score anomaly detection on the full dataset (needs local-full)
	$(RUN) zap forecast --dataset full

bench: ## Benchmarks on the full dataset: incremental vs full refresh, fact vs mart query
	$(RUN) zap bench all --dataset full

app: ## Open the Streamlit dashboard at http://localhost:8501
	$(RUN) streamlit run app/Home.py

# --- Knowledge graph (Docker) ------------------------------------------------------------
COMPOSE_KG := docker compose --env-file .env -f docker/neo4j/docker-compose.yml

up-neo4j: ## Start Neo4j + GDS on 127.0.0.1. Stop Airflow first: both together need too much RAM
	$(COMPOSE_KG) up -d --wait

down-neo4j: ## Stop Neo4j (keeps its data)
	$(COMPOSE_KG) down

graph: ## Load the 5% sample into Neo4j, run GDS and the queries, export to the dashboard
	$(RUN) zap graph all --dataset sample_5pct

# --- Airflow (Docker) --------------------------------------------------------------------
COMPOSE_AF := docker compose -f airflow/docker-compose.yml
COMPOSE_SF := $(COMPOSE_AF) -f airflow/docker-compose.snowflake.yml
AF_DATASET := airflow_5pct
AF_CUTOFF := 2026-06-23

airflow-bootstrap: ## Build the Airflow dataset with history up to AF_CUTOFF (the DAG replays the rest)
	$(RUN) zap ingest --dataset $(AF_DATASET) --sample-pct 5 --until $(AF_CUTOFF)
	$(RUN) zap load --dataset $(AF_DATASET)
	$(RUN) zap dbt --dataset $(AF_DATASET) build --full-refresh

airflow-up: ## Build the image and start Airflow; UI at http://localhost:8080 (AIRFLOW_HOST_PORT)
	$(COMPOSE_AF) up -d --build

airflow-down: ## Stop Airflow (keeps its metadata volume)
	$(COMPOSE_AF) down

airflow-replay: ## Run the DAG through the scheduler for FROM..TO (default: all of 2026-06-24..30)
	$(COMPOSE_AF) exec airflow-scheduler airflow dags unpause zomato_daily
	$(COMPOSE_AF) exec airflow-scheduler airflow backfill create --dag-id zomato_daily \
		--from-date $(or $(FROM),2026-06-24) --to-date $(or $(TO),2026-06-30) \
		--max-active-runs 1 --reprocess-behavior completed

airflow-up-snowflake: ## Start Airflow with AWS and Snowflake access (see airflow/docker-compose.snowflake.yml)
	$(COMPOSE_SF) up -d --build

airflow-replay-snowflake: ## Run zomato_daily_snowflake for FROM..TO. Uses S3 and Snowflake credits
	$(COMPOSE_SF) exec airflow-scheduler airflow dags unpause zomato_daily_snowflake
	$(COMPOSE_SF) exec airflow-scheduler airflow backfill create --dag-id zomato_daily_snowflake \
		--from-date $(or $(FROM),2026-06-29) --to-date $(or $(TO),2026-06-30) \
		--max-active-runs 1 --reprocess-behavior completed

airflow-smoke: ## Trigger alerts_smoke: one deadline alert + one failure alert -> artifacts/airflow/alerts.jsonl
	$(COMPOSE_AF) exec airflow-scheduler airflow dags unpause alerts_smoke
	$(COMPOSE_AF) exec airflow-scheduler airflow dags trigger alerts_smoke

# --- Cloud (M5): see docs/CLOUD_SETUP.md. These targets cost money, a little. ---------------
TF ?= terraform
TF_DIR := infra/terraform
DATASET ?= full

cloud-keys: ## Create the Snowflake key pair and fill in the bootstrap SQL (free)
	$(RUN) zap cloud keys

cloud-whoami: ## Show which AWS and Snowflake identities are in use (free, changes nothing)
	$(RUN) zap cloud whoami

cloud-infra: ## Terraform: create or update the S3 bucket and IAM role, then save the outputs
	$(TF) -chdir=$(TF_DIR) init -input=false
	$(TF) -chdir=$(TF_DIR) apply -input=false
	$(TF) -chdir=$(TF_DIR) output -json > artifacts/cloud/terraform_output.json

cloud-setup: ## Snowflake: integration, schemas, stage, grants. Re-run cloud-infra afterwards
	$(RUN) zap cloud setup

cloud-upload: ## Mirror lake/$(DATASET) to S3 (only changed files)
	$(RUN) zap cloud upload --dataset $(DATASET)

cloud-load: ## COPY the S3 lake into Snowflake RAW tables
	$(RUN) zap cloud load --dataset $(DATASET)

cloud-dbt: ## dbt build on Snowflake
	$(RUN) zap cloud dbt build

cloud-check: ## Prove ZAP_ANALYST's limits and PII masking, then compare Snowflake with DuckDB
	$(RUN) zap cloud check-access
	$(RUN) zap cloud compare --dataset $(DATASET)

cloud-bench: ## Snowflake benchmarks: incremental vs full refresh, clustering. Uses credits
	$(RUN) zap cloud bench all --dataset $(DATASET)

cloud-destroy: ## Delete the bucket, its data and the IAM role, so AWS stops charging
	$(TF) -chdir=$(TF_DIR) destroy -input=false
