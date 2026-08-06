"""Tests for Brain providers and the chat loop."""

from apexis_desktop.brain.base import BrainProvider
from apexis_desktop.brain.mock import MockProvider
from apexis_desktop.chat import run_chat


def test_mock_provider_implements_contract() -> None:
    provider = MockProvider()

    assert isinstance(provider, BrainProvider)
    assert provider.name == "MockProvider"
    assert provider.respond(" hello ") == "Mock Brain received: hello"


def test_chat_responds_and_exits_without_persistence() -> None:
    messages = iter(["Hello APEXIS", "/exit"])
    output: list[str] = []

    result = run_chat(
        MockProvider(),
        input_func=lambda _: next(messages),
        output_func=output.append,
    )

    assert result == 0
    assert "APEXIS: Mock Brain received: Hello APEXIS" in output
    assert output[-1] == "Chat ended. No conversation was saved."


def test_chat_rejects_empty_input() -> None:
    messages = iter(["   ", "/exit"])
    output: list[str] = []

    run_chat(
        MockProvider(),
        input_func=lambda _: next(messages),
        output_func=output.append,
    )

    assert "APEXIS: Please enter a message or /exit." in output
