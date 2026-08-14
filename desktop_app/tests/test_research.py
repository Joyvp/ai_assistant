"""Tests for ``apexis research``.

The point of this command is that the model loads **once** for a whole pile
of pages, instead of once per page. So the tests that matter most are about
load counting and about telling the truth afterwards — the previous three
bugs in this project were all a display line that disagreed with what the
code actually did.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import httpx
import pytest

from apexis_shared.jobs import Job, JobState

from apexis_desktop import research
from apexis_desktop.nodes import Fleet, Node


@pytest.fixture(autouse=True)
def scratch_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


PAGE = (
    "<html><head><title>A Page</title></head><body>"
    "<p>Some genuine prose about the subject at hand goes here.</p>"
    "</body></html>"
)


class FakeLifecycle:
    """Counts loads and unloads."""

    def __init__(self, host: str = "", resident: bool = False) -> None:
        self.host = host
        self._resident = resident
        self.loads = 0
        self.unloads = 0

    def is_resident(self, model: str) -> bool:
        return self._resident

    @contextmanager
    def borrowed(self, model, *, keep_alive="5m", unload_after=True):
        if not self._resident:
            self.loads += 1
        try:
            yield None
        finally:
            if not self._resident and unload_after:
                self.unloads += 1


class FakeProvider:
    def __init__(self, model: str, host: str) -> None:
        self.model = model
        self.prompts: list[str] = []

    def stream_messages(self, messages):
        FakeProvider.last_prompt = messages[-1]["content"]
        yield "the answer"

    def close(self) -> None:
        pass


@pytest.fixture
def wired(monkeypatch):
    """A laptop that answers, and web pages that resolve."""
    lifecycle = FakeLifecycle()

    monkeypatch.setattr(research, "ModelLifecycle", lambda host: lifecycle)
    monkeypatch.setattr(research, "OllamaProvider", FakeProvider)
    monkeypatch.setattr(
        research,
        "load_fleet",
        lambda: Fleet(laptop=Node(name="laptop", host="http://127.0.0.1:11434")),
    )

    def handler(request):
        url = str(request.url)
        if "dead" in url:
            raise httpx.ConnectError("no such host")
        if "same" in url:
            body = PAGE  # identical on purpose, for the dedupe test
        else:
            body = (
                f"<html><head><title>A Page {request.url.path}</title></head>"
                f"<body><p>Distinct prose about {request.url.path} lives "
                f"here for the reader.</p></body></html>"
            )
        return httpx.Response(
            200, text=body, headers={"content-type": "text/html"}
        )

    real = httpx.Client

    def patched(*args, **kwargs):
        kwargs.pop("transport", None)
        kwargs.pop("follow_redirects", None)
        kwargs.pop("headers", None)
        kwargs.pop("timeout", None)
        return real(transport=httpx.MockTransport(handler))

    monkeypatch.setattr(httpx, "Client", patched)
    return lifecycle


# -- the whole point -------------------------------------------------------


def test_five_pages_cost_one_model_load(wired, capsys):
    urls = [f"https://example.com/{i}?v={i}" for i in range(5)]
    research.ask("what changed?", urls)

    assert wired.loads == 1


def test_the_model_is_unloaded_afterwards(wired):
    research.ask("what changed?", ["https://example.com/a"])

    assert wired.unloads == 1


def test_the_model_sees_all_the_pages_at_once(wired):
    urls = [f"https://example.com/{i}?v={i}" for i in range(3)]
    research.ask("what changed?", urls)

    assert FakeProvider.last_prompt.count("--- from:") == 3


# -- honesty ---------------------------------------------------------------


def test_it_does_not_claim_an_unload_it_did_not_do(monkeypatch, capsys):
    lifecycle = FakeLifecycle(resident=True)
    monkeypatch.setattr(research, "ModelLifecycle", lambda host: lifecycle)
    monkeypatch.setattr(research, "OllamaProvider", FakeProvider)
    monkeypatch.setattr(
        research,
        "load_fleet",
        lambda: Fleet(laptop=Node(name="laptop", host="http://x")),
    )

    research.answer(Job(question="q"))
    out = capsys.readouterr().out

    assert "left loaded" in out
    assert "unloaded" not in out


def test_a_reused_model_is_announced_as_reused(monkeypatch, capsys):
    lifecycle = FakeLifecycle(resident=True)
    monkeypatch.setattr(research, "ModelLifecycle", lambda host: lifecycle)
    monkeypatch.setattr(research, "OllamaProvider", FakeProvider)
    monkeypatch.setattr(
        research,
        "load_fleet",
        lambda: Fleet(laptop=Node(name="laptop", host="http://x")),
    )

    research.answer(Job(question="q"))

    assert "already loaded" in capsys.readouterr().out


def test_collapsed_duplicates_are_explained(wired, capsys):
    """Four links in, two pages out, must not be silent about the gap."""
    urls = ["https://a.example/same", "https://b.example/same"]
    research.ask("what changed?", urls)
    out = capsys.readouterr().out

    assert "duplicate" in out


def test_a_failed_page_is_named(wired, capsys):
    research.ask(
        "what changed?", ["https://good.example/a", "https://dead.example/b"]
    )
    out = capsys.readouterr().out

    assert "dead.example" in out
    assert "skipped" in out


def test_a_failed_page_does_not_stop_the_answer(wired):
    code = research.ask(
        "what changed?", ["https://good.example/a", "https://dead.example/b"]
    )
    assert code == 0


def test_every_page_failing_is_reported_and_no_model_is_loaded(wired, capsys):
    code = research.ask("what changed?", ["https://dead.example/b"])
    out = capsys.readouterr().out

    assert code == 1
    assert wired.loads == 0
    assert "none of the pages" in out


# -- the deferred path -----------------------------------------------------


def test_prep_saves_without_answering(wired):
    research.ask("later please", ["https://example.com/a"], answer_now=False)

    assert wired.loads == 0
    assert len(research.all_jobs()) == 1
    assert research.all_jobs()[0].state is JobState.PREPARED


def test_a_prepped_job_can_be_answered_later(wired):
    research.ask("later please", ["https://example.com/a"], answer_now=False)
    job_id = research.all_jobs()[0].id

    research.main("answer", [job_id])

    assert research.load(job_id).state is JobState.DONE
    assert research.load(job_id).answer


def test_the_answer_is_kept_on_disk(wired):
    research.ask("what changed?", ["https://example.com/a"])
    job = research.all_jobs()[0]

    assert job.answer == "the answer"
    assert job.answered_by


def test_jobs_survive_a_restart(wired):
    research.ask("what changed?", ["https://example.com/a"])
    job_id = research.all_jobs()[0].id

    assert research.load(job_id) is not None


def test_a_corrupt_job_file_does_not_break_the_list(wired):
    research.ask("what changed?", ["https://example.com/a"])
    (research.jobs_dir() / "broken.json").write_text("{not json")

    assert len(research.all_jobs()) == 1


# -- the command line ------------------------------------------------------


def test_urls_are_separated_from_the_question():
    question, urls = research.split_question(
        ["what", "changed", "https://a.com", "in", "python", "https://b.com"]
    )

    assert question == "what changed in python"
    assert urls == ["https://a.com", "https://b.com"]


def test_a_question_with_no_urls_is_allowed():
    question, urls = research.split_question(["what", "is", "2+2?"])

    assert question == "what is 2+2?"
    assert urls == []


def test_an_empty_question_is_refused(wired, capsys):
    assert research.ask("", []) == 1
    assert "usage" in capsys.readouterr().out


def test_listing_nothing_is_not_an_error(wired, capsys):
    assert research.main("list") == 0
    assert "no research jobs" in capsys.readouterr().out


def test_showing_a_missing_job_is_an_error(wired, capsys):
    assert research.main("show", ["nope"]) == 1
    assert "no job" in capsys.readouterr().out


def test_answering_a_missing_job_is_an_error(wired, capsys):
    assert research.main("answer", ["nope"]) == 1


def test_show_prints_the_sources_and_the_answer(wired, capsys):
    research.ask("what changed?", ["https://example.com/a"])
    capsys.readouterr()

    research.main("show", [research.all_jobs()[0].id])
    out = capsys.readouterr().out

    assert "A Page" in out
    assert "the answer" in out


def test_a_bare_question_is_treated_as_the_question(wired):
    research.main("hello", ["https://example.com/a"])

    assert research.all_jobs()[0].question == "hello"
