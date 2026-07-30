"""FastAPI application for APEXIS Headquarters."""

from datetime import datetime, timezone

from fastapi import FastAPI

from apexis_shared import HealthResponse

from apexis_core import __version__


app = FastAPI(
    title="APEXIS Core",
    description="Headquarters API for the APEXIS distributed system.",
    version=__version__,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.get(
    "/api/v1/health/live",
    response_model=HealthResponse,
    tags=["health"],
)
async def health_live() -> HealthResponse:
    """Return minimal, non-sensitive process health."""

    return HealthResponse(
        version=__version__,
        timestamp=datetime.now(timezone.utc),
    )
