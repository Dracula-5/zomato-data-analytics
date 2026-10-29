# Thin wrapper around the `zap` CLI and the dev tools. Each recipe line is a plain
# `uv run ...` call, so targets behave the same under cmd.exe, PowerShell, Git Bash,
# macOS and Linux CI. Without make, run the same commands by hand.

.DEFAULT_GOAL := help
UV ?= uv
RUN := $(UV) run
export PYTHONUTF8 := 1

.PHONY: help setup lint format typecheck test check hooks fixtures info

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
