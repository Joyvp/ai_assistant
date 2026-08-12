"""Persistent memory — facts and conversation that survive restarts.

Two distinct kinds of memory, deliberately kept separate:

*   **Facts** — things you explicitly told APEXIS to remember. These are
    injected into the system prompt on every request, so the model always
    knows them. Small, curated, permanent until you delete them.

*   **Messages** — the running transcript. Useful for continuity ("what were
    we just talking about?") and for review, but *not* injected wholesale;
    only the most recent turns are replayed.

**No silent memory.** Master spec §15 lists "silent memory creation" as
excluded from V1, so nothing is remembered as a fact unless you say
``/remember``. The transcript is logged, but the model is not told to treat
it as truth about you.

Storage is SQLite at ``~/.local/share/apexis/memory.db`` — one file, no
server, trivially backed up, and it moves to the Pi in Phase 5 without the
callers changing.
"""

from __future__ import annotations

import os
import pathlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone


SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'user',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session     TEXT    NOT NULL,
    role        TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session
    ON messages (session, id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def default_db_path() -> pathlib.Path:
    """Where memory lives, honouring XDG."""
    if override := os.getenv("APEXIS_DB"):
        return pathlib.Path(override).expanduser()

    base = os.getenv("XDG_DATA_HOME")
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".local" / "share"
    return root / "apexis" / "memory.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class Fact:
    """Something the user explicitly asked APEXIS to remember."""

    id: int
    text: str
    source: str
    created_at: str

    @property
    def when(self) -> str:
        """Short human date, e.g. '2026-08-12'."""
        return self.created_at[:10]


@dataclass(frozen=True)
class Message:
    """One line of transcript."""

    id: int
    session: str
    role: str
    content: str
    created_at: str


class Memory:
    """SQLite-backed store for facts and conversation history."""

    def __init__(self, path: pathlib.Path | str | None = None) -> None:
        self.path = pathlib.Path(path) if path else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # Survive an ungraceful shutdown without corrupting the file — which
        # matters on a laptop that has already lost a drive once.
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Memory:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # -- facts -------------------------------------------------------------

    def remember(self, text: str, *, source: str = "user") -> Fact:
        """Store a fact. Raises ValueError on empty input."""
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("cannot remember an empty fact")

        created = _now()
        with self._write() as conn:
            cursor = conn.execute(
                "INSERT INTO facts (text, source, created_at) VALUES (?, ?, ?)",
                (cleaned, source, created),
            )
        return Fact(cursor.lastrowid, cleaned, source, created)

    def facts(self) -> list[Fact]:
        """All remembered facts, oldest first."""
        rows = self._conn.execute(
            "SELECT id, text, source, created_at FROM facts ORDER BY id"
        ).fetchall()
        return [Fact(r["id"], r["text"], r["source"], r["created_at"]) for r in rows]

    def forget(self, fact_id: int) -> bool:
        """Delete one fact. Returns False if it did not exist."""
        with self._write() as conn:
            cursor = conn.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        return cursor.rowcount > 0

    def forget_all(self) -> int:
        """Delete every fact. Returns how many were removed."""
        with self._write() as conn:
            cursor = conn.execute("DELETE FROM facts")
        return cursor.rowcount

    def search_facts(self, term: str) -> list[Fact]:
        """Case-insensitive substring search over facts."""
        needle = f"%{term.strip().lower()}%"
        rows = self._conn.execute(
            "SELECT id, text, source, created_at FROM facts "
            "WHERE lower(text) LIKE ? ORDER BY id",
            (needle,),
        ).fetchall()
        return [Fact(r["id"], r["text"], r["source"], r["created_at"]) for r in rows]

    # -- transcript --------------------------------------------------------

    def log(self, session: str, role: str, content: str) -> None:
        """Append a message to the transcript. Empty content is ignored."""
        cleaned = content.strip()
        if not cleaned:
            return

        with self._write() as conn:
            conn.execute(
                "INSERT INTO messages (session, role, content, created_at) "
                "VALUES (?, ?, ?, ?)",
                (session, role, cleaned, _now()),
            )

    def recent(self, session: str, limit: int = 20) -> list[Message]:
        """The last ``limit`` messages of a session, oldest first."""
        rows = self._conn.execute(
            "SELECT id, session, role, content, created_at FROM messages "
            "WHERE session = ? ORDER BY id DESC LIMIT ?",
            (session, limit),
        ).fetchall()
        return [
            Message(r["id"], r["session"], r["role"], r["content"], r["created_at"])
            for r in reversed(rows)
        ]

    def last_session(self) -> str | None:
        """The most recently used session id, if any."""
        row = self._conn.execute(
            "SELECT session FROM messages ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return row["session"] if row else None

    def sessions(self) -> list[str]:
        """Every session id, most recent first."""
        rows = self._conn.execute(
            "SELECT session, MAX(id) AS last FROM messages "
            "GROUP BY session ORDER BY last DESC"
        ).fetchall()
        return [r["session"] for r in rows]

    def clear_session(self, session: str) -> int:
        """Delete one session's transcript. Returns rows removed."""
        with self._write() as conn:
            cursor = conn.execute("DELETE FROM messages WHERE session = ?", (session,))
        return cursor.rowcount

    # -- prompt integration ------------------------------------------------

    def facts_block(self) -> str:
        """Facts formatted for injection into a system prompt.

        Returns an empty string when there is nothing to inject, so callers
        can concatenate unconditionally.
        """
        stored = self.facts()
        if not stored:
            return ""

        # Facts are stored verbatim, so they are usually written in first
        # person ("I live in Saskatoon"). Injected raw, a small model can
        # read that "I" as *itself* and get confused about who is who.
        # Quoting each line and labelling the speaker fixes that.
        lines = "\n".join(f'- The user said: "{f.text}"' for f in stored)
        return (
            "\n\nFACTS YOU KNOW ABOUT THE USER:\n"
            f"{lines}\n"
            "These are true. When the user asks about any of them, answer "
            "directly and plainly from this list. Do not say you are unsure "
            "and do not ask them to clarify."
        )

    def stats(self) -> dict[str, int]:
        """Counts, for the /memory command."""
        facts = self._conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
        msgs = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        sessions = self._conn.execute(
            "SELECT COUNT(DISTINCT session) AS n FROM messages"
        ).fetchone()["n"]
        return {"facts": facts, "messages": msgs, "sessions": sessions}
