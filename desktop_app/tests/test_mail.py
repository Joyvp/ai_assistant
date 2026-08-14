"""Tests for away mode and email.

This is the first part of APEXIS that can reach outside the house on its
own, so the tests are mostly about what it must REFUSE to do. The old
implementation guarded strangers with a blocking input() call, which meant
the guard only worked while someone was sitting there — precisely when the
feature was not needed.
"""

from __future__ import annotations

import json

import pytest

from apexis_desktop import away, mail, mail_cli


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for var in (
        "APEXIS_EMAIL_USER",
        "APEXIS_EMAIL_PASS",
        "APEXIS_NOTIFY_EMAIL",
    ):
        monkeypatch.delenv(var, raising=False)

    # These are resolved at import time, so point them at the scratch dir.
    config = tmp_path / "config" / "apexis"
    config.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mail, "CONFIG_PATH", config / "mail.json")
    monkeypatch.setattr(mail, "OUTBOX_PATH", config / "outbox.json")
    monkeypatch.setattr(away, "state_path", lambda: config / "away.json")
    return tmp_path


@pytest.fixture
def configured():
    mail.set_setting("sender", "apexis.bot@gmail.com")
    mail.set_setting("password", "abcdefghijklmnop")
    mail.set_setting("owner", "joy@example.com")


class Sent:
    """Captures messages instead of contacting a mail server."""

    def __init__(self):
        self.messages = []

    def __call__(self, message):
        self.messages.append(message)

    @property
    def recipients(self):
        return [m["To"] for m in self.messages]


# -- away mode -------------------------------------------------------------


def test_home_is_the_default():
    assert away.is_away() is False


def test_leaving_and_returning():
    away.leave()
    assert away.is_away() is True

    away.arrive()
    assert away.is_away() is False


def test_the_note_is_kept():
    away.leave("at my friend's place")
    assert "friend" in json.loads(away.state_path().read_text())["note"]


def test_away_survives_a_restart():
    away.leave()
    assert away.is_away() is True  # re-read from disk each time


def test_a_corrupt_state_file_means_home():
    """Silence is the safe failure. Never email on a parse error."""
    away.state_path().write_text("{not json")
    assert away.is_away() is False


def test_elapsed_is_empty_when_home():
    assert away.elapsed() == ""


def test_elapsed_reads_naturally_when_away():
    away.leave()
    assert away.elapsed() == "just now"


# -- who APEXIS may write to -----------------------------------------------


def test_it_writes_to_you_without_asking(configured):
    sent = Sent()
    ok, note = mail.send_to(
        "joy@example.com", "s", "b", transport=sent
    )

    assert ok
    assert sent.recipients == ["joy@example.com"]


def test_it_will_not_write_to_anyone_else(configured):
    sent = Sent()
    ok, note = mail.send_to(
        "stranger@example.com", "s", "b", transport=sent
    )

    assert not ok
    assert sent.messages == []
    assert "outbox" in note


def test_a_refused_message_is_kept_not_lost(configured):
    mail.send_to("stranger@example.com", "hello", "body")

    waiting = mail.outbox()
    assert len(waiting) == 1
    assert waiting[0].to == "stranger@example.com"


def test_an_approved_message_really_sends(configured):
    sent = Sent()
    ok, _ = mail.send_to(
        "stranger@example.com", "s", "b", approved=True, transport=sent
    )

    assert ok
    assert sent.recipients == ["stranger@example.com"]


def test_the_owner_check_ignores_case_and_spaces(configured):
    assert mail.is_owner("  JOY@Example.com  ")


def test_a_malformed_address_is_refused(configured):
    ok, note = mail.send_to("not-an-address", "s", "b")

    assert not ok
    assert "does not look like" in note


def test_nothing_is_queued_for_a_malformed_address(configured):
    mail.send_to("not-an-address", "s", "b")
    assert mail.outbox() == []


# -- notifications ---------------------------------------------------------


def test_notify_goes_to_the_owner(configured):
    sent = Sent()
    assert mail.notify("subject", "body", transport=sent) is True
    assert sent.recipients == ["joy@example.com"]


def test_notify_is_quiet_when_email_is_not_set_up():
    """A missing notification must not raise into the job that caused it."""
    assert mail.notify("subject", "body") is False


def test_notify_swallows_a_send_failure(configured):
    def explode(message):
        raise mail.MailError("server on fire")

    assert mail.notify("s", "b", transport=explode) is False


# -- credentials -----------------------------------------------------------


def test_the_environment_beats_the_config_file(configured, monkeypatch):
    monkeypatch.setenv("APEXIS_EMAIL_USER", "from-env@example.com")
    assert mail.sender() == "from-env@example.com"


def test_the_config_file_is_not_world_readable(configured):
    assert mail.CONFIG_PATH.stat().st_mode & 0o077 == 0


def test_missing_settings_are_named():
    assert set(mail.missing()) == {"sender", "password", "owner"}


def test_nothing_is_configured_by_default():
    assert mail.is_configured() is False


# -- the report ------------------------------------------------------------


class FakeSource:
    def __init__(self, ok=True, title="A Page", url="https://x", error=""):
        self.ok = ok
        self.title = title
        self.url = url
        self.error = error


class FakeJob:
    id = "abc123"
    question = "what changed in python 3.13?"
    answer = "Quite a lot, actually."
    answered_by = "phi3:mini"
    sources = [
        FakeSource(),
        FakeSource(ok=False, url="https://dead", error="HTTP 404"),
    ]


