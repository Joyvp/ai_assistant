"""Taking instructions by email.

The worker already writes to the user. This is the way back: send APEXIS a
mail from anywhere and it queues the question, then answers it the next time
the timer runs.

Two things make this safe enough to leave running:

  * Only the owner's address is listened to. Everything else is ignored
    without being read.
  * The subject must begin with a trigger word. Without it, a normal email
    from the owner - a forwarded receipt, a reply to an answer - would
    become a job. The trigger is what separates "a message" from "an order".

A From header can be forged, and this file does not pretend otherwise.
What it does instead is refuse to do anything dangerous: mail can only queue
a question, and answers only ever go back to the owner's own address. The
worst a forged mail achieves is making the laptop read a web page and email
the user about it.

Nothing here mutates the mailbox. Messages are peeked at, never flagged, and
a local file remembers what has been handled - so APEXIS reading the mail
never changes what the user sees as unread.
"""

from __future__ import annotations

import email
import imaplib
import json
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr

from apexis_desktop import mail
from apexis_desktop.away import config_dir


DEFAULT_IMAP_HOST = "imap.gmail.com"
DEFAULT_IMAP_PORT = 993

SEEN_PATH = config_dir() / "inbox_seen.json"

# How far back to look. Anything older is somebody else's problem.
LOOKBACK_DAYS = 2

# Never let one poll turn into a hundred jobs.
MAX_PER_POLL = 10

# Remembering every message id forever would grow without limit.
MAX_REMEMBERED = 500

TRIGGER = "apexis"

_URL = re.compile(r"https?://\S+")


class InboxError(RuntimeError):
    """Reading the mailbox failed, with a reason worth showing."""


# -- settings --------------------------------------------------------------


def imap_host() -> str:
    return mail.get_setting("imap_host") or DEFAULT_IMAP_HOST


def imap_port() -> int:
    try:
        return int(mail.get_setting("imap_port") or DEFAULT_IMAP_PORT)
    except ValueError:
        return DEFAULT_IMAP_PORT


def is_enabled() -> bool:
    """Off unless the user turns it on. Listening is opt-in."""
    return str(mail.get_setting("inbox")).lower() in {"1", "true", "on", "yes"}


def set_enabled(on: bool) -> None:
    mail.set_setting("inbox", "on" if on else "off")


# -- what has already been handled ----------------------------------------


def _read_seen() -> list[str]:
    try:
        data = json.loads(SEEN_PATH.read_text())
    except (OSError, ValueError):
        return []
    return [str(x) for x in data] if isinstance(data, list) else []


def _remember(ids: list[str]) -> None:
    if not ids:
        return
    seen = _read_seen()
    seen.extend(i for i in ids if i not in seen)
    try:
        SEEN_PATH.write_text(json.dumps(seen[-MAX_REMEMBERED:], indent=2))
    except OSError:
        pass


def forget_all() -> int:
    """Clear the handled list, so recent mail is read again."""
    count = len(_read_seen())
    try:
        SEEN_PATH.unlink()
    except OSError:
        pass
    return count


# -- reading a message -----------------------------------------------------


def _decode(raw: str) -> str:
    """Subjects arrive MIME-encoded often enough to matter."""
    try:
        return str(make_header(decode_header(raw or "")))
    except Exception:
        return raw or ""


def _plain_text(message) -> str:
    """The readable part of a mail, ignoring HTML and attachments."""
    if not message.is_multipart():
        try:
            payload = message.get_payload(decode=True)
        except Exception:
            return ""
        if payload is None:
            return str(message.get_payload() or "")
        charset = message.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")

    for part in message.walk():
        if part.get_content_type() != "text/plain":
            continue
        if "attachment" in str(part.get("Content-Disposition", "")).lower():
            continue
        try:
            payload = part.get_payload(decode=True)
        except Exception:
            continue
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    return ""


def strip_quoted(body: str) -> str:
    """Drop the quoted history that clients staple under a reply.

    Without this, replying to an answer would queue that whole answer back
    as a new question.
    """
    lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(">"):
            break
        if stripped.startswith("--"):
            break
        low = stripped.lower()
        if low.startswith("on ") and low.endswith("wrote:"):
            break
        if low.startswith("sent from my"):
            break
        if stripped == "___" or stripped.startswith("____"):
            break
        lines.append(line)
    return "\n".join(lines).strip()


def parse_command(subject: str, body: str) -> tuple[str, list[str]]:
    """Turn a mail into (question, urls), or ("", []) if it is not a command.

    The trigger word may be followed by a colon or not, and the question can
    live in the subject, the body, or both. Someone typing on a phone should
    not have to remember a format.
    """
    subject = (subject or "").strip()
    head = subject.lower()
    for prefix in (TRIGGER + ":", TRIGGER):
        if head.startswith(prefix):
            subject = subject[len(prefix):].strip(" :\t")
            break
    else:
        return "", []

    body = strip_quoted(body or "")

    urls = _URL.findall(subject) + _URL.findall(body)
    urls = [u.rstrip(".,);]>") for u in urls]

    # De-duplicate while keeping the order the user wrote them in.
    seen: set[str] = set()
    ordered = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            ordered.append(u)

    question = _URL.sub("", subject).strip()
    if not question:
        question = _URL.sub("", body).strip()
        question = " ".join(question.split())

    return question, ordered


