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


def test_cloud_notice_flags_the_task_without_promising_a_network_call(
    cloud_router: TierRouter,
) -> None:
    """The router picks the tier; tier 3 decides whether anything is sent.

    It used to announce "Going online to Claude ... this leaves your network"
    at routing time, which was a lie whenever tier 3 was in handoff or off
    mode. Warnings that are sometimes false get ignored, including the true
    ones.
    """
    decision = cloud_router.decide("what is the latest news on AI", laptop=LAPTOP_UP)

    notice = decision.notice()
    assert notice is not None
    assert "beyond the local models" in notice.lower()
    # Must not assert an action it cannot know happened.
    assert "leaves your network" not in notice.lower()
    assert "claude" not in notice.lower()


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


# -- the router was deaf to difficulty --------------------------------------
#
# A real question from the user went to the 1B model on the Pi:
#
#   "explain why a mixture of experts model can run 26 billion parameters
#    in 2 gigabytes of ram but a normal 7b model cannot"
#
# It scored ZERO - the same as "hi". It contained no keyword from the heavy
# list, and the router only ever looked at vocabulary, never at what was
# being asked for. A question can be hard without using hard words.


def _router():
    from apexis_core.tier_router import TierRouter

    return TierRouter(allow_cloud=True)


def _laptop():
    from apexis_shared.routing import NodeCapability

    return NodeCapability(node="laptop", online=True)


def test_the_moe_question_no_longer_goes_to_the_pi():
    from apexis_shared.routing import Tier

    question = (
        "explain why a mixture of experts model can run 26 billion "
        "parameters in 2 gigabytes of ram but a normal 7b model cannot"
    )
    decision = _router().decide(question, laptop=_laptop())
    assert decision.tier is Tier.CLOUD
    assert decision.complexity >= 75


def test_asking_why_scores_higher_than_asking_what():
    """Explaining a mechanism is different work from naming a thing."""
    router = _router()
    what, _ = router.score("what is a raspberry pi")
    why, _ = router.score("why is a raspberry pi slower than a laptop")
    assert why > what


def test_technical_density_counts_distinct_terms():
    """Repeating one word must not inflate the score."""
    router = _router()
    repeated, _ = router.score("parameters parameters parameters parameters")
    varied, _ = router.score("parameters quantization inference bandwidth")
    assert varied > repeated


def test_small_talk_still_stays_on_the_pi():
    """The fix must not push everything to the cloud."""
    from apexis_shared.routing import Tier

    router = _router()
    for phrase in ("hi", "thanks", "what time is it", "remind me to buy milk",
                   "list my notes", "yes", "summarize this page",
                   "what is a raspberry pi"):
        decision = router.decide(phrase, laptop=_laptop())
        assert decision.tier is Tier.PI_LOCAL, f"{phrase!r} should stay on the Pi"


def test_a_moderately_technical_question_reaches_the_laptop():
    from apexis_shared.routing import Tier

    decision = _router().decide(
        "how does quantization reduce model size", laptop=_laptop()
    )
    assert decision.tier is Tier.LAPTOP


def test_a_contradiction_to_resolve_scores_for_reasoning():
    router = _router()
    plain, _ = router.score("describe how model memory works")
    tension, _ = router.score(
        "why does one model fit in memory but another cannot"
    )
    assert tension > plain


def test_the_cloud_is_still_never_reached_when_it_is_off():
    """Escalation must never override the user's consent setting."""
    from apexis_core.tier_router import TierRouter
    from apexis_shared.routing import Tier

    hard = (
        "explain why a mixture of experts model can run 26 billion "
        "parameters in 2 gigabytes of ram but a normal 7b model cannot"
    )
    decision = TierRouter(allow_cloud=False).decide(hard, laptop=_laptop())
    assert decision.tier is not Tier.CLOUD


# -- the Pi's failure mode is a confident fabrication -----------------------
#
# The user asked why local training is hard and how AI containment works
# during training. It scored 20 and went to the 1B model, which invented
# "quasi-sandboxing" and being "washed clean" - neither is a real term -
# and answered about deployment sandboxing instead of the safety question.
#
# That is the danger the router exists to prevent. A slow answer is a cost.
# A fluent lie is a trap, because nothing about it looks wrong.


SIMPLE = [
    "hi", "thanks", "okay", "bye", "yes",
    "what time is it", "what day is it",
    "remind me to buy milk", "add milk to my list",
    "note that i left at 5", "remember that my sister is joy",
    "list my notes", "show me my tasks", "show me the queue",
    "tell me my notes", "summarize this page", "what is a raspberry pi",
]

CONCEPTUAL = [
    "tell me how ai containment works",
    "why is the sky blue",
    "how is a transformer different from an rnn",
    "how does gradient descent avoid overfitting on a small dataset",
    "what are the tradeoffs of red teaming an aligned model",
    "what are the failure modes of running an llm on a raspberry pi",
    "why is it very hard to maintain and train ai locally and also tell me "
    "how ai containment work while they are training the ai in an sandbox",
]


def test_no_conceptual_question_reaches_the_pi():
    from apexis_shared.routing import Tier

    for question in CONCEPTUAL:
        decision = _router().decide(question, laptop=_laptop())
        assert decision.tier is not Tier.PI_LOCAL, (
            f"{question!r} would be answered by the 1B model"
        )


