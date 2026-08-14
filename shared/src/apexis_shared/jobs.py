"""Prepared work — what the Pi hands the laptop.

The Pi is always on and the laptop is not. That asymmetry is worth more than
any model the Pi could run: at 3am, with the lid shut, the Pi can fetch pages,
pull the text out of them, split it up and throw away the duplicates. None of
that needs intelligence. It needs a machine that is awake.

Then the laptop opens, phi3 loads **once**, reads a tidy package, and thinks.
One model load instead of ten, over material that was gathered while nobody
was waiting.

This module is the contract between the two halves. It lives in ``shared``
because both machines need to agree on it, and it deliberately contains no
model code at all — a ``Job`` is just prepared text with a question attached.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JobState(str, Enum):
    """Where a job is in its life."""

    QUEUED = "queued"        # the Pi has work to do
    PREPARED = "prepared"    # the Pi is done; waiting for the laptop
    DONE = "done"            # the laptop has answered
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            JobState.QUEUED: "waiting on the Pi",
            JobState.PREPARED: "ready for the laptop",
            JobState.DONE: "answered",
            JobState.FAILED: "failed",
        }[self]


class Source(BaseModel):
    """One document the Pi fetched and cleaned up."""

    model_config = ConfigDict(extra="forbid")

    url: str
    title: str = ""
    text: str = ""
    fetched_at: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text) and not self.error

    @property
    def words(self) -> int:
        return len(self.text.split())

    @property
    def fingerprint(self) -> str:
        """Content hash, for spotting the same article on two sites."""
        normalised = " ".join(self.text.lower().split())
        return hashlib.sha256(normalised.encode()).hexdigest()[:16]


class Chunk(BaseModel):
    """A slice of a source, sized to fit a small model's context."""

    model_config = ConfigDict(extra="forbid")

    source_url: str
    index: int
    text: str

    @property
    def words(self) -> int:
        return len(self.text.split())


class Job(BaseModel):
    """A question plus the material needed to answer it.

    Created by whoever asks, filled in by the Pi, answered by the laptop.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    question: str
    urls: list[str] = Field(default_factory=list)
    state: JobState = JobState.QUEUED

    sources: list[Source] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)

    answer: str = ""
    answered_by: str = ""
    error: str = ""

    created_at: str = ""
    prepared_at: str = ""
    answered_at: str = ""

    # Where the request came from, so a notification can go back the same way.
    origin: Literal["cli", "email", "schedule"] = "cli"
    notify: str = ""

    def __init__(self, **data: object) -> None:
        super().__init__(**data)
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.id:
            seed = f"{self.question}{self.created_at}".encode()
            self.id = hashlib.sha256(seed).hexdigest()[:12]

    # -- reporting ---------------------------------------------------------

    @property
    def good_sources(self) -> list[Source]:
        return [s for s in self.sources if s.ok]

    @property
    def failed_sources(self) -> list[Source]:
        return [s for s in self.sources if not s.ok]

    @property
    def total_words(self) -> int:
        return sum(c.words for c in self.chunks)

    def summary(self) -> str:
        """One line describing the job's state."""
        if self.state is JobState.QUEUED:
            return f"{len(self.urls)} pages to fetch"

        if self.state is JobState.PREPARED:
            bits = [
                f"{len(self.good_sources)}/{len(self.sources)} pages",
                f"{len(self.chunks)} chunks",
                f"{self.total_words:,} words",
            ]
            return " · ".join(bits)

        if self.state is JobState.DONE:
            return f"answered by {self.answered_by or 'unknown'}"

        return self.error or "failed"

    def context(self, *, max_words: int = 2000) -> str:
        """The prepared material, formatted for a model prompt.

        Truncated by word count because the laptop's model has a finite
        context window and silently overflowing it produces confident
        nonsense.
        """
        parts: list[str] = []
        used = 0

        for chunk in self.chunks:
            if used + chunk.words > max_words:
                break
            source = next(
                (s for s in self.sources if s.url == chunk.source_url), None
            )
            heading = (source.title or source.url) if source else chunk.source_url
            parts.append(f"--- from: {heading} ---\n{chunk.text}")
            used += chunk.words

        return "\n\n".join(parts)

    def prompt(self, *, max_words: int = 2000) -> str:
        """The full instruction for the laptop's model."""
        material = self.context(max_words=max_words)
        if not material:
            return self.question

        return (
            "Answer the question using the material below. It was gathered "
            "from the listed pages. If the material does not cover something, "
            "say so rather than guessing.\n\n"
            f"{material}\n\n"
            f"QUESTION: {self.question}"
        )
