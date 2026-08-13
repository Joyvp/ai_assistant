"""Tests for routing inside the chat loop.

The behaviour that matters here is not "does it pick a tier" — the router
tests cover that. It is: does the *conversation* survive being answered by
two different machines, and does a machine disappearing mid-sentence stay a
non-event.
"""

from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest

from apexis_desktop.brain.ollama import OllamaError
from apexis_desktop.nodes import Fleet, Node
from apexis_desktop.orchestrator import LAPTOP_MODEL, PI_MODEL
from apexis_desktop.routed_chat import HISTORY_TURNS, RoutedChat
from apexis_shared.routing import NodeCapability, Tier


class FakeProvider:
    """Records the exact message list it was handed."""

    seen: list[tuple[str, list[dict[str, str]]]] = []

    def __init__(self, model: str, host: str) -> None:
        self.model = model
        self.host = host

    def stream_messages(self, messages):
        FakeProvider.seen.append((self.model, messages))
        user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"), ""
        )
        for word in f"[{self.model}] {user}".split(" "):
            yield word + " "

    def close(self) -> None:
        pass


class DeadProvider(FakeProvider):
    def stream_messages(self, messages):
        raise OllamaError("cannot reach Ollama")
        yield  # pragma: no cover


class FakeLifecycle:
    def __init__(self, host: str) -> None:
        self.host = host
        self.loaded: list[str] = []
        self.unloaded: list[str] = []

    def resident_mb(self) -> int:
        return 0

    @contextmanager
    def borrowed(self, model: str, *, unload_after: bool = True):
        self.loaded.append(model)
        try:
            yield
        finally:
            if unload_after:
                self.unloaded.append(model)


@pytest.fixture(autouse=True)
def _clear():
    FakeProvider.seen = []


def _fleet(with_pi: bool = True) -> Fleet:
    return Fleet(
        laptop=Node("laptop", "127.0.0.1", role="laptop"),
        pi=Node("pi", "192.168.1.50") if with_pi else None,
    )


def _chat(
    with_pi: bool = True,
    provider=FakeProvider,
    lifecycles: dict | None = None,
    **kw,
) -> RoutedChat:
    fleet = _fleet(with_pi)
    store = lifecycles if lifecycles is not None else {}

    def make(host: str) -> FakeLifecycle:
        if host not in store:
            store[host] = FakeLifecycle(host)
        return store[host]

    chat = RoutedChat(
        fleet=fleet,
        provider_factory=provider,
        lifecycle_factory=make,
        **kw,
    )
    chat.laptop_capability = lambda: NodeCapability(  # type: ignore[method-assign]
        node="laptop", online=True, models=[LAPTOP_MODEL]
    )
    return chat


def _say(chat: RoutedChat, message: str) -> str:
    return "".join(chat.ask(message)).strip()


# -- picking a machine -----------------------------------------------------


def test_a_greeting_goes_to_the_pi():
    chat = _chat()
    plan = chat.route("hey")
    assert plan.tier is Tier.PI_LOCAL
    assert plan.model == PI_MODEL
    assert plan.where == "pi"


def test_real_work_goes_to_the_laptop():
    chat = _chat()
    plan = chat.route("refactor this function")
    assert plan.tier is Tier.LAPTOP
    assert plan.model == LAPTOP_MODEL
    assert plan.where == "laptop"


def test_routing_does_not_run_anything():
    chat = _chat()
    chat.route("hey")
    assert FakeProvider.seen == []


def test_without_a_pi_the_cheap_tier_runs_on_the_laptop():
    chat = _chat(with_pi=False)
    plan = chat.route("hey")
    assert plan.tier is Tier.PI_LOCAL  # the decision stands
    assert plan.where == "laptop"  # it is just served here
    assert plan.model == PI_MODEL


# -- the conversation survives the machines --------------------------------


def test_the_laptop_is_told_what_the_pi_said():
    """The point of holding history in the loop rather than the provider."""
    chat = _chat()
    _say(chat, "hey")
    _say(chat, "refactor this function")

    model, messages = FakeProvider.seen[-1]
    assert model == LAPTOP_MODEL
    joined = " ".join(m["content"] for m in messages)
    assert "hey" in joined


def test_the_pi_is_told_what_the_laptop_said():
    chat = _chat()
    _say(chat, "refactor this function")
    _say(chat, "thanks")

    model, messages = FakeProvider.seen[-1]
    assert model == PI_MODEL
    joined = " ".join(m["content"] for m in messages)
    assert "refactor this function" in joined


def test_history_records_which_machine_answered():
    chat = _chat()
    _say(chat, "hey")
    _say(chat, "refactor this function")

    assert [t.tier for t in chat.history] == [Tier.PI_LOCAL, Tier.LAPTOP]


def test_history_is_trimmed_so_a_small_model_is_not_flooded():
    chat = _chat()
    for i in range(HISTORY_TURNS + 4):
        _say(chat, f"hey {i}")

    _model, messages = FakeProvider.seen[-1]
    replayed = [m for m in messages if m["role"] in {"user", "assistant"}]
    # HISTORY_TURNS past exchanges (2 messages each) plus the new question.
    assert len(replayed) == HISTORY_TURNS * 2 + 1


def test_reset_clears_the_conversation():
    chat = _chat()
    _say(chat, "hey")
    chat.reset()
    assert chat.history == []
    assert chat.turns == 0


