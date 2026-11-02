"""Read-only HTTP API for the public portfolio dashboard."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any, cast

import pandas as pd
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from zap.config import get_settings
from zap.dashboard import data

app = FastAPI(
    title="Zomato AI Data Platform API",
    description="Read-only analytics API over the selected DuckDB demo warehouse.",
    version="1.0.0",
)

origins = [
    origin.strip()
    for origin in os.environ.get("FRONTEND_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


def _database_path() -> Path:
    dataset = os.environ.get("ZAP_DATASET", "fixture")
    return get_settings().duckdb_path(dataset)


def _require_database() -> Path:
    path = _database_path()
    if not path.is_file():
        raise HTTPException(
            status_code=503,
            detail=f"Warehouse dataset is not available: {path.name}",
        )
    return path


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]], json.loads(frame.to_json(orient="records", date_format="iso"))
    )


def _summary_record(summary: dict[str, Any]) -> dict[str, Any]:
    return cast(
        dict[str, Any],
        json.loads(pd.DataFrame([summary]).to_json(orient="records", date_format="iso"))[0],
    )


@app.get("/healthz", tags=["health"])
def health() -> dict[str, str]:
    path = _database_path()
    if not path.is_file():
        raise HTTPException(status_code=503, detail="Demo warehouse is not ready")
    return {"status": "ok", "dataset": os.environ.get("ZAP_DATASET", "fixture")}


@app.get("/api/v1/meta", tags=["dashboard"])
def metadata() -> dict[str, Any]:
    path = _database_path()
    return {
        "dataset": os.environ.get("ZAP_DATASET", "fixture"),
        "available": path.is_file(),
        "data_notice": "Orders and reviews are SYNTHETIC; results are for demonstration only.",
    }


@app.get("/api/v1/overview", tags=["dashboard"])
def overview() -> dict[str, Any]:
    db = _require_database()
    return {
        "summary": _summary_record(data.kpi_summary(db)),
        "daily": _records(data.daily_kpis(db)),
    }


@app.get("/api/v1/cities", tags=["dashboard"])
def cities(
    start_date: date | None = None,
    end_date: date | None = None,
) -> dict[str, Any]:
    db = _require_database()
    summary = data.kpi_summary(db)
    first_day = pd.Timestamp(summary["first_day"]).date()
    last_day = pd.Timestamp(summary["last_day"]).date()
    start = start_date or first_day
    end = end_date or last_day
    if start > end:
        raise HTTPException(status_code=422, detail="start_date must be on or before end_date")
    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "items": _records(data.city_summary(db, start, end)),
    }


@app.get("/api/v1/restaurants", tags=["dashboard"])
def restaurants(
    city: str = Query(min_length=1, max_length=120),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    return {"items": _records(data.top_restaurants(_require_database(), city, limit))}


@app.get("/api/v1/delivery-sla", tags=["dashboard"])
def delivery_sla(top_cities: int = Query(default=12, ge=1, le=50)) -> dict[str, Any]:
    return {"items": _records(data.delivery_sla(_require_database(), top_cities))}


@app.get("/api/v1/data-quality", tags=["dashboard"])
def data_quality() -> dict[str, Any]:
    db = _require_database()
    return {
        "rules": _records(data.dq_results(db)),
        "dbt_tests": _records(data.dbt_test_summary(db)),
    }
