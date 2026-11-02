from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from zap import api


def _client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> TestClient:
    db = tmp_path / "fixture.duckdb"
    db.touch()
    monkeypatch.setattr(api, "_database_path", lambda: db)
    return TestClient(api.app)


def test_health_reports_ready_dataset(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health_rejects_missing_warehouse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(api, "_database_path", lambda: tmp_path / "missing.duckdb")

    response = TestClient(api.app).get("/healthz")

    assert response.status_code == 503
    assert response.json()["detail"] == "Demo warehouse is not ready"


def test_overview_returns_serializable_aggregates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        api.data,
        "kpi_summary",
        lambda _: {
            "first_day": date(2026, 1, 1),
            "last_day": date(2026, 1, 2),
            "orders": 25,
            "delivered": 21,
            "gmv": 5500,
            "aov": 261.9,
            "cancel_rate": 0.16,
            "sla_breach_rate": 0.12,
            "customers": 18,
        },
    )
    monkeypatch.setattr(
        api.data,
        "daily_kpis",
        lambda _: pd.DataFrame([{"order_date": date(2026, 1, 1), "orders": 12, "gmv": 2200}]),
    )

    response = client.get("/api/v1/overview")

    assert response.status_code == 200
    assert response.json()["summary"]["orders"] == 25
    assert response.json()["daily"][0]["order_date"] == "2026-01-01T00:00:00.000"


def test_cities_uses_data_window_and_rejects_reversed_dates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(
        api.data,
        "kpi_summary",
        lambda _: {"first_day": date(2026, 1, 1), "last_day": date(2026, 1, 31)},
    )
    calls: list[tuple[date, date]] = []
    monkeypatch.setattr(
        api.data,
        "city_summary",
        lambda _, start, end: (
            calls.append((start, end)) or pd.DataFrame([{"city": "Pune", "gmv": 1000}])
        ),
    )

    response = client.get("/api/v1/cities")
    invalid = client.get("/api/v1/cities?start_date=2026-01-20&end_date=2026-01-02")

    assert response.status_code == 200
    assert calls == [(date(2026, 1, 1), date(2026, 1, 31))]
    assert invalid.status_code == 422


def test_restaurants_forwards_validated_filters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(
        api.data,
        "top_restaurants",
        lambda _, city, limit: (
            calls.append((city, limit))
            or pd.DataFrame([{"rank": 1, "restaurant_name": "Demo Kitchen", "gmv": 1000}])
        ),
    )

    response = client.get("/api/v1/restaurants?city=Pune&limit=5")
    invalid = client.get("/api/v1/restaurants?city=Pune&limit=1000")

    assert response.status_code == 200
    assert calls == [("Pune", 5)]
    assert invalid.status_code == 422


def test_metadata_discloses_synthetic_demo_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)

    response = client.get("/api/v1/meta")

    assert response.status_code == 200
    assert response.json()["available"] is True
    assert "SYNTHETIC" in response.json()["data_notice"]
