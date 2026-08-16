"""Tests for the queue worker — the part that runs while nobody is home.

Two properties matter more than the rest. One model load for the whole
batch, because loading phi3 once per job is the waste this project exists
to avoid. And absolute silence when the user is home, because a machine
that emails you while you are looking at it is spam.
"""

from __future__ import annotations

from contextlib import contextmanager

import httpx
import pytest

from apexis_shared.jobs import JobState

from apexis_desktop import away, mail, research, worker
from apexis_desktop.nodes import Fleet, Node


PAGE = (
    "<html><head><title>A Page</title></head><body>"
    "<p>Some genuine prose about the subject at hand.</p></body></html>"
)


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    for var in ("APEXIS_EMAIL_USER", "APEXIS_EMAIL_PASS", "APEXIS_NOTIFY_EMAIL"):
        monkeypatch.delenv(var, raising=False)

    config = tmp_path / "config" / "apexis"
    config.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(mail, "CONFIG_PATH", config / "mail.json")
    monkeypatch.setattr(mail, "OUTBOX_PATH", config / "outbox.json")
    monkeypatch.setattr(away, "state_path", lambda: config / "away.json")
    return tmp_path


class CountingLifecycle:
    def __init__(self, host=""):
        self.host = host
        self.loads = 0
        self.unloads = 0

    def is_resident(self, model):
        return False

    @contextmanager
    def borrowed(self, model, *, keep_alive="5m", unload_after=True):
        self.loads += 1
        try:
            yield None
        finally:
            if unload_after:
                self.unloads += 1


class FakeProvider:
    runs = 0

    def __init__(self, model, host):
        self.model = model

    def stream_messages(self, messages):
        FakeProvider.runs += 1
        yield "an answer"

    def close(self):
        pass


