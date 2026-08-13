"""Routing inside the chat loop — the tier is chosen per message.

Until now ``apexis talk`` sent everything to phi3 on the laptop, and the
router existed only in tests. This module joins them: each thing you type is
scored, sent to the cheapest machine that can handle it, and the tier is shown
on screen before the reply starts.

Two things make this harder than calling ``Orchestrator.handle()``:

*   **Streaming.** The orchestrator returns a finished string. A chat loop
    needs tokens as they arrive, so the run step is reimplemented here as a
    generator. The routing, borrowing and releasing are identical.

*   **Continuity.** The Pi and the laptop are different processes with
    different context. Ask the Pi a question, then a harder follow-up that
    routes to the laptop, and the laptop would have no idea what "it" meant.
    So conversation history is held *here*, in the loop, and replayed to
    whichever model answers. The machines are interchangeable; the
    conversation is not.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

import httpx

from apexis_core.tier_router import TierRouter
from apexis_shared.routing import NodeCapability, RoutingDecision, Tier

from apexis_desktop.brain.lifecycle import ModelLifecycle
from apexis_desktop.brain.ollama import OllamaError, OllamaProvider
from apexis_desktop.nodes import Fleet, load_fleet
from apexis_desktop.orchestrator import LAPTOP_MODEL, PI_MODEL


# How many past turns to replay to whichever model answers. Small models have
# small context windows, and the Pi's is the smallest, so this stays modest.
HISTORY_TURNS = 6


@dataclass
class Turn:
    """One exchange, and which machine produced it."""

    user: str
    assistant: str
    tier: Tier
    model: str


@dataclass
class RoutedReply:
    """What happened while answering one message."""

    decision: RoutingDecision
    model: str
    host: str
    where: str
    text: str = ""
    ms: float = 0.0
    freed_mb: int = 0
    notices: list[str] = field(default_factory=list)
    fell_back: bool = False

    @property
    def tier(self) -> Tier:
        return self.decision.tier


class RoutedChat:
    """A conversation that picks a machine for every message."""

    def __init__(
        self,
        *,
        fleet: Fleet | None = None,
        router: TierRouter | None = None,
        provider_factory: Callable[[str, str], OllamaProvider] | None = None,
        lifecycle_factory: Callable[[str], ModelLifecycle] | None = None,
        system_prompt: str = "",
        memory: object | None = None,
        unload_after: bool = True,
    ) -> None:
        self.fleet = fleet or load_fleet()
        self.router = router or TierRouter()
        self.system_prompt = system_prompt
        self.memory = memory
        self.unload_after = unload_after

        self.provider_factory = provider_factory or (
            lambda model, host: OllamaProvider(model=model, host=host)
        )
        self._lifecycle_factory = lifecycle_factory or (
            lambda host: ModelLifecycle(host=host)
        )
        self._lifecycles: dict[str, ModelLifecycle] = {}

        # The conversation, held here rather than in any one provider, so it
        # survives moving between machines.
        self.history: list[Turn] = []

    # -- plumbing ----------------------------------------------------------

    def _lifecycle(self, host: str) -> ModelLifecycle:
        if host not in self._lifecycles:
            self._lifecycles[host] = self._lifecycle_factory(host)
        return self._lifecycles[host]

    def _target(self, tier: Tier) -> tuple[str, str, str]:
        """Return (model, host, human-readable machine name) for a tier."""
        if tier is Tier.PI_LOCAL and self.fleet.pi is not None:
            return PI_MODEL, self.fleet.pi.host, "pi"

        if tier is Tier.PI_LOCAL:
            # No Pi connected. The small model may still be installed here,
            # in which case the cheap tier is honoured locally.
            return PI_MODEL, self.fleet.laptop.host, "laptop"

        return LAPTOP_MODEL, self.fleet.laptop.host, "laptop"

    def _keeps_resident(self, tier: Tier) -> bool:
        """Only a real Pi holds its model between requests."""
        return tier is Tier.PI_LOCAL and self.fleet.pi is not None

    def laptop_capability(self) -> NodeCapability:
        return self.fleet.laptop_capability()

    # -- context -----------------------------------------------------------

    def _messages(self, message: str) -> list[dict[str, str]]:
        """Build the message list, replaying recent history.

        Whichever machine answers gets the same conversation, so switching
        tiers mid-thread does not lose the thread.
        """
        messages: list[dict[str, str]] = []

        system = self.system_prompt
        if self.memory is not None:
            try:
                system += self.memory.facts_block()
            except Exception:
                pass  # memory must never break generation
        if system:
            messages.append({"role": "system", "content": system})

        for turn in self.history[-HISTORY_TURNS:]:
            messages.append({"role": "user", "content": turn.user})
            messages.append({"role": "assistant", "content": turn.assistant})

        messages.append({"role": "user", "content": message})
        return messages

    def reset(self) -> None:
        self.history.clear()

    @property
    def turns(self) -> int:
        return len(self.history) * 2

    # -- the loop ----------------------------------------------------------

    def route(self, message: str, *, probe: bool = False) -> RoutedReply:
        """Decide where a message goes, without running it.

        ``probe=True`` also checks the chosen machine is answering, so the
        caller can print an honest destination instead of announcing the Pi
        and then quietly using the laptop. Costs one fast HTTP call.
        """
        decision = self.router.decide(
            message.strip(), laptop=self.laptop_capability()
        )
        model, host, where = self._target(decision.tier)

        if probe and decision.tier is Tier.PI_LOCAL and self.fleet.pi is not None:
            if not self.fleet.pi.is_up():
                model, host, where = LAPTOP_MODEL, self.fleet.laptop.host, "laptop"
                reply = RoutedReply(
                    decision=decision, model=model, host=host, where=where
                )
                reply.fell_back = True
                reply.notices.append(
                    "The Pi is not answering — this laptop is handling it."
                )
                return reply

        reply = RoutedReply(decision=decision, model=model, host=host, where=where)
        notice = decision.notice()
        if notice:
            reply.notices.append(notice)
        return reply

    def ask(self, message: str, plan: RoutedReply | None = None) -> Iterator[str]:
        """Run a message on its chosen tier, yielding tokens as they arrive.

        ``plan`` comes from :meth:`route` when the caller wants to display the
        destination before generation starts. The completed reply is appended
        to history by the time the generator is exhausted.
        """
        cleaned = message.strip()
        if not cleaned:
            raise ValueError("message cannot be empty")

        plan = plan or self.route(cleaned)

        # route(probe=True) may already have redirected this to the laptop.
        effective = Tier.LAPTOP if plan.fell_back else plan.tier

        try:
            yield from self._run(cleaned, plan, tier=effective)
            return
        except (OllamaError, RuntimeError, httpx.HTTPError) as exc:
            # The chosen machine is off, unplugged, or off the wifi. For the
            # cheap tier that is routine — escalate rather than fail.
            if effective is not Tier.PI_LOCAL:
                raise

            where = "The Pi" if self.fleet.pi else f"{PI_MODEL}"
            plan.notices.append(
                f"{where} did not answer ({type(exc).__name__}) — "
                "this laptop handled it instead."
            )
            plan.fell_back = True
            plan.model = LAPTOP_MODEL
            plan.host = self.fleet.laptop.host
            plan.where = "laptop"

        # Retried outside the except block so a second failure reports
        # cleanly rather than as "during handling of the above exception".
        yield from self._run(cleaned, plan, tier=Tier.LAPTOP)

    def _run(
        self, message: str, plan: RoutedReply, *, tier: Tier | None = None
    ) -> Iterator[str]:
        tier = tier or plan.tier
        lifecycle = self._lifecycle(plan.host)

        before_mb = lifecycle.resident_mb()
        started = time.perf_counter()

        release = self.unload_after and not self._keeps_resident(tier)
        collected: list[str] = []

        with lifecycle.borrowed(plan.model, unload_after=release):
            provider = self.provider_factory(plan.model, plan.host)
            try:
                for chunk in provider.stream_messages(self._messages(message)):
                    collected.append(chunk)
                    yield chunk
            finally:
                provider.close()

        plan.ms = (time.perf_counter() - started) * 1000
        after_mb = lifecycle.resident_mb()
        plan.freed_mb = max(0, before_mb - after_mb)

        text = "".join(collected).strip()
        plan.text = text

        if text:
            self.history.append(Turn(message, text, tier, plan.model))
