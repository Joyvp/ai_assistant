"""Tests for persistent memory."""

from __future__ import annotations

import json

import httpx
import pytest

from apexis_desktop.brain.ollama import OllamaProvider
from apexis_desktop.memory import Memory, default_db_path


@pytest.fixture
def mem(tmp_path) -> Memory:
    with Memory(tmp_path / "test.db") as m:
        yield m


# -- facts -----------------------------------------------------------------


def test_remember_and_list(mem: Memory) -> None:
    mem.remember("my project is called APEXIS")

    facts = mem.facts()
    assert len(facts) == 1
    assert facts[0].text == "my project is called APEXIS"
    assert facts[0].source == "user"


def test_remember_strips_whitespace(mem: Memory) -> None:
    fact = mem.remember("   spaced out   ")

    assert fact.text == "spaced out"


def test_remember_rejects_empty(mem: Memory) -> None:
    with pytest.raises(ValueError):
        mem.remember("   ")


def test_facts_are_ordered_oldest_first(mem: Memory) -> None:
    mem.remember("first")
    mem.remember("second")
    mem.remember("third")

    assert [f.text for f in mem.facts()] == ["first", "second", "third"]


def test_forget_removes_one(mem: Memory) -> None:
    a = mem.remember("keep me")
    b = mem.remember("delete me")

    assert mem.forget(b.id) is True
    assert [f.text for f in mem.facts()] == ["keep me"]
    assert mem.forget(a.id) is True


def test_forget_unknown_id_returns_false(mem: Memory) -> None:
    assert mem.forget(999) is False


def test_forget_all(mem: Memory) -> None:
    mem.remember("one")
    mem.remember("two")

    assert mem.forget_all() == 2
    assert mem.facts() == []


def test_search_is_case_insensitive(mem: Memory) -> None:
    mem.remember("My Project Is APEXIS")
    mem.remember("I live in Saskatoon")

    assert len(mem.search_facts("apexis")) == 1
    assert len(mem.search_facts("SASKATOON")) == 1
    assert mem.search_facts("nothing") == []


# -- persistence -----------------------------------------------------------


def test_facts_survive_reopen(tmp_path) -> None:
    path = tmp_path / "persist.db"

    with Memory(path) as first:
        first.remember("my project is called APEXIS")

    with Memory(path) as second:
        assert [f.text for f in second.facts()] == ["my project is called APEXIS"]


def test_messages_survive_reopen(tmp_path) -> None:
    path = tmp_path / "persist.db"

    with Memory(path) as first:
        first.log("s1", "user", "hello")
        first.log("s1", "assistant", "hi")

    with Memory(path) as second:
        recent = second.recent("s1")
        assert [m.content for m in recent] == ["hello", "hi"]


def test_creates_parent_directories(tmp_path) -> None:
    path = tmp_path / "deep" / "nested" / "memory.db"

    with Memory(path) as m:
        m.remember("works")

    assert path.exists()


# -- transcript ------------------------------------------------------------


def test_log_and_recent(mem: Memory) -> None:
    mem.log("s1", "user", "first")
    mem.log("s1", "assistant", "reply")

    recent = mem.recent("s1")
    assert [m.role for m in recent] == ["user", "assistant"]


def test_recent_returns_oldest_first_within_limit(mem: Memory) -> None:
    for i in range(10):
        mem.log("s1", "user", f"msg{i}")

    recent = mem.recent("s1", limit=3)

    assert [m.content for m in recent] == ["msg7", "msg8", "msg9"]


def test_empty_messages_not_logged(mem: Memory) -> None:
    mem.log("s1", "user", "   ")

    assert mem.recent("s1") == []


def test_sessions_are_isolated(mem: Memory) -> None:
    mem.log("a", "user", "in a")
    mem.log("b", "user", "in b")

    assert [m.content for m in mem.recent("a")] == ["in a"]
    assert [m.content for m in mem.recent("b")] == ["in b"]


def test_last_session_is_most_recent(mem: Memory) -> None:
    mem.log("old", "user", "x")
    mem.log("new", "user", "y")

    assert mem.last_session() == "new"


