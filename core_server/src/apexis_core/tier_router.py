"""Tier router — decides Pi vs Laptop vs Cloud.

This replaces the keyword-counting ``router.py`` sketch with something that
scores a task on several independent signals and escalates only when a real
threshold is crossed.

Design rules, taken from the master spec:

* **Cheapest tier that can do the job.** Never wake the laptop for "what's
  2+2", never go online for something phi3 can handle.
* **Never silently go online.** Cloud requires ``requires_notice`` and, by
  default, explicit consent (spec §15 excludes public internet from V1).
* **Availability matters as much as complexity.** A hard task cannot go to the
  laptop if the laptop is asleep — it queues instead (spec Mode A).
"""

from __future__ import annotations

import re

from apexis_shared.routing import NodeCapability, RoutingDecision, Tier


# --- scoring signals -------------------------------------------------------
#
# Each signal contributes to a 0-100 complexity score. Weights are deliberately
# blunt and easy to tune; this is a heuristic, not a model.

# Things a 1B model genuinely cannot do well.
_HEAVY_PATTERNS: list[tuple[str, int, str]] = [
    (r"\b(write|build|create|make)\b.{0,30}\b(app|website|site|program|script|api)\b",
     35, "build request"),
    (r"\b(refactor|debug|optimi[sz]e|architect)\b", 30, "engineering task"),
    (r"\b(react|next\.?js|typescript|rust|kubernetes|docker)\b", 25, "complex stack"),
    (r"\b(essay|article|report|proposal|thesis)\b", 25, "long-form writing"),
    # Split rather than alternated: "analyze X and compare Y" is genuinely
    # harder than either alone, and should score for both.
    (r"\b(analy[sz]e|analysis)\b", 20, "analysis"),
    (r"\b(compare|comparison|versus|vs\.?)\b", 15, "comparison"),
    (r"\b(evaluate|critique|assess|review)\b", 15, "evaluation"),
    (r"\b(production|polished|professional|investor|client)\b", 20, "quality bar"),
    (r"\b(step[- ]by[- ]step|in detail|comprehensive|thorough)\b", 15, "depth requested"),
    (r"```|\bcode\b", 15, "code involved"),

    # -- reasoning, not vocabulary ----------------------------------------
    #
    # A real question exposed the gap: "explain why a mixture of experts model
    # can run 26 billion parameters in 2 gigabytes of ram but a normal 7b
    # model cannot" scored ZERO and went to the 1B model on the Pi. It hit no
    # keyword, so the router called it as simple as "hi".
    #
    # Asking *why* something is true is a different kind of work from asking
    # *what* it is. Retrieval is easy; explaining a mechanism is not.
    (r"\bexplain\b", 20, "explanation asked for"),
    (r"\bwhy\b", 20, "asks why, not what"),
    (r"\bhow (does|do|can|could|would|is|are)\b", 20, "asks for a mechanism"),
    (r"\bwhat (makes|causes)\b", 20, "asks for a cause"),
    (r"\b(difference|differences) between\b", 15, "contrast"),
    # Holding two cases against each other is harder than describing one.
    (r"\bbut\b.{0,60}\b(cannot|can'?t|does ?n'?t|won'?t|isn'?t)\b",
     20, "contradiction to resolve"),
    (r"\b(trade[- ]?off|implications|consequences|reasoning)\b",
     15, "reasoning asked for"),
]

# Specialist vocabulary. One such word means little; several together mean the
# question is technical enough that a 1B model will produce fluent nonsense.
_TECHNICAL_TERMS = re.compile(
    r"\b("
    r"parameters?|quanti[sz]ed?|quanti[sz]ation|inference|latency|throughput|"
    r"bandwidth|kernel|cache|token|tokens|embedding|gradient|neural|"
    r"mixture of experts|moe|transformer|attention|checkpoint|weights|"
    r"gigabytes?|megabytes?|terabytes?|billion|allocation|concurrency|"
    r"asynchronous|protocol|encryption|compiler|runtime|firmware|"
    r"filesystem|partition|daemon|systemd|kubernetes|virtuali[sz]ation|"
    r"ram|memory|cpu|gpu|ssd|models?|algorithms?|architecture"
    r")\b"
)

# Things the Pi's tiny model handles fine.
_LIGHT_PATTERNS: list[tuple[str, int, str]] = [
    (r"^\s*(hi|hey|hello|yo|sup|thanks|thank you|ok|okay|bye)\b", -40, "greeting"),
    (r"\b(what time|what's the date|what day)\b", -35, "clock question"),
    (r"\b(remind me|add to list|note that|remember)\b", -30, "memory op"),
    (r"\b(yes|no|maybe|sure)\b\s*$", -30, "short answer"),
    (r"\b(summari[sz]e|tldr|shorten)\b", -15, "summarization"),
    (r"\b(list|show|tell me)\b", -10, "simple retrieval"),
]

# Things that genuinely need current information from the internet.
_NEEDS_INTERNET: list[tuple[str, str]] = [
    (r"\b(latest|current|today'?s|right now|this week)\b.{0,40}"
     r"\b(news|price|weather|score|release|version)\b", "needs live data"),
    (r"\bsearch (the )?(web|internet|online)\b", "explicit web search"),
    (r"\bwhat'?s happening\b", "current events"),
]

