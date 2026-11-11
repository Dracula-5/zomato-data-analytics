# Business insight queries: results

Written by `zap insights --dataset full` (orders and reviews are synthetic) at 2026-11-11T10:30:53+00:00. Don't edit this by hand: change a query in this folder and run `make insights`. The README's business insights quote these tables.

## 01_demand_by_weekday: Demand by day of week, over the last 365 days of data, all cities

| day | avg_orders | avg_gmv_inr | orders_above_quietest_day_pct | late_delivery_pct | cancelled_pct |
|---|---:|---:|---:|---:|---:|
| Monday | 11,523 | 5,080,152 | 0.4 | 19.4 | 6.0 |
| Tuesday | 11,501 | 5,080,752 | 0.2 | 19.4 | 5.9 |
| Wednesday | 11,474 | 5,069,870 | 0.0 | 19.4 | 6.0 |
| Thursday | 12,066 | 5,333,674 | 5.2 | 19.4 | 6.0 |
| Friday | 15,550 | 6,871,766 | 35.5 | 19.4 | 6.0 |
| Saturday | 18,462 | 8,156,839 | 60.9 | 19.4 | 6.0 |
| Sunday | 16,681 | 7,377,568 | 45.4 | 19.4 | 6.0 |

Query: [01_demand_by_weekday.sql](01_demand_by_weekday.sql)

## 02_late_deliveries: Late deliveries: how many miss the promise, and whether that changes by hour or city

| Measure | Value |
|---|---:|
| delivered_orders | 9,150,122 |
| late_pct | 19.4 |
| median_delivery_min | 31 |
| p90_delivery_min | 45 |
| late_pct_best_hour | 19.2 |
| late_pct_worst_hour | 19.7 |
| busiest_vs_quietest_hour_orders_x | 119 |
| late_pct_best_top10_city | 19.3 |
| late_pct_worst_top10_city | 19.5 |

Query: [02_late_deliveries.sql](02_late_deliveries.sql)

## 03_city_concentration: How concentrated delivered GMV is across cities, over the last 365 days of data

| Measure | Value |
|---|---:|
| cities_with_orders | 552 |
| top_10_cities_gmv_pct | 61.6 |
| cities_holding_80pct_of_gmv | 44 |

Query: [03_city_concentration.sql](03_city_concentration.sql)

## 04_top_cities: The 10 largest cities by delivered GMV, over the last 365 days of data

| city | gmv_share_pct | orders | aov_inr | cancelled_pct |
|---|---:|---:|---:|---:|
| Bangalore | 12.2 | 561,142 | 530 | 6.0 |
| Delhi | 9.2 | 430,924 | 523 | 6.0 |
| Chennai | 8.0 | 394,034 | 500 | 6.0 |
| Hyderabad | 7.6 | 371,377 | 502 | 6.0 |
| Pune | 7.6 | 372,178 | 499 | 6.0 |
| Kolkata | 6.1 | 284,042 | 528 | 6.0 |
| Mumbai | 4.9 | 195,086 | 611 | 5.9 |
| Ahmedabad | 2.5 | 110,201 | 554 | 5.9 |
| Chandigarh | 2.0 | 109,276 | 442 | 5.9 |
| Gurgaon | 1.5 | 76,076 | 480 | 5.9 |

Query: [04_top_cities.sql](04_top_cities.sql)

## 05_forecast_accuracy: Forecast accuracy: average daily error (MAPE) of each model, nationally

| metric | model | mape_pct | days_scored |
|---|---|---:|---:|
| orders | ets | 0.64 | 112 |
| orders | lightgbm | 0.70 | 112 |
| orders | seasonal_naive | 1.16 | 112 |
| gmv | ets | 0.96 | 112 |
| gmv | lightgbm | 1.00 | 112 |
| gmv | seasonal_naive | 1.40 | 112 |

Query: [05_forecast_accuracy.sql](05_forecast_accuracy.sql)

## 06_forecast_next_14_days: The next 14 days: ETS forecast, nationally, against the 14 days before them

| metric | first_day | last_day | forecast_next_14_days | actual_previous_14_days | change_pct |
|---|---|---|---:|---:|---:|
| orders | 2026-07-01 | 2026-07-14 | 222,779 | 221,203 | 0.7 |
| gmv | 2026-07-01 | 2026-07-14 | 98,273,866 | 97,676,981 | 0.6 |

Query: [06_forecast_next_14_days.sql](06_forecast_next_14_days.sql)

## 07_anomaly_alerts: Anomaly alerts: share of planted incidents each detector catches, by size (SYNTHETIC)

| detector | caught_20pct_drops_pct | caught_30pct_drops_pct | caught_50pct_drops_pct | caught_3day_outages_pct | caught_all_planted_pct | false_alarms_per_1000_normal_days |
|---|---:|---:|---:|---:|---:|---:|
| seasonal_robust_z | 63.7 | 89.9 | 99.9 | 97.3 | 85.4 | 2.08 |
| plain_rolling_z | 0.0 | 0.0 | 5.6 | 0.0 | 7.8 | 0.22 |

Query: [07_anomaly_alerts.sql](07_anomaly_alerts.sql)

## 08_anomalies_on_real_days: Real days the main detector flagged, against what its false-alarm rate alone predicts

| Measure | Value |
|---|---:|
| real_days_flagged | 48 |
| series_days_checked | 20,064 |
| flagged_pct | 0.24 |
| expected_from_false_alarms_alone | 42 |

Query: [08_anomalies_on_real_days.sql](08_anomalies_on_real_days.sql)
