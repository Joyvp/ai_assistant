"""Tests for model load/unload lifecycle and the orchestrator."""

from __future__ import annotations

import json

import httpx
import pytest

from apexis_core.tier_router import TierRouter
from apexis_shared.routing import NodeCapability, Tier

from apexis_desktop.brain.lifecycle import LoadedModel, ModelLifecycle
from apexis_desktop.orchestrator import LAPTOP_MODEL, PI_MODEL, Orchestrator


LAPTOP_UP = NodeCapability(node="laptop", online=True, models=[LAPTOP_MODEL])

GB = 1024**3


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# -- inspection ------------------------------------------------------------


def test_resident_lists_loaded_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/ps"
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "phi3:mini", "size": 2 * GB, "expires_at": "later"}
                ]
            },
        )

    lc = ModelLifecycle(client=_client(handler))
    resident = lc.resident()

    assert len(resident) == 1
    assert resident[0].name == "phi3:mini"
    assert resident[0].size_gb == 2.0


def test_resident_empty_when_ollama_down() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    lc = ModelLifecycle(client=_client(handler))

    assert lc.resident() == []
    assert lc.resident_mb() == 0


def test_is_resident_matches_base_name() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": "phi3:mini", "size": 0}]})

    lc = ModelLifecycle(client=_client(handler))

    assert lc.is_resident("phi3") is True
    assert lc.is_resident("phi3:mini") is True
    assert lc.is_resident("llama3.2:1b") is False


def test_resident_mb_totals_all_models() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "models": [
                    {"name": "phi3:mini", "size": 2 * GB},
                    {"name": "llama3.2:1b", "size": 1 * GB},
                ]
            },
        )

    lc = ModelLifecycle(client=_client(handler))

    assert lc.resident_mb() == 3072


# -- control ---------------------------------------------------------------


def test_load_sends_empty_prompt_with_keep_alive() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"done": True})

    lc = ModelLifecycle(client=_client(handler))
    elapsed = lc.load("phi3:mini", keep_alive="10m")

    assert captured == {"model": "phi3:mini", "prompt": "", "keep_alive": "10m"}
    assert elapsed >= 0


def test_unload_sends_keep_alive_zero() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"done": True})

    lc = ModelLifecycle(client=_client(handler))

    assert lc.unload("phi3:mini") is True
    assert captured["keep_alive"] == 0
    assert captured["prompt"] == ""


def test_unload_returns_false_on_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    lc = ModelLifecycle(client=_client(handler))

    assert lc.unload("phi3:mini") is False


def test_unload_all_unloads_every_resident_model() -> None:
    unloaded: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": "phi3:mini", "size": GB},
                        {"name": "llama3.2:1b", "size": GB},
                    ]
                },
            )
        unloaded.append(json.loads(request.content)["model"])
        return httpx.Response(200, json={"done": True})

    lc = ModelLifecycle(client=_client(handler))

    assert lc.unload_all() == 2
    assert set(unloaded) == {"phi3:mini", "llama3.2:1b"}


# -- borrowed() ------------------------------------------------------------


