"""Provider-neutral Brain interface."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class BrainProvider(Protocol):
    """Contract implemented by every APEXIS Brain provider."""

    @property
    def name(self) -> str:
        """Return the human-readable provider name."""

    def respond(self, message: str) -> str:
        """Return a response for one validated user message."""
