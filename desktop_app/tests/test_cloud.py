"""Tests for tier 3.

Most of these are about honesty rather than functionality. The failure mode
that matters is not "the cloud call broke" — it is "APEXIS said something
about the internet that was not true", in either direction.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apexis_desktop import cloud


@pytest.fixture()
def conf(tmp_path):
    return tmp_path / "cloud.json"


# -- defaults --------------------------------------------------------------


def test_the_default_costs_nothing_and_needs_no_account(conf):
    assert cloud.get_mode(conf) == "handoff"


def test_nothing_is_configured_by_default(conf):
    assert cloud.is_configured(conf) is False


def test_a_corrupt_config_falls_back_to_the_safe_default(conf):
    conf.write_text("{ not json")
    assert cloud.get_mode(conf) == "handoff"
    assert cloud.get_provider(conf) == cloud.DEFAULT_PROVIDER


def test_an_unknown_mode_in_the_file_is_ignored(conf):
    conf.write_text(json.dumps({"mode": "send-everything-everywhere"}))
    assert cloud.get_mode(conf) == "handoff"


def test_an_unknown_provider_in_the_file_is_ignored(conf):
    conf.write_text(json.dumps({"provider": "skynet"}))
    assert cloud.get_provider(conf) == cloud.DEFAULT_PROVIDER


def test_modes_round_trip(conf):
    for mode in ("off", "handoff", "api"):
        cloud.set_mode(mode, conf)
        assert cloud.get_mode(conf) == mode


def test_an_invalid_mode_is_refused(conf):
    with pytest.raises(ValueError):
        cloud.set_mode("whenever-you-like", conf)


def test_every_provider_is_openai_compatible_and_documented():
    for name, spec in cloud.PROVIDERS.items():
        assert spec["url"].startswith("https://"), name
        for field in ("label", "model", "key_env", "signup", "free", "trains"):
            assert spec[field], f"{name} missing {field}"


# -- keys ------------------------------------------------------------------


def test_a_saved_key_is_readable(conf):
    cloud.set_api_key("groq", "abc123", conf)
    assert cloud.api_key("groq", conf) == "abc123"


def test_the_environment_beats_the_file(conf, monkeypatch):
    cloud.set_api_key("groq", "from-file", conf)
    monkeypatch.setenv("GROQ_API_KEY", "from-env")
    assert cloud.api_key("groq", conf) == "from-env"


def test_the_key_file_is_not_world_readable(conf):
    cloud.set_api_key("groq", "abc123", conf)
    assert conf.stat().st_mode & 0o077 == 0


def test_a_key_for_an_unknown_provider_is_refused(conf):
    with pytest.raises(ValueError):
        cloud.set_api_key("skynet", "abc", conf)


def test_api_mode_without_a_key_is_not_configured(conf):
    cloud.set_mode("api", conf)
    assert cloud.is_configured(conf) is False


def test_api_mode_with_a_key_is_configured(conf):
    cloud.set_mode("api", conf)
    cloud.set_api_key(cloud.DEFAULT_PROVIDER, "k", conf)
    assert cloud.is_configured(conf) is True


# -- the handoff -----------------------------------------------------------


def test_the_prompt_carries_the_question():
    prompt = cloud.build_prompt("how do I do X")
    assert "how do I do X" in prompt


def test_the_prompt_carries_recent_context():
    prompt = cloud.build_prompt(
        "and then?", history=[("hello", "hi"), ("build a thing", "ok")]
    )
    assert "build a thing" in prompt


def test_the_prompt_only_carries_the_last_few_turns():
    history = [(f"q{i}", f"a{i}") for i in range(10)]
    prompt = cloud.build_prompt("now what", history=history)
    assert "q9" in prompt
    assert "q0" not in prompt


def test_handoff_sends_nothing(conf):
    cloud.set_mode("handoff", conf)
    result = cloud.handle("a hard question", path=conf)

    assert result.went_online is False
    assert result.answered is False
    assert "a hard question" in result.prompt


def test_off_sends_nothing_and_produces_no_prompt(conf):
    cloud.set_mode("off", conf)
    result = cloud.handle("a hard question", path=conf)

    assert result.went_online is False
    assert result.prompt == ""
    assert any("turned off" in n for n in result.notices)


# -- privacy ---------------------------------------------------------------


def test_facts_are_not_included_by_default(conf):
    cloud.set_mode("handoff", conf)
    result = cloud.handle(
        "a hard question", facts="FACTS: lives in Saskatoon", path=conf
    )
    assert "Saskatoon" not in result.prompt


def test_facts_are_included_when_explicitly_permitted(conf):
    cloud.set_mode("handoff", conf)
    result = cloud.handle(
        "a hard question",
        facts="FACTS: lives in Saskatoon",
        send_facts=True,
        path=conf,
    )
    assert "Saskatoon" in result.prompt


def test_facts_do_not_reach_a_provider_by_default(conf):
    body = {}

    def handler(request):
        body["sent"] = request.content.decode()
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}]}
        )

    cloud.set_mode("api", conf)
    cloud.set_api_key("groq", "k", conf)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cloud.handle(
            "hard", facts="FACTS: lives in Saskatoon", path=conf, client=client
        )

    assert "Saskatoon" not in body["sent"]


# -- the online call -------------------------------------------------------


def _ok(content: str = "the answer"):
    def handler(request):
        return httpx.Response(
            200, json={"choices": [{"message": {"content": content}}]}
        )

    return handler


def test_a_successful_call_returns_the_text(conf):
    cloud.set_api_key("groq", "k", conf)
    with httpx.Client(transport=httpx.MockTransport(_ok())) as client:
        got = cloud.ask_online("hi", path=conf, client=client)
    assert got == "the answer"


def test_the_key_is_sent_as_a_bearer_token(conf):
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "x"}}]}
        )

    cloud.set_api_key("groq", "secret-key", conf)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cloud.ask_online("hi", path=conf, client=client)

    assert seen["auth"] == "Bearer secret-key"


def test_no_key_raises_rather_than_silently_doing_nothing(conf):
    with pytest.raises(cloud.CloudError, match="no API key"):
        cloud.ask_online("hi", path=conf)


def test_a_rejected_key_says_so(conf):
    cloud.set_api_key("groq", "bad", conf)

    def handler(request):
        return httpx.Response(401, json={"error": "nope"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cloud.CloudError, match="rejected"):
            cloud.ask_online("hi", path=conf, client=client)


def test_a_rate_limit_mentions_the_free_tier(conf):
    cloud.set_api_key("groq", "k", conf)

    def handler(request):
        return httpx.Response(429, json={"error": "slow down"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cloud.CloudError, match="rate limit"):
            cloud.ask_online("hi", path=conf, client=client)


def test_an_unreachable_provider_is_a_clean_error(conf):
    cloud.set_api_key("groq", "k", conf)

    def handler(request):
        raise httpx.ConnectError("no internet")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cloud.CloudError, match="could not reach"):
            cloud.ask_online("hi", path=conf, client=client)


def test_a_malformed_response_is_a_clean_error(conf):
    cloud.set_api_key("groq", "k", conf)

    def handler(request):
        return httpx.Response(200, json={"unexpected": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(cloud.CloudError, match="unexpected"):
            cloud.ask_online("hi", path=conf, client=client)


# -- honesty ---------------------------------------------------------------


def test_going_online_is_announced(conf):
    cloud.set_mode("api", conf)
    cloud.set_api_key("groq", "k", conf)

    with httpx.Client(transport=httpx.MockTransport(_ok())) as client:
        result = cloud.handle("hard", path=conf, client=client)

    assert result.went_online is True
    assert any("leaving your network" in n for n in result.notices)


def test_the_notice_names_the_provider_actually_used(conf):
    cloud.set_mode("api", conf)
    cloud.set_provider("gemini", conf)
    cloud.set_api_key("gemini", "k", conf)

    with httpx.Client(transport=httpx.MockTransport(_ok())) as client:
        result = cloud.handle("hard", path=conf, client=client)

    assert "Gemini" in result.notices[0]
    assert "Groq" not in result.notices[0]


@pytest.mark.parametrize("mode", ["off", "handoff"])
def test_offline_modes_never_claim_to_have_gone_online(conf, mode):
    cloud.set_mode(mode, conf)
    result = cloud.handle("hard", path=conf)

    assert result.went_online is False
    joined = " ".join(result.notices).lower()
    assert "leaving your network" not in joined


def test_the_routing_notice_does_not_promise_a_network_call():
    """The router does not know the tier-3 mode, so it must not assert one."""
    from apexis_shared.routing import RoutingDecision, Tier

    decision = RoutingDecision(
        tier=Tier.CLOUD,
        reason="very complex",
        complexity=90,
        requires_notice=True,
    )
    notice = decision.notice()

    assert "leaves your network" not in notice
    # And it must not name a provider the user may not even be using.
    assert "Claude" not in notice
