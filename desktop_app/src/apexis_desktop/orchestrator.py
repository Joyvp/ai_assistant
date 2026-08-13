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

import httpx

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
from apexis_desktop.nodes import Fleet, load_fleet


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
        provider_factory: Callable[[str, str], OllamaProvider] | None = None,
        cloud_handler: Callable[[str], str] | None = None,
        unload_after: bool = True,
        fleet: Fleet | None = None,
        laptop_capability: Callable[[], NodeCapability] | None = None,
    ) -> None:
        self.router = router or TierRouter()

        # Which machines exist and where they are. Laptop-only is a normal
        # configuration; the Pi is optional until you connect one.
        self.fleet = fleet or load_fleet()

        # Built after the fleet, so the default lifecycle points at the
        # laptop's *configured* address rather than assuming the default
        # port. Getting this wrong sent unload requests to a machine that
        # was not there.
        self.lifecycle = lifecycle or ModelLifecycle(host=self.fleet.laptop.host)

        # One lifecycle per host — unloading a model on the laptop must not
        # send the request to the Pi. Keyed by base URL, built on demand.
        self._lifecycles: dict[str, ModelLifecycle] = {
            self.fleet.laptop.host: self.lifecycle
        }

        # Injectable so tests never touch a real model.
        self.provider_factory = provider_factory or (
            lambda model, host: OllamaProvider(model=model, host=host)
        )

        # None means "cloud not wired up yet" — the honest default, since
        # spec §15 excludes internet access from V1.
        self.cloud_handler = cloud_handler

        # On 8GB, releasing RAM immediately is usually right.
        self.unload_after = unload_after

        # How to find out whether the laptop can take tier-2 work. The router
        # degrades to the Pi when the laptop is unreachable, so somebody has
        # to actually ask — previously nobody did, and every complex task
        # silently fell back to the small model.
        self._probe_laptop = laptop_capability or self.fleet.laptop_capability

    # -- helpers -----------------------------------------------------------

    def _model_for(self, tier: Tier) -> str | None:
        return {
            Tier.PI_LOCAL: PI_MODEL,
            Tier.LAPTOP: LAPTOP_MODEL,
            Tier.CLOUD: None,
        }[tier]

    def _host_for(self, tier: Tier) -> str:
        """Which machine serves this tier.

        With no Pi configured, the Pi tier runs on the laptop instead — the
        routing decision still stands, it is just served locally.
        """
        if tier is Tier.PI_LOCAL and self.fleet.pi is not None:
            return self.fleet.pi.host
        return self.fleet.laptop.host

    def _lifecycle_for(self, host: str) -> ModelLifecycle:
        if host not in self._lifecycles:
            self._lifecycles[host] = ModelLifecycle(host=host)
        return self._lifecycles[host]

    def _keeps_resident(self, tier: Tier) -> bool:
        """The Pi's model stays loaded; the laptop's does not.

        That asymmetry *is* the architecture. The Pi is always on and holds a
        1B model costing about 1.3GB of a machine doing nothing else, so
        unloading it would only add cold-start latency to every trivial
        request. The laptop's 2.5GB is being taken from a desktop the user is
        actively working on, so it goes back the moment the task is done.
        """
        return tier is Tier.PI_LOCAL and self.fleet.pi is not None

    def _run_local(
        self,
        task: str,
        model: str,
        record: TaskRecord,
        tier: Tier | None = None,
    ) -> tuple[str, int]:
        """Load the model, run the task, release the model."""
        tier = tier or (Tier.LAPTOP if model == LAPTOP_MODEL else Tier.PI_LOCAL)
        host = self._host_for(tier)
        lifecycle = self._lifecycle_for(host)

        before_mb = lifecycle.resident_mb()
        started = time.perf_counter()

        # The Pi keeps its model; the laptop gives its RAM back.
        release = self.unload_after and not self._keeps_resident(tier)

        with lifecycle.borrowed(model, unload_after=release):
            provider = self.provider_factory(model, host)
            try:
                reply = provider.respond(task)
            finally:
                provider.close()

        elapsed = (time.perf_counter() - started) * 1000
        after_mb = lifecycle.resident_mb()

        where = "pi" if tier is Tier.PI_LOCAL and self.fleet.pi else "laptop"
        record.add(
            TierAttempt(
                tier=tier,
                ok=True,
                detail=f"{model} on {where} · {len(reply)} chars",
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

        if laptop is None:
            laptop = self._probe_laptop()

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
                reply, freed = self._run_local(
                    cleaned, LAPTOP_MODEL, record, Tier.LAPTOP
                )
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
            reply, freed = self._run_local(cleaned, model, record, decision.tier)
        except (OllamaError, RuntimeError, httpx.HTTPError) as exc:
            # A node that is switched off, unplugged, or has dropped off the
            # wifi raises here. That is an ordinary Tuesday for a Pi on a
            # shelf, not an error worth crashing the session over.
            record.add(
                TierAttempt(tier=decision.tier, ok=False, detail=str(exc))
            )

            if decision.tier is Tier.PI_LOCAL:
                where = "The Pi" if self.fleet.pi else PI_MODEL
                notices.append(
                    f"{where} did not answer — this laptop handled it instead."
                )
                reply, freed = self._run_local(
                    cleaned, LAPTOP_MODEL, record, Tier.LAPTOP
                )
            else:
                raise

        return TaskResult(cleaned, reply, record, freed, notices)

    def explain(self, task: str, *, laptop: NodeCapability | None = None) -> str:
        """Dry-run: show the routing decision without executing anything."""
        if laptop is None:
            laptop = self._probe_laptop()
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
