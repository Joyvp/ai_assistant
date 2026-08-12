"""Tests for personality selection."""

from __future__ import annotations

import pytest

from apexis_desktop import personality
from apexis_desktop.brain.ollama import OllamaProvider


def test_default_is_casual() -> None:
    assert personality.get() == personality.PERSONAS["casual"]


def test_named_persona_returned() -> None:
    assert personality.get("blunt") == personality.PERSONAS["blunt"]


def test_unknown_name_falls_back_to_default() -> None:
    assert personality.get("nonsense") == personality.PERSONAS[
        personality.DEFAULT_PERSONA
    ]


def test_env_var_selects_persona(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APEXIS_PERSONA", "technical")

    assert personality.get() == personality.PERSONAS["technical"]


def test_bad_env_var_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APEXIS_PERSONA", "does-not-exist")

    assert personality.get() == personality.PERSONAS[personality.DEFAULT_PERSONA]


def test_custom_persona_file_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    custom = tmp_path / "persona.txt"
    custom.write_text("You are a pirate.\n")
    monkeypatch.setattr(personality, "CUSTOM_PATH", custom)
    monkeypatch.delenv("APEXIS_PERSONA", raising=False)

    assert personality.get() == "You are a pirate."
    assert "custom" in personality.available()


def test_explicit_name_beats_custom_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    custom = tmp_path / "persona.txt"
    custom.write_text("You are a pirate.\n")
    monkeypatch.setattr(personality, "CUSTOM_PATH", custom)

    assert personality.get("blunt") == personality.PERSONAS["blunt"]


def test_save_custom_roundtrip(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    custom = tmp_path / "nested" / "persona.txt"
    monkeypatch.setattr(personality, "CUSTOM_PATH", custom)

    personality.save_custom("  You only speak in haiku.  ")

    assert personality.load_custom() == "You only speak in haiku."


def test_empty_custom_file_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    custom = tmp_path / "persona.txt"
    custom.write_text("   \n")
    monkeypatch.setattr(personality, "CUSTOM_PATH", custom)

    assert personality.load_custom() is None


def test_every_persona_has_a_description() -> None:
    for name in personality.PERSONAS:
        assert personality.describe(name)


def test_casual_persona_bans_chatbot_filler() -> None:
    prompt = personality.PERSONAS["casual"].lower()

    assert "as an ai" in prompt
    assert "short" in prompt


def test_provider_uses_persona_by_default() -> None:
    provider = OllamaProvider()

    assert provider.system_prompt == personality.PERSONAS["casual"]


def test_provider_accepts_explicit_prompt() -> None:
    provider = OllamaProvider(system_prompt="You are a cat.")

    assert provider.system_prompt == "You are a cat."