# -- talking to the server -------------------------------------------------


def _connect():  # pragma: no cover - patched in tests
    """Open an IMAP session. The SMTP app password works here too."""
    client = imaplib.IMAP4_SSL(imap_host(), imap_port())
    client.login(mail.sender(), mail.password())
    return client


def _since_token(days: int = LOOKBACK_DAYS) -> str:
    when = datetime.now(timezone.utc) - timedelta(days=days)
    return when.strftime("%d-%b-%Y")


def read_commands(*, client=None) -> list[dict]:
    """Every unhandled command sitting in the mailbox.

    Returns dicts of {id, question, urls, subject}. Raises InboxError if the
    mailbox cannot be read - the caller decides whether that is fatal.
    """
    owner = mail.owner().strip()
    if not owner:
        raise InboxError("no owner address set - run: apexis email to <address>")

    close_after = client is None
    try:
        client = client or _connect()
    except Exception as exc:
        raise InboxError(str(exc)) from exc

    found: list[dict] = []
    already = set(_read_seen())

    try:
        client.select("INBOX")
        status, data = client.search(
            None, "FROM", f'"{owner}"', "SINCE", _since_token()
        )
        if status != "OK":
            raise InboxError("the mailbox refused the search")

        numbers = (data[0] or b"").split()
        # Newest first, so a burst of mail does not push the latest out.
        for num in reversed(numbers):
            if len(found) >= MAX_PER_POLL:
                break
            # PEEK, so reading never marks the user's mail as read.
            status, payload = client.fetch(num, "(BODY.PEEK[])")
            if status != "OK" or not payload:
                continue

            raw = None
            for part in payload:
                if isinstance(part, tuple) and len(part) > 1:
                    raw = part[1]
                    break
            if raw is None:
                continue

            message = email.message_from_bytes(
                raw if isinstance(raw, bytes) else str(raw).encode()
            )

            ident = _decode(message.get("Message-ID", "")).strip()
            if not ident:
                ident = f"{_decode(message.get('Date',''))}|{_decode(message.get('Subject',''))}"
            if ident in already:
                continue

            sender_addr = parseaddr(message.get("From", ""))[1]
            if not mail.is_owner(sender_addr):
                # The server was asked for the owner's mail only; if something
                # else arrives, it is not trusted just because it turned up.
                continue

            subject = _decode(message.get("Subject", ""))
            question, urls = parse_command(subject, _plain_text(message))
            if not question:
                # Not a command, or an empty one. Remember it anyway so a
                # normal email is not re-examined on every single poll.
                _remember([ident])
                already.add(ident)
                continue

            found.append(
                {"id": ident, "question": question, "urls": urls, "subject": subject}
            )
    except InboxError:
        raise
    except Exception as exc:
        raise InboxError(str(exc)) from exc
    finally:
        if close_after:
            try:
                client.logout()
            except Exception:
                pass

    found.reverse()  # queue them in the order they were sent
    return found


def collect(*, client=None, verbose: bool = False) -> dict:
    """Read the mailbox and queue whatever was asked for.

    Never raises. A mailbox that cannot be reached must not stop the worker
    from finishing the jobs it already has.
    """
    from apexis_desktop import worker

    result = {"queued": 0, "questions": [], "error": ""}

    if not is_enabled():
        return result
    if not mail.is_configured():
        result["error"] = "email is not set up"
        return result

    try:
        commands = read_commands(client=client)
    except InboxError as exc:
        result["error"] = str(exc)
        if verbose:
            print(f"  could not read the mailbox: {exc}")
        return result

    handled = []
    for command in commands:
        try:
            worker.add(command["question"], command["urls"])
        except Exception as exc:  # a bad job must not eat the good ones
            if verbose:
                print(f"  could not queue {command['question'][:40]!r}: {exc}")
            continue
        handled.append(command["id"])
        result["questions"].append(command["question"])
        result["queued"] += 1
        if verbose:
            print(f"  queued from email: {command['question']}")

    _remember(handled)

    if result["queued"]:
        _confirm(result["questions"])

    return result


def _confirm(questions: list[str]) -> None:
    """Tell the user their mail landed, so silence never means uncertainty."""
    count = len(questions)
    lines = [
        f"{count} question{'s' if count != 1 else ''} queued:",
        "",
    ]
    lines.extend(f"  - {q}" for q in questions)
    lines += [
        "",
        "The answers follow as separate emails once the worker runs.",
    ]
    subject = f"[APEXIS] queued {count} question{'s' if count != 1 else ''}"
    mail.notify(subject, "\n".join(lines))
