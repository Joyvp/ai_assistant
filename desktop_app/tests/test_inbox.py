"""Taking instructions by email.

The dangerous parts are: who is allowed to give orders, what counts as an
order, and whether a broken mailbox can stop work that is already queued.
"""

from __future__ import annotations

import email.message

import pytest

from apexis_desktop import inbox, mail, worker


@pytest.fixture(autouse=True)
def clean(tmp_path, monkeypatch):
    monkeypatch.setattr(mail, "CONFIG_PATH", tmp_path / "mail.json")
    monkeypatch.setattr(inbox, "SEEN_PATH", tmp_path / "seen.json")
    monkeypatch.delenv("APEXIS_EMAIL_USER", raising=False)
    monkeypatch.delenv("APEXIS_EMAIL_PASS", raising=False)
    monkeypatch.delenv("APEXIS_NOTIFY_EMAIL", raising=False)
    mail.set_setting("sender", "me@gmail.com")
    mail.set_setting("password", "abcdefghijklmnop")
    mail.set_setting("owner", "me@gmail.com")
    inbox.set_enabled(True)
    yield


def make_mail(sender: str, subject: str, body: str = "", ident: str = "<1@x>"):
    msg = email.message.EmailMessage()
    msg["From"] = sender
    msg["Subject"] = subject
    msg["Message-ID"] = ident
    msg.set_content(body)
    return msg


class FakeIMAP:
    """Enough of imaplib to prove the logic, and it records how it was used."""

    def __init__(self, messages):
        self.messages = messages
        self.fetch_commands = []
        self.logged_out = False
        self.stored = []

    def select(self, box):
        return "OK", [b""]

    def search(self, charset, *criteria):
        self.criteria = criteria
        nums = " ".join(str(i + 1) for i in range(len(self.messages)))
        return "OK", [nums.encode()]

    def fetch(self, num, spec):
        self.fetch_commands.append(spec)
        msg = self.messages[int(num) - 1]
        return "OK", [(b"1 (BODY[])", msg.as_bytes())]

    def store(self, *a):
        self.stored.append(a)
        return "OK", [b""]

    def logout(self):
        self.logged_out = True


# -- what counts as an order ----------------------------------------------


def test_subject_must_start_with_the_trigger():
    question, urls = inbox.parse_command("apexis what is a pi", "")
    assert question == "what is a pi"


def test_a_normal_email_is_not_a_command():
    assert inbox.parse_command("dinner tonight?", "call me") == ("", [])


def test_trigger_may_have_a_colon_and_any_case():
    assert inbox.parse_command("APEXIS: what is groq", "")[0] == "what is groq"


def test_a_reply_to_an_answer_is_not_a_new_question():
    """Subjects come back as 'Re: [APEXIS] ...' — that must not re-queue."""
    question, _ = inbox.parse_command("Re: [APEXIS] what is a pi", "thanks!")
    assert question == ""


def test_links_are_pulled_from_the_body():
    question, urls = inbox.parse_command(
        "apexis how much power", "https://example.com/pi"
    )
    assert question == "how much power"
    assert urls == ["https://example.com/pi"]


def test_a_question_can_live_entirely_in_the_body():
    question, _ = inbox.parse_command("apexis", "what is a systemd timer")
    assert question == "what is a systemd timer"


def test_duplicate_links_collapse():
    _, urls = inbox.parse_command(
        "apexis compare", "https://a.com https://a.com https://b.com"
    )
    assert urls == ["https://a.com", "https://b.com"]


def test_trailing_punctuation_is_not_part_of_the_link():
    _, urls = inbox.parse_command("apexis read", "see https://a.com/page.")
    assert urls == ["https://a.com/page"]


def test_quoted_history_is_dropped():
    body = "new question\n\nOn Sun, APEXIS wrote:\n> a long old answer"
    assert inbox.strip_quoted(body) == "new question"


def test_phone_signature_is_dropped():
    assert inbox.strip_quoted("do this\n\nSent from my iPhone") == "do this"


# -- who is allowed ---------------------------------------------------------


def test_only_the_owner_is_obeyed():
    client = FakeIMAP([make_mail("stranger@evil.com", "apexis delete stuff")])
    assert inbox.read_commands(client=client) == []


def test_the_owner_is_obeyed():
    client = FakeIMAP([make_mail("me@gmail.com", "apexis what is a pi")])
    found = inbox.read_commands(client=client)
    assert len(found) == 1
    assert found[0]["question"] == "what is a pi"


def test_the_search_asks_the_server_for_the_owner_only():
    """Filtering server-side means a stranger's mail is never even read."""
    client = FakeIMAP([make_mail("me@gmail.com", "apexis hi")])
    inbox.read_commands(client=client)
    assert '"me@gmail.com"' in client.criteria


def test_listening_is_off_until_turned_on():
    inbox.set_enabled(False)
    assert inbox.is_enabled() is False
    assert inbox.collect()["queued"] == 0


