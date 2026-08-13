"""Tier routing contracts — shared by the Pi and the laptop.

APEXIS is a router first and a chatbot second. Every task is handled by the
cheapest tier that can do it, escalating only when necessary:

    TIER 1  PI_LOCAL     tiny always-on model on the Pi        (spec Mode C)
    TIER 2  LAPTOP       phi3:mini, loaded on demand, unloaded (spec Mode A)
    TIER 3  CLOUD        Claude — costs money, leaves the house (spec Mode B)

These live in ``shared`` because both nodes must agree on what a routing
decision *is*. The Pi decides; the laptop reports what it can do; the user
sees why.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Tier(IntEnum):
    """Execution tiers, ordered by cost. Higher = more expensive."""

    PI_LOCAL = 1
    LAPTOP = 2
    CLOUD = 3

    @property
    def label(self) -> str:
        return {
            Tier.PI_LOCAL: "Pi (always-on)",
            Tier.LAPTOP: "Laptop (on-demand)",
            Tier.CLOUD: "Cloud (internet)",
        }[self]

    @property
    def leaves_home(self) -> bool:
        """True if using this tier sends data off the local network."""
        return self is Tier.CLOUD


class RoutingDecision(BaseModel):
    """Why a task was sent to a particular tier.

    Every field exists to be shown to the user. The anti-Ultron rule is that
    APEXIS may act on its own, but it may never be *quiet* about it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: Tier
    reason: str = Field(min_length=1)
    complexity: int = Field(ge=0, le=100)
    signals: list[str] = Field(default_factory=list)

    # Set when the chosen tier was not the first choice.
    escalated_from: Tier | None = None

    # True when this decision requires telling the user before acting.
    requires_notice: bool = False

    def notice(self) -> str | None:
        """User-facing message, or None when no notice is required."""
        if not self.requires_notice:
            return None

        if self.tier.leaves_home:
            # Describes the *decision*, not the action. Whether anything
            # actually leaves the machine depends on the tier-3 mode, which
            # the router does not know about. Announcing a network call that
            # may never happen is how you teach someone to ignore warnings.
            return (
                f"This is beyond the local models — {self.reason}. "
                f"Tier 3 will decide how to handle it."
            )
        return f"Using {self.tier.label} — {self.reason}."


class TierAttempt(BaseModel):
    """One tier's attempt at a task, success or failure."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tier: Tier
    ok: bool
    detail: str = ""
    elapsed_ms: float = 0.0


class TaskRecord(BaseModel):
    """The full audit trail for one task, across every tier it touched."""

    model_config = ConfigDict(extra="forbid")

    task: str
    decision: RoutingDecision
    attempts: list[TierAttempt] = Field(default_factory=list)
    final_tier: Tier | None = None
    went_online: bool = False
    started_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def add(self, attempt: TierAttempt) -> None:
        self.attempts.append(attempt)
        if attempt.ok:
            self.final_tier = attempt.tier
            if attempt.tier.leaves_home:
                self.went_online = True


class NodeCapability(BaseModel):
    """What a node can currently do — reported by the laptop to the Pi."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node: Literal["pi", "laptop"]
    online: bool
    models: list[str] = Field(default_factory=list)
    free_ram_mb: int | None = None

    def can_run(self, model: str) -> bool:
        base = model.split(":")[0]
        return self.online and any(m.split(":")[0] == base for m in self.models)