# Cheap proxy for "this is a big ask".
_LONG_TASK_CHARS = 280
_VERY_LONG_TASK_CHARS = 700

# Thresholds. Tuned so casual chat stays on the Pi.
_LAPTOP_THRESHOLD = 30
_CLOUD_THRESHOLD = 75


class TierRouter:
    """Score a task and pick the cheapest tier that can handle it."""

    def __init__(
        self,
        *,
        allow_cloud: bool = False,
        laptop_threshold: int = _LAPTOP_THRESHOLD,
        cloud_threshold: int = _CLOUD_THRESHOLD,
    ) -> None:
        # Cloud is opt-in. Spec §15 excludes public internet access from V1,
        # so the safe default is off.
        self.allow_cloud = allow_cloud
        self.laptop_threshold = laptop_threshold
        self.cloud_threshold = cloud_threshold

    # -- scoring -----------------------------------------------------------

    def score(self, task: str) -> tuple[int, list[str]]:
        """Return a 0-100 complexity score and the signals that produced it."""
        text = task.lower().strip()
        if not text:
            return 0, []

        total = 0
        signals: list[str] = []

        for pattern, weight, label in _HEAVY_PATTERNS:
            if re.search(pattern, text):
                total += weight
                signals.append(f"+{weight} {label}")

        for pattern, weight, label in _LIGHT_PATTERNS:
            if re.search(pattern, text):
                total += weight
                signals.append(f"{weight} {label}")

        # Technical density. Scored on distinct terms, so repeating one word
        # cannot inflate it.
        technical = len(set(_TECHNICAL_TERMS.findall(text)))
        if technical >= 2:
            bump = min(15 + (technical - 2) * 10, 35)
            total += bump
            signals.append(f"+{bump} technical ({technical} terms)")

        if len(text) > _VERY_LONG_TASK_CHARS:
            total += 25
            signals.append("+25 very long request")
        elif len(text) > _LONG_TASK_CHARS:
            total += 15
            signals.append("+15 long request")

        # Multi-part requests are harder than their wording suggests.
        parts = len(re.findall(r"\b(and then|also|after that|plus)\b", text))
        if parts:
            bump = min(parts * 10, 30)
            total += bump
            signals.append(f"+{bump} multi-step ({parts} steps)")

        return max(0, min(100, total)), signals

    def needs_internet(self, task: str) -> str | None:
        """Return why the task needs live data, or None."""
        text = task.lower()
        for pattern, label in _NEEDS_INTERNET:
            if re.search(pattern, text):
                return label
        return None

    # -- routing -----------------------------------------------------------

    def decide(
        self,
        task: str,
        *,
        laptop: NodeCapability | None = None,
    ) -> RoutingDecision:
        """Choose a tier for ``task``.

        ``laptop`` describes whether the laptop is reachable and what it can
        run. When the laptop is offline, tier-2 work is kept on the Pi rather
        than escalated to cloud — cost and privacy beat latency.
        """
        complexity, signals = self.score(task)
        internet_reason = self.needs_internet(task)

        laptop_up = bool(laptop and laptop.online)

        # --- live data: only the cloud can answer -------------------------
        if internet_reason:
            if self.allow_cloud:
                return RoutingDecision(
                    tier=Tier.CLOUD,
                    reason=internet_reason,
                    complexity=complexity,
                    signals=[*signals, f"internet: {internet_reason}"],
                    escalated_from=Tier.PI_LOCAL,
                    requires_notice=True,
                )
            return RoutingDecision(
                tier=Tier.LAPTOP if laptop_up else Tier.PI_LOCAL,
                reason=(
                    f"{internet_reason}, but internet is off — "
                    "answering from local knowledge only"
                ),
                complexity=complexity,
                signals=signals,
                requires_notice=True,
            )

        # --- simple: keep it on the Pi ------------------------------------
        if complexity < self.laptop_threshold:
            return RoutingDecision(
                tier=Tier.PI_LOCAL,
                reason=f"simple task (score {complexity})",
                complexity=complexity,
                signals=signals,
            )

        # --- hard enough for the cloud ------------------------------------
        if complexity >= self.cloud_threshold and self.allow_cloud:
            return RoutingDecision(
                tier=Tier.CLOUD,
                reason=f"very complex (score {complexity}) — beyond local models",
                complexity=complexity,
                signals=signals,
                escalated_from=Tier.LAPTOP,
                requires_notice=True,
            )

        # --- laptop tier --------------------------------------------------
        if laptop_up:
            return RoutingDecision(
                tier=Tier.LAPTOP,
                reason=f"complex task (score {complexity}) — waking phi3 on the laptop",
                complexity=complexity,
                signals=signals,
                escalated_from=Tier.PI_LOCAL,
                requires_notice=True,
            )

        # --- laptop asleep: degrade, do not go online ---------------------
        return RoutingDecision(
            tier=Tier.PI_LOCAL,
            reason=(
                f"complex (score {complexity}) but the laptop is offline — "
                "answering with the small model; ask again when it wakes"
            ),
            complexity=complexity,
            signals=signals,
            requires_notice=True,
        )
