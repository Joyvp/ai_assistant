"""Deterministic Brain provider used for development and tests."""


class MockProvider:
    """Echo a predictable response without model or network access."""

    @property
    def name(self) -> str:
        return "MockProvider"

    def respond(self, message: str) -> str:
        cleaned = message.strip()
        if not cleaned:
            raise ValueError("message cannot be empty")
        return f"Mock Brain received: {cleaned}"
