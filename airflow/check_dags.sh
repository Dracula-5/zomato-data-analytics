#!/usr/bin/env bash
# Fails if any DAG has an import error or an expected DAG is missing. Runs inside the Airflow
# image (CI, or locally: see the airflow-image job in .github/workflows/ci.yml).
# Airflow 3 lists DAGs from its database, so they are parsed into it first (`reserialize`);
# without that, a fresh container lists nothing and "no import errors" would prove nothing.
set -euo pipefail

airflow db migrate > /dev/null
airflow dags reserialize > /dev/null

errors=$(airflow dags list-import-errors -o plain)
echo "$errors"
[ "$errors" = "No data found" ] || { echo "DAG import errors found"; exit 1; }

dags=$(airflow dags list -o plain | awk 'NR > 1 {print $1}' | sort | xargs)
echo "parsed DAGs: $dags"
expected="alerts_smoke zomato_daily zomato_daily_snowflake"
[ "$dags" = "$expected" ] || { echo "expected: $expected"; exit 1; }