def test_every_simple_phrase_stays_on_the_pi():
    """The fix must not turn the router into 'send everything to the cloud'."""
    from apexis_shared.routing import Tier

    for phrase in SIMPLE:
        decision = _router().decide(phrase, laptop=_laptop())
        assert decision.tier is Tier.PI_LOCAL, f"{phrase!r} left the Pi"


def test_tell_me_is_only_cheap_when_what_follows_is_cheap():
    """The same two words meant opposite things and both got the discount."""
    router = _router()
    lookup, _ = router.score("tell me my notes")
    concept, _ = router.score("tell me how ai containment works")
    assert concept > lookup + 20


def test_two_questions_in_one_breath_score_more_than_one():
    router = _router()
    single, _ = router.score("why is it hard to train ai locally")
    double, _ = router.score(
        "why is it hard to train ai locally and also tell me how "
        "ai containment works"
    )
    assert double > single


def test_training_vocabulary_is_not_invisible():
    """The term list was built from conversations already had, and had no
    training words at all - so a training question fell straight through."""
    router = _router()
    for term in ("fine-tuning", "dataset", "gradients", "overfitting",
                 "distillation", "hyperparameters"):
        score, _ = router.score(f"how does {term} affect the result")
        assert score >= 30, f"{term!r} scored {score}"


def test_safety_vocabulary_is_not_invisible():
    router = _router()
    for term in ("containment", "alignment", "jailbreak", "guardrails",
                 "interpretability", "red teaming"):
        score, _ = router.score(f"how does {term} work in practice")
        assert score >= 30, f"{term!r} scored {score}"


def test_an_unknown_topic_still_cannot_be_called_trivial():
    """The real protection: a keyword list only knows what its author
    imagined. A conceptual question about something never anticipated must
    still not land on the Pi."""
    from apexis_shared.routing import Tier

    for question in (
        "why does sourdough starter collapse after peaking",
        "how does a differential gearbox split torque",
        "explain why brass instruments need valves but trombones do not",
    ):
        decision = _router().decide(question, laptop=_laptop())
        assert decision.tier is not Tier.PI_LOCAL, f"{question!r} hit the Pi"


def test_the_floor_never_overrides_cloud_consent():
    from apexis_core.tier_router import TierRouter
    from apexis_shared.routing import Tier

    decision = TierRouter(allow_cloud=False).decide(
        CONCEPTUAL[-1], laptop=_laptop()
    )
    assert decision.tier is not Tier.CLOUD


# -- a four-part question scored 50 -----------------------------------------
#
# "tell me how a big parameter ai model can be compromized to fit in 8 gigs
#  ... If it can not fit ... what is the minimum ram ... and what is the
#  maximum ability ..."
#
# Three gaps at once: "how a model CAN BE" put the modal six words after
# "how", the sub-questions were joined by sentence breaks rather than "and
# also", and asking for a minimum figure scored nothing.


PERSONAL = [
    "how much milk do i have",
    "how many tasks do i have",
    "what is the maximum on my list",
    "when did i last go out",
    "where did i put my keys",
    "what do i have left to do",
]


def test_the_four_part_ram_question_reaches_the_cloud():
    from apexis_shared.routing import Tier

    question = (
        "tell me how a big parameter ai model can be compromized to fit in "
        "maximum 8 gigabytes of ram while running 100% locally and it being "
        "able to keep learning more new things while constantly handleing a "
        "server. If it can not fit in 8 gigs of ram what is the minimum "
        "amount of ram needed for an ai model to do what was mentioned above "
        "and what is the maximum ability a local ai model can do with 8 gigs "
        "of ram"
    )
    decision = _router().decide(question, laptop=_laptop())
    assert decision.tier is Tier.CLOUD
    assert decision.complexity >= 90


def test_a_late_modal_still_counts_as_asking_for_a_mechanism():
    """'how a model CAN BE compressed' - six words between how and can."""
    router = _router()
    score, _ = router.score(
        "how a big parameter ai model can be compressed to fit in memory"
    )
    assert score >= 30


def test_questions_split_by_sentences_count_as_several():
    """They used to need 'and also' to count as more than one ask."""
    router = _router()
    one, _ = router.score("what is the minimum ram for a model")
    many, _ = router.score(
        "what is the minimum ram for a model. If it does not fit what is "
        "the maximum size. and what happens then"
    )
    assert many > one + 20


def test_asking_for_a_figure_scores():
    """A 1B model will invent a number as happily as it invents a term."""
    router = _router()
    score, _ = router.score("what is the minimum ram needed to fine tune a 7b model")
    assert score >= 30


def test_questions_about_the_users_own_data_stay_on_the_pi():
    """The counterweight: 'how much milk do i have' is a lookup, not
    research, and belongs on the cheapest tier."""
    from apexis_shared.routing import Tier

    for question in PERSONAL:
        decision = _router().decide(question, laptop=_laptop())
        assert decision.tier is Tier.PI_LOCAL, f"{question!r} left the Pi"


def test_tell_me_how_x_works_is_not_personal_just_because_it_says_me():
    """'me' is an object pronoun here, not the subject of the question."""
    from apexis_shared.routing import Tier

    decision = _router().decide(
        "tell me how ai containment works", laptop=_laptop()
    )
    assert decision.tier is not Tier.PI_LOCAL