def test_an_empty_message_is_refused():
    chat = _chat()
    with pytest.raises(ValueError):
        list(chat.ask("   "))


def test_the_system_prompt_is_sent():
    chat = _chat(system_prompt="you are apexis")
    _say(chat, "hey")
    _model, messages = FakeProvider.seen[-1]
    assert messages[0]["role"] == "system"
    assert "you are apexis" in messages[0]["content"]


# -- memory ----------------------------------------------------------------


class FakeMemory:
    def __init__(self, block: str = "\n\nFACTS: lives in Saskatoon") -> None:
        self._block = block

    def facts_block(self) -> str:
        return self._block


class BrokenMemory:
    def facts_block(self) -> str:
        raise RuntimeError("database is on fire")


def test_remembered_facts_reach_whichever_machine_answers():
    chat = _chat(system_prompt="base", memory=FakeMemory())
    _say(chat, "refactor this function")
    _model, messages = FakeProvider.seen[-1]
    assert "Saskatoon" in messages[0]["content"]


def test_broken_memory_never_stops_the_reply():
    chat = _chat(system_prompt="base", memory=BrokenMemory())
    assert _say(chat, "hey")


# -- RAM -------------------------------------------------------------------


def test_the_pi_keeps_its_model_and_the_laptop_does_not():
    lifecycles: dict[str, FakeLifecycle] = {}
    chat = _chat(lifecycles=lifecycles)

    _say(chat, "hey")
    _say(chat, "refactor this function")

    pi = lifecycles["http://192.168.1.50:11434"]
    laptop = lifecycles["http://127.0.0.1:11434"]

    assert pi.loaded == [PI_MODEL]
    assert pi.unloaded == []
    assert laptop.loaded == [LAPTOP_MODEL]
    assert laptop.unloaded == [LAPTOP_MODEL]


def test_without_a_pi_nothing_is_held_resident():
    lifecycles: dict[str, FakeLifecycle] = {}
    chat = _chat(with_pi=False, lifecycles=lifecycles)
    _say(chat, "hey")

    laptop = lifecycles["http://127.0.0.1:11434"]
    assert laptop.unloaded == [PI_MODEL]


def test_unload_after_false_keeps_everything_loaded():
    lifecycles: dict[str, FakeLifecycle] = {}
    chat = _chat(lifecycles=lifecycles, unload_after=False)
    _say(chat, "refactor this function")

    assert lifecycles["http://127.0.0.1:11434"].unloaded == []


# -- a machine going away --------------------------------------------------


def _flaky(dead_model: str):
    """A provider factory where one model's machine is unreachable."""

    def make(model: str, host: str):
        return DeadProvider(model, host) if model == dead_model else FakeProvider(
            model, host
        )

    return make


def test_a_dead_pi_falls_back_to_the_laptop():
    chat = _chat(provider=_flaky(PI_MODEL))
    reply = _say(chat, "hey")
    assert LAPTOP_MODEL in reply


def test_the_fallback_is_announced():
    chat = _chat(provider=_flaky(PI_MODEL))
    plan = chat.route("hey")
    list(chat.ask("hey", plan))

    assert plan.fell_back is True
    assert any("did not answer" in n for n in plan.notices)


def test_the_fallback_still_lands_in_history():
    chat = _chat(provider=_flaky(PI_MODEL))
    _say(chat, "hey")
    assert len(chat.history) == 1
    assert chat.history[0].model == LAPTOP_MODEL


def test_a_dead_laptop_is_not_swallowed():
    """Nothing to fall back to — the error must surface."""
    chat = _chat(provider=_flaky(LAPTOP_MODEL))
    with pytest.raises(OllamaError):
        _say(chat, "refactor this function")


def test_a_dead_pi_does_not_poison_later_turns():
    chat = _chat(provider=_flaky(PI_MODEL))
    _say(chat, "hey")
    reply = _say(chat, "refactor this function")
    assert LAPTOP_MODEL in reply
    assert len(chat.history) == 2


def test_a_transport_error_also_falls_back():
    class Flaky(FakeProvider):
        def stream_messages(self, messages):
            if self.model == PI_MODEL:
                raise httpx.ConnectError("no route to host")
            yield from FakeProvider.stream_messages(self, messages)

    chat = _chat(provider=Flaky)
    assert LAPTOP_MODEL in _say(chat, "hey")


# -- probing ---------------------------------------------------------------


def test_probe_redirects_before_announcing_the_pi():
    """Do not print 'pi' and then quietly use the laptop."""
    chat = _chat()
    chat.fleet.pi.is_up = lambda *a, **k: False  # type: ignore[method-assign]

    plan = chat.route("hey", probe=True)
    assert plan.where == "laptop"
    assert plan.fell_back is True


def test_probe_leaves_a_healthy_pi_alone():
    chat = _chat()
    chat.fleet.pi.is_up = lambda *a, **k: True  # type: ignore[method-assign]

    plan = chat.route("hey", probe=True)
    assert plan.where == "pi"
    assert plan.fell_back is False


def test_a_pre_probed_fallback_runs_on_the_laptop():
    chat = _chat()
    chat.fleet.pi.is_up = lambda *a, **k: False  # type: ignore[method-assign]

    plan = chat.route("hey", probe=True)
    reply = "".join(chat.ask("hey", plan))
    assert LAPTOP_MODEL in reply
