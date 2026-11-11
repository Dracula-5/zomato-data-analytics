# Business insight queries

The queries behind the "Business insights" section of the [README](../../README.md). Each `.sql`
file answers one question against the DuckDB warehouse, and its first comment line says which.
The latest answers are in [RESULTS.md](RESULTS.md); the README quotes only that file.

```bash
make insights                 # full data (needs `make local-full`, and `make forecast` for 05-08)
make insights DATASET=fixture # the SYNTHETIC fixture from `make demo`; 05-08 are skipped
```

Or run one file yourself from the repository root with the DuckDB CLI, read-only:
`duckdb -readonly warehouse/full.duckdb < docs/insights/01_demand_by_weekday.sql`.

| File | Question | Feeds insight |
|---|---|---|
| [01_demand_by_weekday.sql](01_demand_by_weekday.sql) | How do orders, lateness and cancellations change by day of week? | 1, 2 |
| [02_late_deliveries.sql](02_late_deliveries.sql) | How many deliveries miss the promise, and does it depend on hour or city? | 2 |
| [03_city_concentration.sql](03_city_concentration.sql) | How much of GMV sits in a few cities? | 3 |
| [04_top_cities.sql](04_top_cities.sql) | The 10 largest cities: GMV share, AOV, cancellations | 3 |
| [05_forecast_accuracy.sql](05_forecast_accuracy.sql) | How far off is each forecasting model, day by day? | 4 |
| [06_forecast_next_14_days.sql](06_forecast_next_14_days.sql) | What do the next 14 days look like? | 4 |
| [07_anomaly_alerts.sql](07_anomaly_alerts.sql) | What share of planted incidents does each alert catch, by size? | 5 |
| [08_anomalies_on_real_days.sql](08_anomalies_on_real_days.sql) | Did the alerts find anything real? | 5 |

The orders and reviews are synthetic, so these describe the dataset, not a real business, and the
incidents in 07 are planted on purpose (SYNTHETIC).