# -- not disturbing the mailbox --------------------------------------------


def test_reading_uses_peek_so_mail_stays_unread():
    client = FakeIMAP([make_mail("me@gmail.com", "apexis hi")])
    inbox.read_commands(client=client)
    assert all("PEEK" in c for c in client.fetch_commands)


def test_nothing_is_ever_flagged_or_deleted():
    client = FakeIMAP([make_mail("me@gmail.com", "apexis hi")])
    inbox.read_commands(client=client)
    assert client.stored == []


# -- not doing the same thing twice ----------------------------------------


def test_a_handled_message_is_not_handled_again(monkeypatch):
    monkeypatch.setattr(worker, "add", lambda q, u: None)
    monkeypatch.setattr(mail, "notify", lambda *a, **k: True)
    msg = make_mail("me@gmail.com", "apexis what is a pi", ident="<same@x>")

    first = inbox.collect(client=FakeIMAP([msg]))
    assert first["queued"] == 1

    second = inbox.collect(client=FakeIMAP([msg]))
    assert second["queued"] == 0


def test_a_non_command_is_remembered_so_it_is_not_re_read():
    client = FakeIMAP([make_mail("me@gmail.com", "dinner?", ident="<n@x>")])
    inbox.read_commands(client=client)
    assert "<n@x>" in inbox._read_seen()


def test_a_burst_of_mail_is_capped(monkeypatch):
    monkeypatch.setattr(inbox, "MAX_PER_POLL", 3)
    messages = [
        make_mail("me@gmail.com", f"apexis question {i}", ident=f"<{i}@x>")
        for i in range(10)
    ]
    assert len(inbox.read_commands(client=FakeIMAP(messages))) == 3


# -- failure must not spread -----------------------------------------------


def test_an_unreachable_mailbox_does_not_raise():
    class Broken:
        def select(self, box):
            raise OSError("network down")

        def logout(self):
            pass

    result = inbox.collect(client=Broken())
    assert result["queued"] == 0
    assert "network down" in result["error"]


def test_an_unreachable_mailbox_still_lets_the_queue_drain(monkeypatch):
    """The whole point: broken email must not strand finished work."""
    def explode(**kwargs):
        raise RuntimeError("imap is down")

    monkeypatch.setattr(inbox, "collect", explode)
    assert worker.collect_mail(verbose=False) == 0


def test_one_bad_job_does_not_stop_the_others(monkeypatch):
    calls = []

    def flaky(question, urls):
        calls.append(question)
        if "bad" in question:
            raise RuntimeError("nope")

    monkeypatch.setattr(worker, "add", flaky)
    monkeypatch.setattr(mail, "notify", lambda *a, **k: True)
    messages = [
        make_mail("me@gmail.com", "apexis good one", ident="<1@x>"),
        make_mail("me@gmail.com", "apexis bad one", ident="<2@x>"),
        make_mail("me@gmail.com", "apexis another good", ident="<3@x>"),
    ]
    result = inbox.collect(client=FakeIMAP(messages))
    assert result["queued"] == 2
    assert len(calls) == 3


def test_a_failed_job_is_not_marked_handled(monkeypatch):
    """It should be retried, not silently lost."""
    def always_fails(question, urls):
        raise RuntimeError("nope")

    monkeypatch.setattr(worker, "add", always_fails)
    inbox.collect(client=FakeIMAP([make_mail("me@gmail.com", "apexis x", ident="<z@x>")]))
    assert "<z@x>" not in inbox._read_seen()


# -- telling the user ------------------------------------------------------


def test_the_user_is_told_their_mail_landed(monkeypatch):
    sent = []
    monkeypatch.setattr(worker, "add", lambda q, u: None)
    monkeypatch.setattr(mail, "notify", lambda s, b, **k: sent.append((s, b)))
    inbox.collect(client=FakeIMAP([make_mail("me@gmail.com", "apexis what is a pi")]))
    assert len(sent) == 1
    assert "what is a pi" in sent[0][1]


def test_no_confirmation_when_nothing_was_queued(monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "notify", lambda s, b, **k: sent.append(s))
    inbox.collect(client=FakeIMAP([make_mail("me@gmail.com", "dinner?")]))
    assert sent == []


# -- reachable from the command line ---------------------------------------


def test_inbox_commands_are_reachable_from_the_parser():
    from apexis_desktop.cli import build_parser

    for action in ("inbox", "inbox-off", "inbox-check"):
        args = build_parser().parse_args(["email", action])
        assert args.action == action


def test_the_worker_collects_mail_before_reading_the_queue(monkeypatch):
    """Order matters: mail that arrived must be worked in the same run."""
    order = []
    monkeypatch.setattr(inbox, "collect", lambda **k: order.append("collect") or {"queued": 0})
    monkeypatch.setattr(worker, "queued", lambda: order.append("queued") or [])
    worker.drain(verbose=False)
    assert order == ["collect", "queued"]