def test_borrowed_loads_then_unloads() -> None:
    calls: list[tuple[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        body = json.loads(request.content)
        calls.append((body["model"], body["keep_alive"]))
        return httpx.Response(200, json={"done": True})

    lc = ModelLifecycle(client=_client(handler))

    with lc.borrowed("phi3:mini"):
        pass

    assert calls[0] == ("phi3:mini", "5m")   # load
    assert calls[-1] == ("phi3:mini", 0)     # unload


def test_borrowed_releases_even_when_block_raises() -> None:
    calls: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        calls.append(json.loads(request.content)["keep_alive"])
        return httpx.Response(200, json={"done": True})

    lc = ModelLifecycle(client=_client(handler))

    with pytest.raises(RuntimeError, match="boom"):
        with lc.borrowed("phi3:mini"):
            raise RuntimeError("boom")

    assert 0 in calls, "model must be unloaded even on failure"


def test_borrowed_leaves_already_resident_model_alone() -> None:
    """Something else may be mid-conversation — do not yank its model."""
    unload_calls: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(
                200, json={"models": [{"name": "phi3:mini", "size": GB}]}
            )
        body = json.loads(request.content)
        if body.get("keep_alive") == 0:
            unload_calls.append(body["model"])
        return httpx.Response(200, json={"done": True})

    lc = ModelLifecycle(client=_client(handler))

    with lc.borrowed("phi3:mini"):
        pass

    assert unload_calls == []


def test_borrowed_can_keep_model_resident() -> None:
    unload_calls: list[object] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        body = json.loads(request.content)
        if body.get("keep_alive") == 0:
            unload_calls.append(body["model"])
        return httpx.Response(200, json={"done": True})

    lc = ModelLifecycle(client=_client(handler))

    with lc.borrowed("phi3:mini", unload_after=False):
        pass

    assert unload_calls == []


# -- orchestrator ----------------------------------------------------------


class FakeProvider:
    """Stand-in for OllamaProvider that records what it was asked."""

    def __init__(self, model: str, reply: str = "ok") -> None:
        self.model = model
        self.reply = reply
        self.closed = False

    def respond(self, task: str) -> str:
        return f"[{self.model}] {self.reply}"

    def close(self) -> None:
        self.closed = True


def _fake_lifecycle() -> ModelLifecycle:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, json={"done": True})

    return ModelLifecycle(client=_client(handler))


def test_simple_task_uses_pi_model() -> None:
    used: list[str] = []

    def factory(model: str) -> FakeProvider:
        used.append(model)
        return FakeProvider(model)

    orch = Orchestrator(
        lifecycle=_fake_lifecycle(),
        provider_factory=lambda m, h: factory(m),
    )
    result = orch.handle("hey", laptop=LAPTOP_UP)

    assert result.tier is Tier.PI_LOCAL
    assert used == [PI_MODEL]
    assert result.went_online is False


def test_complex_task_uses_laptop_model() -> None:
    used: list[str] = []

    def factory(model: str) -> FakeProvider:
        used.append(model)
        return FakeProvider(model)

    orch = Orchestrator(
        lifecycle=_fake_lifecycle(),
        provider_factory=lambda m, h: factory(m),
    )
    result = orch.handle("build me a react website with typescript", laptop=LAPTOP_UP)

    assert result.tier is Tier.LAPTOP
    assert used == [LAPTOP_MODEL]
    assert result.notices, "escalation must be announced"


def test_provider_is_always_closed() -> None:
    created: list[FakeProvider] = []

    def factory(model: str) -> FakeProvider:
        p = FakeProvider(model)
        created.append(p)
        return p

    orch = Orchestrator(lifecycle=_fake_lifecycle(), provider_factory=lambda m, h: factory(m))
    orch.handle("hey", laptop=LAPTOP_UP)

    assert all(p.closed for p in created)


def test_cloud_without_handler_falls_back_and_says_so() -> None:
    orch = Orchestrator(
        router=TierRouter(allow_cloud=True),
        lifecycle=_fake_lifecycle(),
        provider_factory=lambda m, h: FakeProvider(m),
        cloud_handler=None,
    )
    result = orch.handle("what is the latest news about AI", laptop=LAPTOP_UP)

    assert result.tier is Tier.LAPTOP
    assert any("not configured" in n for n in result.notices)


def test_cloud_handler_is_used_when_present() -> None:
    orch = Orchestrator(
        router=TierRouter(allow_cloud=True),
        lifecycle=_fake_lifecycle(),
        provider_factory=lambda m, h: FakeProvider(m),
        cloud_handler=lambda task: "cloud says hello",
    )
    result = orch.handle("what is the latest news about AI", laptop=LAPTOP_UP)

    assert result.tier is Tier.CLOUD
    assert result.went_online is True
    assert result.reply == "cloud says hello"
    assert any("logged" in n.lower() for n in result.notices)


def test_going_online_is_always_announced() -> None:
    orch = Orchestrator(
        router=TierRouter(allow_cloud=True),
        lifecycle=_fake_lifecycle(),
        provider_factory=lambda m, h: FakeProvider(m),
        cloud_handler=lambda t: "x",
    )
    result = orch.handle("search the web for python tutorials", laptop=LAPTOP_UP)

    assert result.went_online is True
    assert result.notices, "going online must never be silent"


def test_empty_task_rejected() -> None:
    orch = Orchestrator(lifecycle=_fake_lifecycle())

    with pytest.raises(ValueError):
        orch.handle("   ")


def test_record_captures_the_attempt() -> None:
    orch = Orchestrator(
        lifecycle=_fake_lifecycle(),
        provider_factory=lambda m, h: FakeProvider(m),
    )
    result = orch.handle("hey", laptop=LAPTOP_UP)

    assert len(result.record.attempts) == 1
    assert result.record.attempts[0].ok is True
    assert result.record.final_tier is Tier.PI_LOCAL


def test_explain_is_a_dry_run() -> None:
    used: list[str] = []

    orch = Orchestrator(
        lifecycle=_fake_lifecycle(),
        provider_factory=lambda m, h: (used.append(m), FakeProvider(m))[1],
    )
    text = orch.explain("build me a react website", laptop=LAPTOP_UP)

    assert "tier" in text
    assert "complexity" in text
    assert used == [], "explain() must not run the model"


def test_summary_is_human_readable() -> None:
    orch = Orchestrator(
        lifecycle=_fake_lifecycle(),
        provider_factory=lambda m, h: FakeProvider(m),
    )
    result = orch.handle("hey", laptop=LAPTOP_UP)

    assert "Pi" in result.summary()
