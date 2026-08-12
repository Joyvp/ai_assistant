"""Ollama-backed Brain provider — the real local model.

Talks to the Ollama HTTP API on localhost rather than shelling out to the
`ollama` binary. That buys us three things the subprocess approach cannot:

*   streaming tokens, so the reply appears as it is generated
*   a real multi-turn conversation, because we send the message history
*   no shell quoting bugs, and no chance of the prompt swallowing our flags

(The previous `RealProvider` passed ``--keep-alive`` *after* the prompt, so
Ollama treated the flag as part of the prompt text and the keep-alive was
never applied. Using the API sidesteps that class of bug entirely.)
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import httpx


DEFAULT_HOST = "http://127.0.0.1:11434"
DEFAULT_MODEL = "phi3:mini"

# 8GB Intel Mac: unload the model reasonably quickly so the desktop stays
# usable. Ollama's own default is 5m.
DEFAULT_KEEP_ALIVE = "5m"

# Personality lives in personality.py so it can be swapped at runtime.
from apexis_desktop import personality


class OllamaError(RuntimeError):
    """Raised when the local model cannot be reached or fails to respond."""


class OllamaProvider:
    """Stream responses from a locally running Ollama model.

    Implements the same ``name`` / ``respond`` contract as ``MockProvider``,
    so it is a drop-in replacement, plus a ``stream`` method for live output.
    """

    def __init__(
        self,
        *,
        model: str | None = None,
        host: str | None = None,
        keep_alive: str | None = None,
        system_prompt: str | None = None,
        timeout: float = 120.0,
        client: httpx.Client | None = None,
        memory: object | None = None,
    ) -> None:
        self.model = model or os.getenv("APEXIS_MODEL", DEFAULT_MODEL)
        self.host = (host or os.getenv("APEXIS_OLLAMA_HOST", DEFAULT_HOST)).rstrip("/")
        self.keep_alive = keep_alive or os.getenv(
            "APEXIS_KEEP_ALIVE", DEFAULT_KEEP_ALIVE
        )
        self.system_prompt = system_prompt or personality.get()

        # Optional Memory instance. When present, remembered facts are
        # appended to the system prompt on every request, so newly added
        # facts take effect immediately without a restart.
        self.memory = memory
        self.timeout = timeout

        self._client = client
        self._owns_client = client is None

        # Conversation history. This is what makes it a chat rather than a
        # series of unrelated one-shot prompts.
        self._history: list[dict[str, str]] = []

    # -- lifecycle ---------------------------------------------------------

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, trust_env=False)
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> OllamaProvider:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- contract ----------------------------------------------------------

    @property
    def name(self) -> str:
        return f"Ollama({self.model})"

    def is_available(self) -> bool:
        """Return True if the Ollama server answers."""
        try:
            response = self.client.get(f"{self.host}/api/tags", timeout=3.0)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    def installed_models(self) -> list[str]:
        """Return the models Ollama has pulled locally."""
        try:
            response = self.client.get(f"{self.host}/api/tags", timeout=5.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaError(f"could not list models: {exc}") from exc

        return [m["name"] for m in payload.get("models", [])]

    def reset(self) -> None:
        """Forget the conversation so far."""
        self._history.clear()

    @property
    def turns(self) -> int:
        """Number of messages currently held in context."""
        return len(self._history)

    # -- generation --------------------------------------------------------

    def _system_content(self) -> str:
        """System prompt plus any remembered facts."""
        if self.memory is None:
            return self.system_prompt

        try:
            block = self.memory.facts_block()
        except Exception:
            # Memory must never break generation.
            return self.system_prompt

        return f"{self.system_prompt}{block}" if block else self.system_prompt

    def _messages_for(self, message: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self._system_content()},
            *self._history,
            {"role": "user", "content": message},
        ]

    def stream(self, message: str) -> Iterator[str]:
        """Yield response chunks as the model produces them.

        The full reply is appended to the conversation history once the
        stream completes, so context carries into the next turn.
        """
        cleaned = message.strip()
        if not cleaned:
            raise ValueError("message cannot be empty")

        payload = {
            "model": self.model,
            "messages": self._messages_for(cleaned),
            "stream": True,
            "keep_alive": self.keep_alive,
        }

        collected: list[str] = []

        try:
            with self.client.stream(
                "POST", f"{self.host}/api/chat", json=payload
            ) as response:
                if response.status_code == 404:
                    raise OllamaError(
                        f"model {self.model!r} not found. "
                        f"Pull it first:  ollama pull {self.model}"
                    )
                response.raise_for_status()

                for line in response.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    piece = chunk.get("message", {}).get("content", "")
                    if piece:
                        collected.append(piece)
                        yield piece

                    if chunk.get("done"):
                        break

        except httpx.ConnectError as exc:
            raise OllamaError(
                f"cannot reach Ollama at {self.host}. Is it running? "
                "Start it with:  ollama serve"
            ) from exc
        except httpx.HTTPError as exc:
            raise OllamaError(f"request failed: {exc}") from exc

        reply = "".join(collected).strip()
        if reply:
            self._history.append({"role": "user", "content": cleaned})
            self._history.append({"role": "assistant", "content": reply})

    def respond(self, message: str) -> str:
        """Return the complete response as a single string.

        Satisfies the ``BrainProvider`` contract used by the existing chat
        loop and tests.
        """
        return "".join(self.stream(message)).strip()
