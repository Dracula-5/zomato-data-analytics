# Zomato AI Data Platform

> Work in progress. See [PLAN.md](PLAN.md) for the architecture and milestones. The
> full README (business problem, architecture diagram, metrics, screenshots) arrives in M11.

A food-delivery data platform: CSV → data lake → warehouse (DuckDB locally, Snowflake in
the cloud) → dbt → governance → knowledge graph, forecasting and evaluated GenAI.

## Quickstart (development)

```bash
uv sync --all-groups      # install (Python 3.12, exact versions from uv.lock)
make check                # lint + type check + tests
make fixtures             # generate the SYNTHETIC test dataset
```

## Data

The dataset is not in this repo. The order and review facts are synthetic; the restaurant
and food dimensions appear to be scraped from a food-delivery site. Set `DATA_DIR` in `.env`
(see `.env.example`). Tests and CI use only a generated synthetic fixture.
