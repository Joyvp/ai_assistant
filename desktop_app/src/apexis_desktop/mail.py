"""Sending mail — with two very different sets of rules.

There are two kinds of message APEXIS can send, and conflating them is how
an assistant becomes a liability:

**To you.** A notification about your own machine, sent to your own address.
No permission needed. It only happens while you are away, because a machine
that emails you while you are sitting in front of it is just noise.

**To anyone else.** Never sent unattended. It goes in an outbox and waits
for you to approve it. The old implementation called ``input()`` here, which
blocks forever — so the one moment the feature mattered was the one moment
it could not work. An outbox is the same safety with none of the deadlock.

Credentials come from the environment or a chmod-600 config file, never from
the repository.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import smtplib
import ssl
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage

from apexis_desktop.away import config_dir


DEFAULT_HOST = "smtp.gmail.com"
DEFAULT_PORT = 587

CONFIG_PATH = config_dir() / "mail.json"
OUTBOX_PATH = config_dir() / "outbox.json"


class MailError(RuntimeError):
    """Sending failed, with a reason worth showing the user."""


# -- configuration ---------------------------------------------------------


def _read_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, ValueError):
        return {}


def _write_config(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2))
    try:
        CONFIG_PATH.chmod(0o600)
    except OSError:
        pass


def get_setting(key: str, env: str = "") -> str:
    """Environment beats the config file, so a shell export always wins."""
    if env:
        value = os.getenv(env)
        if value:
            return value
    return str(_read_config().get(key, ""))


def set_setting(key: str, value: str) -> None:
    config = _read_config()
    config[key] = value
    _write_config(config)


def sender() -> str:
    return get_setting("sender", "APEXIS_EMAIL_USER")


def password() -> str:
    return get_setting("password", "APEXIS_EMAIL_PASS")


def owner() -> str:
    """The user's own address — the only one that can be written to freely."""
    return get_setting("owner", "APEXIS_NOTIFY_EMAIL")


def host() -> str:
    return get_setting("host") or DEFAULT_HOST


def port() -> int:
    try:
        return int(get_setting("port") or DEFAULT_PORT)
    except ValueError:
        return DEFAULT_PORT


def is_configured() -> bool:
    return bool(sender() and password() and owner())


def missing() -> list[str]:
    """Which settings still need filling in."""
    gaps = []
    if not sender():
        gaps.append("sender")
    if not password():
        gaps.append("password")
    if not owner():
        gaps.append("owner")
    return gaps


_ADDRESS = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def looks_like_address(value: str) -> bool:
    return bool(_ADDRESS.match(value.strip()))


def is_owner(address: str) -> bool:
    """True when this address is the user's own."""
    mine = owner().strip().lower()
    return bool(mine) and address.strip().lower() == mine


# -- the outbox ------------------------------------------------------------


@dataclass
class Pending:
    """A message to someone else, waiting for the user to approve it."""

    to: str
    subject: str
    body: str
    reason: str = ""
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def as_dict(self) -> dict:
        return {
            "to": self.to,
            "subject": self.subject,
            "body": self.body,
            "reason": self.reason,
            "created_at": self.created_at,
        }


def outbox() -> list[Pending]:
    try:
        raw = json.loads(OUTBOX_PATH.read_text())
    except (OSError, ValueError):
        return []
    items = []
    for entry in raw:
        try:
            items.append(Pending(**entry))
        except TypeError:
            continue
    return items


def _save_outbox(items: list[Pending]) -> None:
    OUTBOX_PATH.write_text(json.dumps([i.as_dict() for i in items], indent=2))


def queue(to: str, subject: str, body: str, reason: str = "") -> Pending:
    """Hold a message for approval. Never sends."""
    item = Pending(to=to, subject=subject, body=body, reason=reason)
    items = outbox()
    items.append(item)
    _save_outbox(items)
    return item


def discard(index: int) -> Pending | None:
    items = outbox()
    if not 0 <= index < len(items):
        return None
    removed = items.pop(index)
    _save_outbox(items)
    return removed


def clear_outbox() -> int:
    count = len(outbox())
    _save_outbox([])
    return count


# -- actually sending ------------------------------------------------------


def _deliver(to: str, subject: str, body: str, transport=None) -> None:
    """Hand one message to the SMTP server. Raises MailError on failure."""
    if not is_configured():
        raise MailError(
            "email is not set up yet — run: apexis email setup"
        )

    message = EmailMessage()
    message["From"] = sender()
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    if transport is not None:  # tests inject a fake
        transport(message)
        return

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP(host(), port(), timeout=30) as server:
            server.starttls(context=context)
            server.login(sender(), password())
            server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        # Quote the server verbatim. Guessing at the cause here is how a
        # wrong username spends an hour being debugged as a wrong password.
        detail = ""
        try:
            detail = exc.smtp_error.decode("utf-8", "replace").strip()
        except (AttributeError, UnicodeDecodeError):
            detail = str(exc)
        raise MailError(f"the mail server rejected the login.\n  {detail}") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"could not reach the mail server: {exc}") from exc


def try_send(to: str, subject: str, body: str, *, transport=None) -> str:
    """Send, returning "" on success or the reason it failed.

    ``notify`` deliberately hides failures so a broken mailbox cannot break
    a job. Setup needs the opposite: the actual reason, in full.
    """
    try:
        _deliver(to, subject, body, transport=transport)
    except MailError as exc:
        return str(exc)
    return ""


def notify(subject: str, body: str, *, transport=None) -> bool:
    """Email the user about their own machine.

    Needs no permission — it is their address and their computer. Returns
    False when email is not configured, rather than raising, because a
    missing notification must never take down the job it was reporting on.
    """
    if not is_configured():
        return False
    try:
        _deliver(owner(), subject, body, transport=transport)
    except MailError:
        return False
    return True


def send_to(
    to: str, subject: str, body: str, *, approved: bool = False, transport=None
) -> tuple[bool, str]:
    """Send to any address.

    Anyone other than the owner requires ``approved=True``, which only the
    interactive approval path sets. Unapproved messages are queued instead
    of prompting, because the prompt would come while nobody is home.

    Returns ``(sent, message_for_the_user)``.
    """
    if not looks_like_address(to):
        return False, f"{to!r} does not look like an email address"

    if is_owner(to) or approved:
        _deliver(to, subject, body, transport=transport)
        return True, f"sent to {to}"

    queue(to, subject, body, reason="waiting for your approval")
    return False, (
        f"{to} is not you, so nothing was sent. It is in the outbox — "
        f"review it with: apexis email outbox"
    )


# -- what a finished job looks like in an inbox ----------------------------


def job_report(job) -> tuple[str, str]:
    """Turn a finished research job into a subject and a body."""
    subject = f"[APEXIS] {job.question[:60]}"

    lines = [
        f"You asked: {job.question}",
        "",
        job.answer or "(no answer was produced)",
        "",
        "---",
    ]

    good = [s for s in job.sources if s.ok]
    bad = [s for s in job.sources if not s.ok]

    if good:
        lines.append("Read:")
        lines += [f"  - {s.title or s.url}" for s in good]
    if bad:
        lines.append("Could not read:")
        lines += [f"  - {s.url} ({s.error})" for s in bad]

    lines += [
        "",
        f"Answered by {job.answered_by or 'the local model'}.",
        f"Job {job.id}. Nothing left your network unless a notice above says so.",
    ]

    return subject, "\n".join(lines)
