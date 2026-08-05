"""Tests for the APEXIS Headquarters status client."""

import httpx
import pytest

from apexis_desktop.status import CoreStatusError, fetch_health


VALID_HEALTH = {
    "status": "ok",
    "service": "apexis-core",
    "api_version": "v1",
    "version": "0.1.0",
    "timestamp": "2026-08-04T22:34:06Z",
}


def test_fetch_health_validates_expected_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/health/live"
        return httpx.Response(200, json=VALID_HEALTH)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = fetch_health("http://headquarters.test:8088", client=client)

    assert result.health.status == "ok"
    assert result.health.service == "apexis-core"
    assert result.latency_ms >= 0


def test_fetch_health_rejects_unexpected_fields() -> None:
    invalid_health = {**VALID_HEALTH, "unexpected": "not allowed"}

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=invalid_health)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(CoreStatusError):
            fetch_health("http://headquarters.test:8088", client=client)
