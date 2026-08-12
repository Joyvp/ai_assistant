"""Model lifecycle — load on demand, unload to reclaim RAM.

On an 8GB laptop the model is the single biggest consumer of memory. phi3:mini
occupies roughly 2.5GB while resident, which is the difference between a
comfortable desktop and swapping.

So APEXIS treats the model as a *resource to be borrowed*, not a service that
runs forever:

    load()      pull the model into RAM (slow: seconds)
    generate()  use it                  (fast, while resident)
    unload()    give the RAM back       (immediate)

Ollama already implements this via ``keep_alive``. What this module adds is:

*   **explicit control** — unload the moment we know we are finished, rather
    than waiting out a timer
*   **visibility** — report what is resident and how much RAM it costs
*   **guaranteed release** — a context manager so a crash mid-task still frees
    memory

Sending ``keep_alive: 0`` with an empty prompt is Ollama's documented way to
unload immediately.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class LoadedModel:
    """A model currently resident in memory."""

    name: str
    size_bytes: int
    expires_at: str | None = None

    @property
    def size_mb(self) -> int:
        return self.size_bytes // (1024 * 1024)

    @property
    def size_gb(self) -> float:
        return round(self.size_bytes / (1024**3), 2)


class ModelLifecycle:
    """Load, inspect and unload Ollama models on demand."""

    def __init__(
        self,
        *,
        host: str = "http://127.0.0.1:11434",
        client: httpx.Client | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout, trust_env=False)
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    # -- inspection --------------------------------------------------------

    def resident(self) -> list[LoadedModel]:
        """Return the models currently loaded in RAM.

        Mirrors ``ollama ps``.
        """
        try:
            response = self.client.get(f"{self.host}/api/ps", timeout=5.0)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError):
            return []

        return [
            LoadedModel(
                name=m.get("name", m.get("model", "?")),
                size_bytes=m.get("size", 0),
                expires_at=m.get("expires_at"),
            )
            for m in payload.get("models", [])
        ]

    def is_resident(self, model: str) -> bool:
        base = model.split(":")[0]
        return any(m.name.split(":")[0] == base for m in self.resident())

    def resident_mb(self) -> int:
        """Total RAM currently held by loaded models."""
        return sum(m.size_mb for m in self.resident())

    # -- control -----------------------------------------------------------

    def load(self, model: str, *, keep_alive: str = "5m") -> float:
        """Load ``model`` into RAM. Returns seconds taken.

        Ollama loads a model when sent an empty prompt, without generating
        anything.
        """
        started = time.perf_counter()

        try:
            response = self.client.post(
                f"{self.host}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": keep_alive},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"could not load {model}: {exc}") from exc

        return time.perf_counter() - started

    def unload(self, model: str) -> bool:
        """Unload ``model`` immediately, freeing its RAM.

        ``keep_alive: 0`` is Ollama's documented immediate-unload signal.
        """
        try:
            response = self.client.post(
                f"{self.host}/api/generate",
                json={"model": model, "prompt": "", "keep_alive": 0},
                timeout=30.0,
            )
            response.raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    def unload_all(self) -> int:
        """Unload every resident model. Returns how many were unloaded."""
        freed = 0
        for m in self.resident():
            if self.unload(m.name):
                freed += 1
        return freed

    # -- scoped borrowing --------------------------------------------------

    @contextmanager
    def borrowed(
        self,
        model: str,
        *,
        keep_alive: str = "5m",
        unload_after: bool = True,
    ) -> Iterator[LoadedModel | None]:
        """Load a model for the duration of a block, then release it.

            with lifecycle.borrowed("phi3:mini"):
                reply = provider.respond("...")
            # RAM released here, even if the block raised

        ``unload_after=False`` leaves the model resident, which is the right
        choice when more work is expected shortly.
        """
        was_resident = self.is_resident(model)

        if not was_resident:
            self.load(model, keep_alive=keep_alive)

        loaded = next(
            (m for m in self.resident() if m.name.split(":")[0] == model.split(":")[0]),
            None,
        )

        try:
            yield loaded
        finally:
            # Only unload if we were the ones who loaded it. Something else
            # may have been mid-conversation with an already-resident model.
            if unload_after and not was_resident:
                self.unload(model)
