"""Command-line status client for APEXIS Headquarters."""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from pydantic import ValidationError

from apexis_shared import HealthResponse


DEFAULT_CORE_URL = "http://monkey.local:8088"
HEALTH_PATH = "/api/v1/health/live"


class CoreStatusError(RuntimeError):
    """Raised when Headquarters cannot return a valid health response."""


@dataclass(frozen=True)
class StatusResult:
    """Validated Headquarters health plus measured request latency."""

    health: HealthResponse
    latency_ms: float


def normalize_core_url(value: str) -> str:
    """Validate and normalize a configured APEXIS Core base URL."""

    candidate = value.strip().rstrip("/")
    parsed = urlparse(candidate)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Core URL must use http or https")
    if not parsed.hostname:
        raise ValueError("Core URL must include a hostname")
    if parsed.username or parsed.password:
        raise ValueError("Credentials are not allowed inside the Core URL")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Core URL must contain only scheme, host, and optional port")

    return candidate


def fetch_health(
    core_url: str,
    *,
    timeout_seconds: float = 3.0,
    client: httpx.Client | None = None,
) -> StatusResult:
    """Request and validate the public APEXIS Core health response."""

    base_url = normalize_core_url(core_url)
    owns_client = client is None

    if client is None:
        client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
        )

    started = time.perf_counter()

    try:
        response = client.get(f"{base_url}{HEALTH_PATH}")
        response.raise_for_status()
        health = HealthResponse.model_validate(response.json())
    except (httpx.HTTPError, ValueError, ValidationError) as exc:
        raise CoreStatusError(str(exc)) from exc
    finally:
        if owns_client:
            client.close()

    latency_ms = (time.perf_counter() - started) * 1000
    return StatusResult(health=health, latency_ms=latency_ms)


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(
        prog="apexis",
        description="Check APEXIS Headquarters status.",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get("APEXIS_CORE_URL", DEFAULT_CORE_URL),
        help="APEXIS Core base URL (default: %(default)s)",
    )
    return parser


def main() -> int:
    """Run the APEXIS status command."""

    args = build_parser().parse_args()

    try:
        result = fetch_health(args.url)
    except (CoreStatusError, ValueError) as exc:
        print("APEXIS Headquarters: OFFLINE", file=sys.stderr)
        print(f"Reason: {exc}", file=sys.stderr)
        return 1

    health = result.health
    print("APEXIS Headquarters: ONLINE")
    print(f"Service: {health.service}")
    print(f"Core version: {health.version}")
    print(f"API version: {health.api_version}")
    print(f"Latency: {result.latency_ms:.1f} ms")
    print(f"Core timestamp: {health.timestamp.isoformat()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
