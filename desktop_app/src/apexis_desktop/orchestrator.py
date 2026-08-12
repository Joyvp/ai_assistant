"""The APEXIS loop: route → borrow → run → release → report.

This is the piece that makes APEXIS a *system* rather than a chat client.
A task arrives and:

1.  the router picks the cheapest tier that can handle it
2.  if that tier needs a model, it is loaded on demand
3.  the work runs
4.  the model is unloaded so the RAM comes back
5.  everything that happened is recorded and shown to the user

Step 5 is the anti-Ultron rule: APEXIS may act on its own, but never quietly.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from apexis_core.tier_router import TierRouter
from apexis_shared.routing import (
    NodeCapability,
    RoutingDecision,
    TaskRecord,
    Tier,
    TierAttempt,
)

from apexis_desktop.brain.lifecycle import ModelLifecycle
from apexis_desktop.brain.ollama import OllamaError, OllamaProvider


# Which model serves which tier.
PI_MODEL = "llama3.2:1b"
LAPTOP_MODEL = "phi3:mini"


@dataclass
class TaskResult:
    """Everything that happened while handling one task."""

    task: str
    reply: str
    record: TaskRecord
    ram_freed_mb: int = 0
    notices: list[str] = field(default_factory=list)

    @property
    def tier(self) -> Tier | None:
        return self.record.final_tier

    @property
    def went_online(self) -> bool:
        return self.record.went_online

    def summary(self) -> str:
        """One-line account of how the task was handled."""
        if self.tier is None:
            return "failed"

        bits = [self.tier.label]
        if self.ram_freed_mb:
            bits.append(f"{self.ram_freed_mb}MB released")
        if self.went_online:
            bits.append("went online")
        return " · ".join(bits)


class Orchestrator:
    """Route a task to a tier, run it, and release resources afterwards."""

    def __init__(
        self,
        *,
        router: TierRouter | None = None,
        lifecycle: ModelLifecycle | None = None,
        provider_factory: Callable[[str], OllamaProvider] | None = None,
        cloud_handler: Callable[[str], str] | None = None,
        unload_after: bool = True,
    ) -> None:
        self.router = router or TierRouter()
        self.lifecycle = lifecycle or ModelLifecycle()

        # Injectable so tests never touch a real model.
        self.provider_factory = provider_factory or (
            lambda model: OllamaProvider(model=model)
        )

        # None means "cloud not wired up yet" — the honest default, since
        # spec §15 excludes internet access from V1.
        self.cloud_handler = cloud_handler

        # On 8GB, releasing RAM immediately is usually right.
        self.unload_after = unload_after

    # -- helpers -----------------------------------------------------------

    def _model_for(self, tier: Tier) -> str | None:
        return {
            Tier.PI_LOCAL: PI_MODEL,
            Tier.LAPTOP: LAPTOP_MODEL,
            Tier.CLOUD: None,
        }[tier]

    def _run_local(self, task: str, model: str, record: TaskRecord) -> str:
        """Load the model, run the task, release the model."""
        before_mb = self.lifecycle.resident_mb()
        started = time.perf_counter()

        with self.lifecycle.borrowed(
            model, unload_after=self.unload_after
        ):
            provider = self.provider_factory(model)
            try:
                reply = provider.respond(task)
            finally:
                provider.close()

        elapsed = (time.perf_counter() - started) * 1000
        after_mb = self.lifecycle.resident_mb()

        record.add(
            TierAttempt(
                tier=(Tier.LAPTOP if model == LAPTOP_MODEL else Tier.PI_LOCAL),
                ok=True,
                detail=f"{model} · {len(reply)} chars",
                elapsed_ms=elapsed,
            )
        )

        return reply, max(0, before_mb - after_mb) if before_mb > after_mb else 0

    # -- entry point -------------------------------------------------------

    def handle(
        self,
        task: str,
        *,
        laptop: NodeCapability | None = None,
    ) -> TaskResult:
        """Route and execute one task."""
        cleaned = task.strip()
        if not cleaned:
            raise ValueError("task cannot be empty")

        decision = self.router.decide(cleaned, laptop=laptop)
        record = TaskRecord(task=cleaned, decision=decision)

        notices: list[str] = []
        notice = decision.notice()
        if notice:
            notices.append(notice)

        # --- cloud --------------------------------------------------------
        if decision.tier is Tier.CLOUD:
            if self.cloud_handler is None:
                record.add(
                    TierAttempt(
                        tier=Tier.CLOUD,
                        ok=False,
                        detail="cloud not configured",
                    )
                )
                # Fall back to the laptop rather than failing outright.
                reply, freed = self._run_local(cleaned, LAPTOP_MODEL, record)
                notices.append(
                    "Cloud is not configured — answered locally instead. "
                    "The result may be weaker than a cloud model would give."
                )
                return TaskResult(cleaned, reply, record, freed, notices)

            started = time.perf_counter()
            reply = self.cloud_handler(cleaned)
            record.add(
                TierAttempt(
                    tier=Tier.CLOUD,
                    ok=True,
                    detail=f"{len(reply)} chars",
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
            )
            return TaskResult(cleaned, reply, record, 0, notices)

        # --- local tiers --------------------------------------------------
        model = self._model_for(decision.tier)
        assert model is not None  # cloud handled above

        try:
            reply, freed = self._run_local(cleaned, model, record)
        except OllamaError as exc:
            record.add(
                TierAttempt(tier=decision.tier, ok=False, detail=str(exc))
            )

            # Escalate one tier if the cheap model is simply not installed.
            if decision.tier is Tier.PI_LOCAL:
                notices.append(
                    f"{PI_MODEL} unavailable — escalating to {LAPTOP_MODEL}."
                )
                reply, freed = self._run_local(cleaned, LAPTOP_MODEL, record)
            else:
                raise

        return TaskResult(cleaned, reply, record, freed, notices)

    def explain(self, task: str, *, laptop: NodeCapability | None = None) -> str:
        """Dry-run: show the routing decision without executing anything."""
        d: RoutingDecision = self.router.decide(task.strip(), laptop=laptop)

        lines = [
            f"task        {task[:60]}",
            f"tier        {d.tier.label}",
            f"complexity  {d.complexity}/100",
            f"reason      {d.reason}",
        ]
        if d.signals:
            lines.append(f"signals     {', '.join(d.signals)}")
        if d.escalated_from:
            lines.append(f"escalated   from {d.escalated_from.label}")
        notice = d.notice()
        if notice:
            lines.append(f"notice      {notice}")

        return "\n".join(lines)