def test_last_session_none_when_empty(mem: Memory) -> None:
    assert mem.last_session() is None


def test_clear_session_leaves_others(mem: Memory) -> None:
    mem.log("a", "user", "x")
    mem.log("b", "user", "y")

    assert mem.clear_session("a") == 1
    assert mem.recent("a") == []
    assert len(mem.recent("b")) == 1


def test_clearing_transcript_keeps_facts(mem: Memory) -> None:
    mem.remember("important")
    mem.log("s1", "user", "chatter")

    mem.clear_session("s1")

    assert len(mem.facts()) == 1


# -- prompt injection ------------------------------------------------------


def test_facts_block_empty_when_no_facts(mem: Memory) -> None:
    assert mem.facts_block() == ""


def test_facts_block_lists_every_fact(mem: Memory) -> None:
    mem.remember("project is APEXIS")
    mem.remember("lives in Saskatoon")

    block = mem.facts_block()

    assert "project is APEXIS" in block
    assert "lives in Saskatoon" in block
    assert block.count("\n- ") == 2


def test_provider_injects_facts_into_system_prompt(mem: Memory) -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        body = json.dumps({"message": {"content": "ok"}, "done": True}) + "\n"
        return httpx.Response(200, content=body.encode())

    mem.remember("my project is called APEXIS")

    provider = OllamaProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        memory=mem,
    )
    provider.respond("what is my project called?")

    system = captured["messages"][0]["content"]
    assert "APEXIS" in system


def test_new_facts_apply_without_restart(mem: Memory) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"][0]["content"])
        body = json.dumps({"message": {"content": "ok"}, "done": True}) + "\n"
        return httpx.Response(200, content=body.encode())

    provider = OllamaProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        memory=mem,
    )

    provider.respond("first")
    mem.remember("added mid-conversation")
    provider.respond("second")

    assert "added mid-conversation" not in seen[0]
    assert "added mid-conversation" in seen[1]


def test_provider_without_memory_still_works() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"message": {"content": "ok"}, "done": True}) + "\n"
        return httpx.Response(200, content=body.encode())

    provider = OllamaProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )

    assert provider.respond("hi") == "ok"


def test_broken_memory_does_not_break_chat() -> None:
    """Memory is a nice-to-have; generation must not depend on it."""

    class Exploding:
        def facts_block(self) -> str:
            raise RuntimeError("disk on fire")

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.dumps({"message": {"content": "ok"}, "done": True}) + "\n"
        return httpx.Response(200, content=body.encode())

    provider = OllamaProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        memory=Exploding(),
    )

    assert provider.respond("hi") == "ok"


# -- stats and paths -------------------------------------------------------


def test_stats_counts_everything(mem: Memory) -> None:
    mem.remember("a")
    mem.log("s1", "user", "x")
    mem.log("s2", "user", "y")

    stats = mem.stats()

    assert stats == {"facts": 1, "auto": 0, "messages": 2, "sessions": 2}


def test_db_path_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("APEXIS_DB", str(tmp_path / "custom.db"))

    assert default_db_path() == tmp_path / "custom.db"


def test_db_path_respects_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("APEXIS_DB", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))

    assert default_db_path() == tmp_path / "apexis" / "memory.db"


# -- first-person disambiguation -------------------------------------------


def test_facts_block_attributes_quotes_to_the_user(mem: Memory) -> None:
    """Regression: raw first-person facts confused the model about who "I" was.

    phi3:mini received 'I live in Saskatoon' in its system prompt and read the
    "I" as itself, answering with hedging like "You say you reside there, but
    that isn't clear." Quoting and attributing each fact fixes it.
    """
    mem.remember("I live in Saskatoon")

    block = mem.facts_block()

    assert 'The user said: "I live in Saskatoon"' in block
    assert "FACTS YOU KNOW ABOUT THE USER" in block


def test_facts_block_tells_model_not_to_hedge(mem: Memory) -> None:
    mem.remember("my name is Joy")

    block = mem.facts_block().lower()

    assert "directly" in block
    assert "clarify" in block
