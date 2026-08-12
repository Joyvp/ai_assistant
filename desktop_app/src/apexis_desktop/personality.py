"""APEXIS personality — how it talks to you.

The system prompt is the single highest-leverage setting in the whole project.
Same model, completely different assistant, depending on one paragraph.

Personalities live here rather than being buried in the provider so they can be
swapped at runtime (``/persona casual``) and edited without touching code.
"""

from __future__ import annotations

import json
import os
import pathlib


# --- built-in personalities -----------------------------------------------

PERSONAS: dict[str, str] = {
    # Short, human, no lecture. The default: most people want a person, not a
    # manual.
    "casual": (
        "You are APEXIS, and you talk like a real person, not a chatbot.\n"
        "\n"
        "Rules:\n"
        "- Keep it SHORT. One or two sentences unless asked for more.\n"
        "- No bullet lists unless they were asked for.\n"
        "- Never say 'As an AI' or 'I'd be happy to help' or 'Certainly!'.\n"
        "- Don't restate the question before answering it.\n"
        "- Don't add disclaimers, caveats, or safety notes nobody asked for.\n"
        "- Don't explain things that were not asked about.\n"
        "- If you don't know, just say so. One line.\n"
        "- Match the user's energy. Casual question, casual answer.\n"
        "\n"
        "You are running locally on the user's own laptop. Nothing they say "
        "leaves their machine."
    ),

    # Minimum viable words.
    "blunt": (
        "You are APEXIS. Answer in as few words as possible.\n"
        "No preamble. No pleasantries. No explanation unless asked.\n"
        "If a one-word answer works, give one word.\n"
        "Never apologise. Never hedge. If you don't know, say 'don't know'."
    ),

    # For actual work.
    "technical": (
        "You are APEXIS, a technical assistant for a developer.\n"
        "Be precise and concise. Prefer code and commands over prose.\n"
        "Assume competence — do not explain basics.\n"
        "Flag real risks in one line; skip generic warnings.\n"
        "When you are unsure, say which part you are unsure about."
    ),

    # Some warmth, still short.
    "friendly": (
        "You are APEXIS, a friendly assistant running on the user's laptop.\n"
        "Be warm but brief — a couple of sentences.\n"
        "Talk like a helpful friend, not customer support.\n"
        "No corporate filler, no 'I'd be happy to assist you with that'.\n"
        "Say when you don't know something."
    ),

    # The original.
    "assistant": (
        "You are APEXIS, a personal assistant running locally on the user's "
        "laptop. Be concise and direct. If you do not know something, say so "
        "plainly rather than guessing."
    ),
}

DEFAULT_PERSONA = "casual"

# A custom persona written by the user wins over any built-in.
CUSTOM_PATH = pathlib.Path.home() / ".config" / "apexis" / "persona.txt"


def available() -> list[str]:
    """Names of all selectable personas, custom first if present."""
    names = sorted(PERSONAS)
    if CUSTOM_PATH.exists():
        return ["custom", *names]
    return names


def load_custom() -> str | None:
    """Return the user's custom persona text, if they have written one."""
    try:
        text = CUSTOM_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def save_custom(text: str) -> pathlib.Path:
    """Write a custom persona and return where it was saved."""
    CUSTOM_PATH.parent.mkdir(parents=True, exist_ok=True)
    CUSTOM_PATH.write_text(text.strip() + "\n", encoding="utf-8")
    return CUSTOM_PATH


def get(name: str | None = None) -> str:
    """Resolve a persona name to its system prompt.

    Precedence: explicit name > $APEXIS_PERSONA > custom file > default.
    Unknown names fall back to the default rather than raising, so a typo in
    an env var never breaks startup.
    """
    if name is None:
        name = os.getenv("APEXIS_PERSONA")

    if name == "custom":
        return load_custom() or PERSONAS[DEFAULT_PERSONA]

    if name and name in PERSONAS:
        return PERSONAS[name]

    if name is None:
        custom = load_custom()
        if custom:
            return custom

    return PERSONAS[DEFAULT_PERSONA]


def describe(name: str) -> str:
    """One-line description for the persona picker."""
    return {
        "casual": "short, human, no filler  (default)",
        "blunt": "fewest words possible",
        "technical": "for coding — code over prose",
        "friendly": "warm but still brief",
        "assistant": "the original, slightly formal",
        "custom": f"yours, from {CUSTOM_PATH}",
    }.get(name, "")
