"""Tests for the three-tier router."""

from __future__ import annotations

import pytest

from apexis_core.tier_router import TierRouter
from apexis_shared.routing import NodeCapability, Tier


LAPTOP_UP = NodeCapability(
    node="laptop", online=True, models=["phi3:mini"], free_ram_mb=4096
)
LAPTOP_DOWN = NodeCapability(node="laptop", online=False, models=[])


@pytest.fixture
def router() -> TierRouter:
    return TierRouter()


@pytest.fixture
def cloud_router() -> TierRouter:
    return TierRouter(allow_cloud=True)


# -- tier 1: stays on the Pi -----------------------------------------------


@pytest.mark.parametrize(
    "task",
    [
        "hi",
        "hello there",
        "thanks",
        "what time is it",
        "remind me to call mum",
        "remember that my project is APEXIS",
    ],
)
def test_simple_tasks_stay_on_pi(router: TierRouter, task: str) -> None:
    decision = router.decide(task, laptop=LAPTOP_UP)

    assert decision.tier is Tier.PI_LOCAL
    assert decision.requires_notice is False
    assert decision.notice() is None


def test_greeting_scores_zero(router: TierRouter) -> None:
    score, _ = router.score("hey")

    assert score == 0


# -- tier 2: wakes the laptop ----------------------------------------------


@pytest.mark.parametrize(
    "task",
    [
        "write me a website for my business",
        "refactor this function to be faster",
        "build a react app with typescript",
        "analyze these three approaches and compare them",
    ],
)
def test_complex_tasks_wake_the_laptop(router: TierRouter, task: str) -> None:
    decision = router.decide(task, laptop=LAPTOP_UP)

    assert decision.tier is Tier.LAPTOP
    assert decision.escalated_from is Tier.PI_LOCAL
    assert decision.requires_notice is True
    assert "laptop" in decision.notice().lower()


def test_laptop_offline_degrades_to_pi_not_cloud(router: TierRouter) -> None:
    decision = router.decide("build me a react website", laptop=LAPTOP_DOWN)

    # Cost and privacy beat latency: do NOT silently escalate to cloud.
    assert decision.tier is Tier.PI_LOCAL
    assert "offline" in decision.reason
    assert decision.requires_notice is True


# -- tier 3: cloud ----------------------------------------------------------


def test_cloud_is_off_by_default(router: TierRouter) -> None:
    task = (
        "build a production-ready e-commerce website in next.js with "
        "typescript and stripe, then refactor the checkout, and also "
        "write a comprehensive investor report analyzing the architecture "
        "step-by-step in detail"
    )
    decision = router.decide(task, laptop=LAPTOP_UP)

    assert decision.tier is not Tier.CLOUD


def test_very_complex_goes_to_cloud_when_allowed(cloud_router: TierRouter) -> None:
    task = (
        "build a production-ready e-commerce website in next.js with "
        "typescript and stripe, then refactor the checkout, and also "
        "write a comprehensive investor report analyzing the architecture "
        "step-by-step in detail"
    )
    decision = cloud_router.decide(task, laptop=LAPTOP_UP)

    assert decision.tier is Tier.CLOUD
    assert decision.requires_notice is True
    assert decision.tier.leaves_home is True


def test_cloud_notice_warns_about_leaving_the_network(
    cloud_router: TierRouter,
) -> None:
    decision = cloud_router.decide("what is the latest news on AI", laptop=LAPTOP_UP)

    notice = decision.notice()
    assert notice is not None
    assert "online" in notice.lower()
    assert "logged" in notice.lower()


# -- live data --------------------------------------------------------------


@pytest.mark.parametrize(
    "task",
    [
        "what is the latest news about AI",
        "search the web for python tutorials",
        "what's today's weather",
    ],
)
def test_live_data_detected(router: TierRouter, task: str) -> None:
    assert router.needs_internet(task) is not None


def test_live_data_without_cloud_says_so(router: TierRouter) -> None:
    decision = router.decide("what is the latest news on AI", laptop=LAPTOP_UP)

    assert decision.tier is not Tier.CLOUD
    assert "internet is off" in decision.reason
    assert decision.requires_notice is True


# -- scoring ---------------------------------------------------------------


def test_multi_step_increases_score(router: TierRouter) -> None:
    simple, _ = router.score("write a script")
    multi, _ = router.score("write a script and then test it and also document it")

    assert multi > simple


def test_long_requests_score_higher(router: TierRouter) -> None:
    short, _ = router.score("analyze this")
    long, _ = router.score("analyze this. " + "extra context here. " * 40)

    assert long > short


def test_score_is_bounded(router: TierRouter) -> None:
    score, _ = router.score("build refactor optimize " * 50)

    assert 0 <= score <= 100


def test_signals_explain_the_decision(router: TierRouter) -> None:
    decision = router.decide("refactor this react app", laptop=LAPTOP_UP)

    assert decision.signals
    assert any("engineering" in s or "stack" in s for s in decision.signals)


def test_empty_task_scores_zero(router: TierRouter) -> None:
    score, signals = router.score("   ")

    assert score == 0
    assert signals == []


# -- capability model -------------------------------------------------------


def test_can_run_matches_base_model_name() -> None:
    cap = NodeCapability(node="laptop", online=True, models=["phi3:mini"])

    assert cap.can_run("phi3") is True
    assert cap.can_run("phi3:mini") is True
    assert cap.can_run("llama3.2") is False


def test_offline_node_can_run_nothing() -> None:
    cap = NodeCapability(node="laptop", online=False, models=["phi3:mini"])

    assert cap.can_run("phi3:mini") is False


def test_only_cloud_leaves_home() -> None:
    assert Tier.PI_LOCAL.leaves_home is False
    assert Tier.LAPTOP.leaves_home is False
    assert Tier.CLOUD.leaves_home is True
