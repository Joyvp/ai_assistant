"""Tests for the Ollama Brain provider.

All network traffic is mocked, so these run without Ollama installed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from apexis_desktop.brain.base import BrainProvider
from apexis_desktop.brain.ollama import OllamaError, OllamaProvider


def _stream_body(*pieces: str, done: bool = True) -> bytes:
    """Build an Ollama-style newline-delimited JSON stream."""
    lines = [
        json.dumps({"message": {"role": "assistant", "content": p}, "done": False})
        for p in pieces
    ]
    if done:
        lines.append(json.dumps({"message": {"content": ""}, "done": True}))
    return ("\n".join(lines) + "\n").encode()


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# -- contract --------------------------------------------------------------


def test_implements_brain_provider_contract() -> None:
    provider = OllamaProvider(client=_client(lambda r: httpx.Response(200)))

    assert isinstance(provider, BrainProvider)
    assert "phi3:mini" in provider.name


def test_rejects_empty_message() -> None:
    provider = OllamaProvider(client=_client(lambda r: httpx.Response(200)))

    with pytest.raises(ValueError):
        provider.respond("   ")


# -- generation ------------------------------------------------------------


def test_respond_joins_streamed_chunks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        return httpx.Response(200, content=_stream_body("Hello", " ", "world"))

    provider = OllamaProvider(client=_client(handler))

    assert provider.respond("hi") == "Hello world"


def test_stream_yields_incrementally() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_stream_body("a", "b", "c"))

    provider = OllamaProvider(client=_client(handler))

    assert list(provider.stream("hi")) == ["a", "b", "c"]


def test_request_includes_system_prompt_and_keep_alive() -> None:
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, content=_stream_body("ok"))

    provider = OllamaProvider(client=_client(handler), keep_alive="10m")
    provider.respond("hello")

    assert captured["keep_alive"] == "10m"
    assert captured["stream"] is True
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][-1] == {"role": "user", "content": "hello"}


# -- conversation memory ---------------------------------------------------


def test_history_carries_between_turns() -> None:
    seen: list[list[dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["messages"])
        return httpx.Response(200, content=_stream_body("reply"))

    provider = OllamaProvider(client=_client(handler))
    provider.respond("first")
    provider.respond("second")

    # Second call must carry the first exchange.
    assert len(seen[0]) == 2   # system + user
    assert len(seen[1]) == 4   # system + user + assistant + user
    assert seen[1][1] == {"role": "user", "content": "first"}
    assert seen[1][2] == {"role": "assistant", "content": "reply"}


def test_reset_clears_history() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_stream_body("x"))

    provider = OllamaProvider(client=_client(handler))
    provider.respond("one")
    assert provider.turns == 2

    provider.reset()
    assert provider.turns == 0


def test_history_not_updated_on_empty_reply() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_stream_body(""))

    provider = OllamaProvider(client=_client(handler))
    provider.respond("hello")

    assert provider.turns == 0


# -- failure modes ---------------------------------------------------------


def test_missing_model_gives_actionable_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    provider = OllamaProvider(client=_client(handler), model="nope:1b")

    with pytest.raises(OllamaError, match="ollama pull nope:1b"):
        provider.respond("hi")


def test_connection_error_names_the_host() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = OllamaProvider(client=_client(handler))

    with pytest.raises(OllamaError, match="ollama serve"):
        provider.respond("hi")


def test_malformed_json_lines_are_skipped() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = (
            json.dumps({"message": {"content": "good"}, "done": False})
            + "\n{ this is not json\n"
            + json.dumps({"message": {"content": " end"}, "done": True})
            + "\n"
        )
        return httpx.Response(200, content=body.encode())

    provider = OllamaProvider(client=_client(handler))

    assert provider.respond("hi") == "good end"


# -- availability ----------------------------------------------------------


def test_is_available_true_when_tags_respond() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": []})

    provider = OllamaProvider(client=_client(handler))

    assert provider.is_available() is True


def test_is_available_false_when_refused() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    provider = OllamaProvider(client=_client(handler))

    assert provider.is_available() is False


def test_installed_models_lists_names() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"models": [{"name": "phi3:mini"}, {"name": "llama3.2:1b"}]},
        )

    provider = OllamaProvider(client=_client(handler))

    assert provider.installed_models() == ["phi3:mini", "llama3.2:1b"]
