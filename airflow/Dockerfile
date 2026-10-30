# Airflow 3.3.2 plus the project's tools in a SEPARATE virtualenv (/opt/zap-venv), so
# dbt, DuckDB and Great Expectations never share dependencies with Airflow itself.
# Versions come from uv.lock (--frozen): the container runs exactly what CI tested.
# The project source is not copied in: docker-compose mounts the repo at /opt/project.
FROM apache/airflow:3.3.2-python3.12

COPY --from=ghcr.io/astral-sh/uv:0.9.25 /uv /usr/local/bin/uv

USER root
RUN mkdir -p /opt/zap-venv /opt/zap-build && chown -R airflow:0 /opt/zap-venv /opt/zap-build
USER airflow

ENV UV_PROJECT_ENVIRONMENT=/opt/zap-venv \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=python3.12

WORKDIR /opt/zap-build
COPY --chown=airflow:0 pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-default-groups --group dbt --group quality \
    && rm -rf /home/airflow/.cache/uv

WORKDIR /opt/airflow
