# Thin wrapper around the `zap` CLI and the dev tools. Each recipe line is a plain
# `uv run ...` call, so targets behave the same under cmd.exe, PowerShell, Git Bash,
# macOS and Linux CI. Without make, run the same commands by hand.

.DEFAULT_GOAL := help
UV ?= uv
RUN := $(UV) run
export PYTHONUTF8 := 1

SAMPLE_PCT ?= 5

.PHONY: help setup lint format typecheck test check hooks fixtures info \
        drift-check ingest ingest-full ingest-fixture local local-full demo docs docs-serve bench app \
        airflow-bootstrap airflow-up airflow-down airflow-replay airflow-smoke

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

bench: ## Benchmarks on the full dataset: incremental vs full refresh, fact vs mart query
	$(RUN) zap bench all --dataset full

app: ## Open the Streamlit dashboard at http://localhost:8501
	$(RUN) streamlit run app/Home.py

# --- Airflow (Docker) --------------------------------------------------------------------
COMPOSE_AF := docker compose -f airflow/docker-compose.yml
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

airflow-smoke: ## Trigger alerts_smoke: one deadline alert + one failure alert -> artifacts/airflow/alerts.jsonl
	$(COMPOSE_AF) exec airflow-scheduler airflow dags unpause alerts_smoke
	$(COMPOSE_AF) exec airflow-scheduler airflow dags trigger alerts_smoke