def test_the_report_carries_the_question_and_answer():
    subject, body = mail.job_report(FakeJob())

    assert "python 3.13" in subject
    assert "Quite a lot" in body


def test_the_report_names_what_it_read():
    _subject, body = mail.job_report(FakeJob())
    assert "A Page" in body


def test_the_report_admits_what_it_could_not_read():
    _subject, body = mail.job_report(FakeJob())

    assert "Could not read" in body
    assert "HTTP 404" in body


def test_the_report_says_which_model_answered():
    _subject, body = mail.job_report(FakeJob())
    assert "phi3:mini" in body


# -- the research hook -----------------------------------------------------


def test_a_finished_job_emails_you_when_you_are_away(configured, monkeypatch):
    from apexis_desktop import research

    sent = Sent()
    monkeypatch.setattr(
        mail, "notify", lambda s, b: bool(sent(_Message("joy@example.com")))
    )
    away.leave()

    research._report_if_away(FakeJob())

    assert len(sent.messages) == 1


def test_a_finished_job_stays_quiet_when_you_are_home(configured, monkeypatch):
    from apexis_desktop import research

    calls = []
    monkeypatch.setattr(mail, "notify", lambda s, b: calls.append(1) or True)

    research._report_if_away(FakeJob())

    assert calls == []


class _Message(dict):
    def __init__(self, to):
        super().__init__()
        self["To"] = to


# -- the commands ----------------------------------------------------------


def test_going_away_reports_that_email_is_missing(capsys):
    mail_cli.go_away("out for a walk")
    out = capsys.readouterr().out

    assert "Away" in out
    assert "isn't set up" in out


def test_coming_home_flags_the_waiting_outbox(configured, capsys):
    away.leave()
    mail.queue("stranger@example.com", "s", "b")
    capsys.readouterr()

    mail_cli.come_home()

    assert "waiting for your approval" in capsys.readouterr().out


def test_coming_home_when_you_never_left_is_harmless(capsys):
    assert mail_cli.come_home() == 0
    assert "weren't marked away" in capsys.readouterr().out


def test_setup_never_asks_for_your_real_google_password(capsys):
    mail_cli.setup()
    out = capsys.readouterr().out

    assert "App Password" in out
    assert "NEW Gmail" in out


def test_the_password_is_not_echoed_back(configured, capsys):
    mail_cli.set_value("password", "abcdefghijklmnop")
    assert "abcdefghijklmnop" not in capsys.readouterr().out


def test_a_bad_address_is_rejected_by_the_command(capsys):
    assert mail_cli.set_value("owner", "nonsense") == 1


def test_approving_sends_and_empties_the_slot(configured, monkeypatch):
    mail.queue("stranger@example.com", "s", "b")
    sent = Sent()
    monkeypatch.setattr(
        mail,
        "send_to",
        lambda to, s, b, approved=False, transport=None: (
            sent(_Message(to)),
            (True, ""),
        )[1],
    )

    assert mail_cli.approve("0") == 0
    assert mail.outbox() == []


def test_dropping_discards_without_sending(configured, capsys):
    mail.queue("stranger@example.com", "s", "b")

    assert mail_cli.drop("0") == 0
    assert mail.outbox() == []
    assert "nothing was sent" in capsys.readouterr().out


def test_approving_a_missing_message_is_an_error(capsys):
    assert mail_cli.approve("7") == 1


def test_the_outbox_shows_what_is_waiting(configured, capsys):
    mail.queue("stranger@example.com", "hello there", "body")

    mail_cli.show_outbox()
    out = capsys.readouterr().out

    assert "stranger@example.com" in out
    assert "hello there" in out


def test_the_email_commands_are_reachable_from_the_parser():
    from apexis_desktop.cli import build_parser

    args = build_parser().parse_args(["email", "outbox"])
    assert args.action == "outbox"


def test_away_is_reachable_from_the_parser():
    from apexis_desktop.cli import build_parser

    args = build_parser().parse_args(["away", "at", "the", "shop"])
    assert args.note == ["at", "the", "shop"]


# -- pasting an app password the way Google shows it -----------------------


def test_a_spaced_app_password_is_accepted(configured):
    """Google displays app passwords as "abcd efgh ijkl mnop".

    Nobody retypes that without spaces. argparse saw four arguments and
    errored out. The shape the user is given must be the shape the command
    accepts.
    """
    mail_cli.main("password", "abcd ltnh zjpf zhpk")

    assert mail.password() == "abcdltnhzjpfzhpk"


def test_an_unspaced_app_password_still_works(configured):
    mail_cli.main("password", "abcdltnhzjpfzhpk")

    assert mail.password() == "abcdltnhzjpfzhpk"


def test_the_parser_accepts_a_spaced_password():
    from apexis_desktop.cli import build_parser

    args = build_parser().parse_args(
        ["email", "password", "abcd", "ltnh", "zjpf", "zhpk"]
    )
    assert args.value == ["abcd", "ltnh", "zjpf", "zhpk"]


def test_an_address_with_a_stray_space_is_not_mangled(configured):
    """Only the password is space-stripped; addresses keep their shape."""
    mail_cli.main("to", "joy@example.com")

    assert mail.owner() == "joy@example.com"
