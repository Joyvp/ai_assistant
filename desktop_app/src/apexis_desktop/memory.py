"""Persistent memory — facts and conversation that survive restarts.

Two distinct kinds of memory, deliberately kept separate:

*   **Facts** — things you explicitly told APEXIS to remember. These are
    injected into the system prompt on every request, so the model always
    knows them. Small, curated, permanent until you delete them.

*   **Messages** — the running transcript. Useful for continuity ("what were
    we just talking about?") and for review, but *not* injected wholesale;
    only the most recent turns are replayed.

**Never silent.** Master spec §15 excludes *silent* memory creation, not
automatic memory creation. Facts can now be captured automatically (see
``capture.py``), but every automatic save is announced on screen the moment
it happens, is tagged ``source='auto'`` so it can be listed and undone
separately, and can be switched off. The transcript is logged, but the model
is not told to treat it as truth about you.

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


SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    text        TEXT    NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'user',
    created_at  TEXT    NOT NULL,
    slot        TEXT
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
    slot: str | None = None

    @property
    def auto(self) -> bool:
        """True if APEXIS noticed this itself rather than being told."""
        return self.source == "auto"

    @property
    def when(self) -> str:
        """Short human date, e.g. '2026-08-12'."""
        return self.created_at[:10]


def _fact(row: sqlite3.Row) -> Fact:
    return Fact(
        row["id"], row["text"], row["source"], row["created_at"], row["slot"]
    )


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
        self._migrate()
        self._conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def _migrate(self) -> None:
        """Bring an older database up to the current schema, in place.

        v1 databases predate the ``slot`` column. Adding it is additive and
        keeps every existing fact, so upgrading never costs the user memory.
        """
        columns = {
            row["name"] for row in self._conn.execute("PRAGMA table_info(facts)")
        }
        if "slot" not in columns:
            self._conn.execute("ALTER TABLE facts ADD COLUMN slot TEXT")
            self._backfill_slots()
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    def _backfill_slots(self) -> None:
        """Work out which existing facts occupy an identity slot.

        Without this, a fact stored before slots existed ("I live in
        Saskatoon") would never be replaced when the user moves, leaving two
        contradictory homes in the prompt. Each old fact is run back through
        the capture rules; if it looks like a slotted fact, it gets the slot.
        """
        from apexis_desktop import capture

        rows = self._conn.execute("SELECT id, text FROM facts").fetchall()
        for row in rows:
            candidates = capture.extract(row["text"])
            if candidates and candidates[0].key:
                self._conn.execute(
                    "UPDATE facts SET slot = ? WHERE id = ?",
                    (candidates[0].key, row["id"]),
                )

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

    def remember(
        self, text: str, *, source: str = "user", slot: str | None = None
    ) -> Fact:
        """Store a fact. Raises ValueError on empty input.

        A ``slot`` marks the fact as singular — you have one name and one
        home — so storing a new fact in an occupied slot replaces what was
        there instead of piling up contradictions.
        """
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("cannot remember an empty fact")

        created = _now()
        with self._write() as conn:
            if slot:
                conn.execute("DELETE FROM facts WHERE slot = ?", (slot,))
            cursor = conn.execute(
                "INSERT INTO facts (text, source, created_at, slot) "
                "VALUES (?, ?, ?, ?)",
                (cleaned, source, created, slot),
            )
        return Fact(cursor.lastrowid, cleaned, source, created, slot)

    def facts(self) -> list[Fact]:
        """All remembered facts, oldest first."""
        rows = self._conn.execute(
            "SELECT id, text, source, created_at, slot FROM facts ORDER BY id"
        ).fetchall()
        return [_fact(r) for r in rows]

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

    def has_fact(self, text: str) -> bool:
        """True if this exact fact is already stored (case-insensitive)."""
        row = self._conn.execute(
            "SELECT 1 FROM facts WHERE lower(text) = ? LIMIT 1",
            (text.strip().lower(),),
        ).fetchone()
        return row is not None

    def absorb(self, message: str) -> list[Fact]:
        """Notice and store any durable facts in something the user said.

        Returns the facts that were actually stored — usually none. Callers
        must announce whatever comes back; automatic memory that the user
        cannot see is exactly what §15 rules out.
        """
        if not self.auto_capture:
            return []

        from apexis_desktop import capture

        stored: list[Fact] = []
        for candidate in capture.extract(message):
            if self.has_fact(candidate.text):
                continue
            stored.append(
                self.remember(candidate.text, source="auto", slot=candidate.key)
            )
        return stored

    # -- settings ----------------------------------------------------------

    def get_setting(self, key: str, default: str = "") -> str:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._write() as conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    @property
    def auto_capture(self) -> bool:
        """Whether APEXIS saves facts without being asked. On by default."""
        return self.get_setting("auto_capture", "on") == "on"

    @auto_capture.setter
    def auto_capture(self, enabled: bool) -> None:
        self.set_setting("auto_capture", "on" if enabled else "off")

    def search_facts(self, term: str) -> list[Fact]:
        """Case-insensitive substring search over facts."""
        needle = f"%{term.strip().lower()}%"
        rows = self._conn.execute(
            "SELECT id, text, source, created_at, slot FROM facts "
            "WHERE lower(text) LIKE ? ORDER BY id",
            (needle,),
        ).fetchall()
        return [_fact(r) for r in rows]

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
            "\n\nBACKGROUND (reference only — do not bring these up):\n"
            f"{lines}\n"
            "These are true, but they are NOT the topic of conversation. "
            "Ignore them completely unless the user's message is directly "
            "asking about one of them. If the user says something casual "
            "like a greeting, just greet them back and say nothing about "
            "this list. When they DO ask, answer plainly from it without "
            "hedging."
        )

    def stats(self) -> dict[str, int]:
        """Counts, for the /memory command."""
        facts = self._conn.execute("SELECT COUNT(*) AS n FROM facts").fetchone()["n"]
        msgs = self._conn.execute("SELECT COUNT(*) AS n FROM messages").fetchone()["n"]
        sessions = self._conn.execute(
            "SELECT COUNT(DISTINCT session) AS n FROM messages"
        ).fetchone()["n"]
        auto = self._conn.execute(
            "SELECT COUNT(*) AS n FROM facts WHERE source = 'auto'"
        ).fetchone()["n"]
        return {
            "facts": facts,
            "auto": auto,
            "messages": msgs,
            "sessions": sessions,
        }
