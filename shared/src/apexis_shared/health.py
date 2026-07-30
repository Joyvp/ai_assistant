"""Health-response contracts shared by APEXIS nodes."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """Minimal non-sensitive status returned by APEXIS Core."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
    )

    status: Literal["ok"] = "ok"
    service: Literal["apexis-core"] = "apexis-core"
    api_version: Literal["v1"] = "v1"
    version: str = Field(min_length=1)
    timestamp: datetime