@pytest.fixture
def wired(monkeypatch):
    lifecycle = CountingLifecycle()
    FakeProvider.runs = 0

    monkeypatch.setattr(worker, "ModelLifecycle", lambda host: lifecycle)
    monkeypatch.setattr(worker, "OllamaProvider", FakeProvider)
    monkeypatch.setattr(
        worker,
        "load_fleet",
        lambda: Fleet(laptop=Node(name="laptop", host="http://x")),
    )

    def handler(request):
        if "dead" in str(request.url):
            raise httpx.ConnectError("no such host")
        return httpx.Response(
            200,
            text=PAGE.replace("A Page", f"Page {request.url.path}"),
            headers={"content-type": "text/html"},
        )

    real = httpx.Client

    def patched(*args, **kwargs):
        for key in ("transport", "follow_redirects", "headers", "timeout"):
            kwargs.pop(key, None)
        return real(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", patched)
    return lifecycle


@pytest.fixture
def configured():
    mail.set_setting("sender", "bot@gmail.com")
    mail.set_setting("password", "abcdefghijklmnop")
    mail.set_setting("owner", "joy@example.com")


# -- queueing --------------------------------------------------------------


def test_queueing_runs_nothing(wired):
    worker.add("a question", ["https://example.com/a"])

    assert wired.loads == 0
    assert len(worker.queued()) == 1


def test_a_queued_job_survives_a_restart(wired):
    job = worker.add("a question", [])

    assert research.load(job.id).state is JobState.QUEUED


def test_the_queue_is_oldest_first(wired):
    worker.add("first", [])
    worker.add("second", [])

    assert [j.question for j in worker.queued()] == ["first", "second"]


def test_draining_an_empty_queue_is_harmless(wired):
    summary = worker.drain(verbose=False)

    assert summary["jobs"] == 0
    assert wired.loads == 0


# -- the whole point -------------------------------------------------------


def test_five_jobs_cost_one_model_load(wired):
    for i in range(5):
        worker.add(f"question {i}", [f"https://example.com/{i}"])

    worker.drain(verbose=False)

    assert wired.loads == 1
    assert FakeProvider.runs == 5


def test_the_model_is_unloaded_after_the_batch(wired):
    worker.add("q", ["https://example.com/a"])
    worker.drain(verbose=False)

    assert wired.unloads == 1


def test_every_job_ends_up_answered(wired):
    for i in range(3):
        worker.add(f"question {i}", [f"https://example.com/{i}"])

    summary = worker.drain(verbose=False)

    assert summary["done"] == 3
    assert all(j.state is JobState.DONE for j in research.all_jobs())


def test_a_drained_job_leaves_the_queue(wired):
    worker.add("q", ["https://example.com/a"])
    worker.drain(verbose=False)

    assert worker.queued() == []


def test_gathering_happens_before_the_model_wakes(wired, monkeypatch):
    """The Pi's half must not need a model resident. If preparation ran
    inside the borrow, the model would idle through every download."""
    order = []

    real_prepare = worker.prepare
    monkeypatch.setattr(
        worker, "prepare", lambda job, **kw: order.append("prepare") or real_prepare(job, **kw)
    )

    class Watched(CountingLifecycle):
        @contextmanager
        def borrowed(self, model, *, keep_alive="5m", unload_after=True):
            order.append("load")
            self.loads += 1
            yield None

    monkeypatch.setattr(worker, "ModelLifecycle", lambda host: Watched())

    worker.add("a", ["https://example.com/a"])
    worker.add("b", ["https://example.com/b"])
    worker.drain(verbose=False)

    assert order == ["prepare", "prepare", "load"]


# -- failures ---------------------------------------------------------------


def test_one_dead_link_does_not_stop_the_batch(wired):
    worker.add("good", ["https://example.com/a"])
    worker.add("bad", ["https://dead.example/b"])

    summary = worker.drain(verbose=False)

    assert summary["done"] == 1
    assert summary["failed"] == 1


def test_an_unreachable_model_does_not_lose_the_jobs(wired, monkeypatch):
    class Broken(CountingLifecycle):
        @contextmanager
        def borrowed(self, *a, **k):
            raise RuntimeError("ollama is not running")
            yield

    monkeypatch.setattr(worker, "ModelLifecycle", lambda host: Broken())

    worker.add("q", ["https://example.com/a"])
    summary = worker.drain(verbose=False)

    assert summary["done"] == 0
    # the job is prepared, not destroyed — it can be answered later
    assert any(j.state is JobState.PREPARED for j in research.all_jobs())


# -- email, and the away gate ----------------------------------------------


def test_it_emails_every_finished_job_when_you_are_away(
    wired, configured, monkeypatch
):
    sent = []
    monkeypatch.setattr(mail, "notify", lambda s, b: sent.append(s) or True)
    away.leave()

    for i in range(3):
        worker.add(f"question {i}", [f"https://example.com/{i}"])
    summary = worker.drain(verbose=False)

    assert len(sent) == 3
    assert summary["emailed"] == 3


def test_it_emails_nothing_when_you_are_home(wired, configured, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "notify", lambda s, b: sent.append(s) or True)

    worker.add("q", ["https://example.com/a"])
    worker.drain(verbose=False)

    assert sent == []


def test_the_work_still_happens_when_you_are_home(wired, configured):
    worker.add("q", ["https://example.com/a"])
    summary = worker.drain(verbose=False)

    assert summary["done"] == 1
    assert summary["emailed"] == 0


def test_a_broken_mailbox_does_not_lose_the_answer(
    wired, configured, monkeypatch
):
    """The answer is the valuable thing. A failed notification must never
    take it down with it."""
    monkeypatch.setattr(mail, "notify", lambda s, b: False)
    away.leave()

    worker.add("q", ["https://example.com/a"])
    summary = worker.drain(verbose=False)

    assert summary["done"] == 1
    assert summary["emailed"] == 0
    assert research.all_jobs()[0].answer


def test_nothing_is_emailed_when_email_is_not_configured(wired):
    away.leave()
    worker.add("q", ["https://example.com/a"])

    summary = worker.drain(verbose=False)

    assert summary["done"] == 1
    assert summary["emailed"] == 0


# -- the commands ----------------------------------------------------------


def test_later_queues_without_running(wired, capsys):
    assert worker.later("a question", ["https://example.com/a"]) == 0
    assert wired.loads == 0
    assert "queued" in capsys.readouterr().out


def test_later_refuses_an_empty_question(wired, capsys):
    assert worker.later("", []) == 1
    assert "usage" in capsys.readouterr().out


def test_later_mentions_email_when_you_are_already_out(
    wired, configured, capsys
):
    away.leave()
    worker.later("q", [])

    assert "emailed" in capsys.readouterr().out


def test_the_empty_queue_explains_itself(wired, capsys):
    assert worker.show_queue() == 0
    assert "nothing queued" in capsys.readouterr().out


def test_the_queue_lists_what_is_waiting(wired, capsys):
    worker.add("something specific", ["https://example.com/a"])

    worker.show_queue()

    assert "something specific" in capsys.readouterr().out


def test_watch_once_drains_and_returns(wired):
    worker.add("q", ["https://example.com/a"])

    assert worker.watch(once=True) == 0
    assert worker.queued() == []


def test_later_is_reachable_from_the_parser():
    from apexis_desktop.cli import build_parser

    args = build_parser().parse_args(["later", "what", "changed", "https://a.com"])
    assert args.words == ["what", "changed", "https://a.com"]


def test_watch_once_is_reachable_from_the_parser():
    from apexis_desktop.cli import build_parser

    args = build_parser().parse_args(["watch", "--once"])
    assert args.once is True


def test_queue_is_reachable_from_the_parser():
    from apexis_desktop.cli import build_parser

    assert build_parser().parse_args(["queue"]).command == "queue"


# -- the automatic timer must not barge in ---------------------------------


def test_the_timer_defers_when_the_model_is_in_use(wired, monkeypatch):
    """Ollama holds a model for five minutes after the last request, so a
    resident model means someone was just talking to it. A background drain
    that seizes it would make the human's next reply crawl."""
    monkeypatch.setattr(worker, "someone_is_using_the_model", lambda: True)
    worker.add("q", ["https://example.com/a"])

    summary = worker.drain(verbose=False, if_idle=True)

    assert summary["deferred"] is True
    assert summary["done"] == 0
    assert wired.loads == 0


def test_a_deferred_job_stays_queued(wired, monkeypatch):
    monkeypatch.setattr(worker, "someone_is_using_the_model", lambda: True)
    worker.add("q", ["https://example.com/a"])

    worker.drain(verbose=False, if_idle=True)

    assert len(worker.queued()) == 1


def test_the_timer_runs_when_nothing_is_resident(wired, monkeypatch):
    monkeypatch.setattr(worker, "someone_is_using_the_model", lambda: False)
    worker.add("q", ["https://example.com/a"])

    summary = worker.drain(verbose=False, if_idle=True)

    assert summary["deferred"] is False
    assert summary["done"] == 1


def test_a_manual_run_never_defers(wired, monkeypatch):
    """The user typed the command. Do not second-guess them."""
    monkeypatch.setattr(worker, "someone_is_using_the_model", lambda: True)
    worker.add("q", ["https://example.com/a"])

    summary = worker.drain(verbose=False)

    assert summary["deferred"] is False
    assert summary["done"] == 1


def test_an_empty_queue_never_touches_the_model(wired, monkeypatch):
    """The timer fires every five minutes forever. Checking must be free."""
    calls = []
    monkeypatch.setattr(
        worker, "someone_is_using_the_model", lambda: calls.append(1) or False
    )

    worker.drain(verbose=False, if_idle=True)

    assert calls == []
    assert wired.loads == 0


def test_a_broken_probe_does_not_block_the_queue(wired, monkeypatch):
    """If we cannot tell whether the model is busy, do the work rather than
    silently never running again."""
    def explode():
        raise OSError("no route to host")

    monkeypatch.setattr(worker, "load_fleet", explode)

    assert worker.someone_is_using_the_model() is False


def test_if_idle_is_reachable_from_the_parser():
    from apexis_desktop.cli import build_parser

    args = build_parser().parse_args(["watch", "--once", "--if-idle"])
    assert args.if_idle is True
