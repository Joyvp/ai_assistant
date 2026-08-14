"""Away mode — the switch that decides whether APEXIS should email you.

You are usually sitting in front of the machine, and a machine that emails
you while you are looking at it is spam. So the rule is yours: it writes to
you only when you have said you are out.

    apexis away              you are leaving
    apexis home              you are back

Everything else in APEXIS can ask ``is_away()`` and stay quiet the rest of
the time. The state is one small file, so a job that finishes at 3am on the
Pi can read it just as easily as the laptop can.
"""

from __future__ import annotations

import json
import os
import pathlib
from datetime import datetime, timezone


def config_dir() -> pathlib.Path:
    base = os.getenv("XDG_CONFIG_HOME")
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".config"
    path = root / "apexis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_path() -> pathlib.Path:
    return config_dir() / "away.json"


def _read() -> dict:
    try:
        return json.loads(state_path().read_text())
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    state_path().write_text(json.dumps(data, indent=2))


def is_away() -> bool:
    """True when the user has said they are out.

    Anything that might email, text or otherwise interrupt should check this
    first. Defaults to False: silence is the safe failure.
    """
    return bool(_read().get("away", False))


def leave(note: str = "") -> dict:
    """Mark the user as away. Returns the new state."""
    state = {
        "away": True,
        "since": datetime.now(timezone.utc).isoformat(),
        "note": note,
    }
    _write(state)
    return state


def arrive() -> dict:
    """Mark the user as back. Returns the state that just ended."""
    previous = _read()
    _write({"away": False, "returned": datetime.now(timezone.utc).isoformat()})
    return previous


def since() -> datetime | None:
    """When the user left, or None if they are home."""
    raw = _read().get("since")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def elapsed() -> str:
    """How long they have been away, in plain words."""
    start = since()
    if start is None:
        return ""

    seconds = (datetime.now(timezone.utc) - start).total_seconds()
    if seconds < 90:
        return "just now"
    minutes = seconds / 60
    if minutes < 90:
        return f"{int(minutes)} minutes ago"
    hours = minutes / 60
    if hours < 36:
        return f"{int(hours)} hours ago"
    return f"{int(hours / 24)} days ago"
