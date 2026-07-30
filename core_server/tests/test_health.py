"""Tests for the public, non-sensitive health endpoint."""

from datetime import datetime

from fastapi.testclient import TestClient

from apexis_core.app import app


client = TestClient(app)


def test_health_live_returns_expected_contract() -> None:
    response = client.get("/api/v1/health/live")

    assert response.status_code == 200

    payload = response.json()

    assert set(payload) == {
        "status",
        "service",
        "api_version",
        "version",
        "timestamp",
    }
    assert payload["status"] == "ok"
    assert payload["service"] == "apexis-core"
    assert payload["api_version"] == "v1"
    assert payload["version"] == "0.1.0"

    timestamp = datetime.fromisoformat(
        payload["timestamp"].replace("Z", "+00:00")
    )
    assert timestamp.tzinfo is not None
