"""Tests for the Pi's half of the work.

This code runs unattended at 3am on a machine in a drawer. The bar is not
"does it parse HTML nicely" — it is "does it ever crash, and does one bad
page ruin the batch". Most of these tests are about failure.
"""

from __future__ import annotations

import httpx
import pytest

from apexis_shared.jobs import Job, JobState, Source
from apexis_shared.prepare import (
    chunk_text,
    deduplicate,
    extract_text,
    fetch,
    fetch_all,
    prepare,
)


ARTICLE = """<html>
<head><title>  The Real Title  </title></head>
<body>
  <nav>Home</nav>
  <script>var tracker = 1;</script>
  <style>.ad { display: none }</style>
  <p>The first real paragraph of the article, which is long enough to keep.</p>
  <p>A second paragraph that also contains genuine prose worth reading.</p>
  <aside>Related articles</aside>
  <footer>Follow us</footer>
</body></html>"""


def _serving(text: str, *, status: int = 200, content_type: str = "text/html"):
    def handler(request):
        return httpx.Response(
            status, text=text, headers={"content-type": content_type}
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


# -- extraction ------------------------------------------------------------


def test_the_title_is_found_and_trimmed():
    title, _text = extract_text(ARTICLE)
    assert title == "The Real Title"


def test_the_prose_survives():
    _title, text = extract_text(ARTICLE)
    assert "first real paragraph" in text
    assert "second paragraph" in text


@pytest.mark.parametrize(
    "noise", ["var tracker", "display: none", "Follow us", "Home"]
)
def test_the_furniture_is_removed(noise):
    _title, text = extract_text(ARTICLE)
    assert noise not in text


def test_entities_are_decoded():
    _title, text = extract_text(
        "<p>Tom &amp; Jerry went to the shop and bought bread.</p>"
    )
    assert "Tom & Jerry" in text


def test_empty_html_does_not_crash():
    assert extract_text("") == ("", "")


def test_garbage_input_does_not_crash():
    title, text = extract_text("<<<>>not really html<<")
    assert isinstance(title, str) and isinstance(text, str)


def test_a_page_with_no_title_still_yields_text():
    title, text = extract_text("<p>Some genuine prose lives here alone.</p>")
    assert title == ""
    assert "genuine prose" in text


# -- fetching --------------------------------------------------------------


def test_a_good_page_becomes_a_usable_source():
    with _serving(ARTICLE) as client:
        source = fetch("https://example.com/a", client=client)

    assert source.ok
    assert source.title == "The Real Title"
    assert source.words > 5


def test_a_404_is_recorded_not_raised():
    with _serving("nope", status=404) as client:
        source = fetch("https://example.com/missing", client=client)

    assert not source.ok
    assert "404" in source.error


def test_a_dead_host_is_recorded_not_raised():
    def handler(request):
        raise httpx.ConnectError("no such host")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = fetch("https://nope.example", client=client)

    assert not source.ok
    assert "could not fetch" in source.error


def test_a_pdf_is_declined_politely():
    with _serving("%PDF-1.4", content_type="application/pdf") as client:
        source = fetch("https://example.com/doc.pdf", client=client)

    assert not source.ok
    assert "not a readable page" in source.error


def test_a_page_with_only_navigation_is_flagged_as_empty():
    with _serving("<html><nav>Home</nav></html>") as client:
        source = fetch("https://example.com/empty", client=client)

    assert not source.ok
    assert "no readable text" in source.error


def test_fetch_all_keeps_the_order_it_was_given():
    def handler(request):
        return httpx.Response(
            200,
            text=f"<p>Content for page {request.url.path} goes here.</p>",
            headers={"content-type": "text/html"},
        )

    urls = [f"https://example.com/{i}" for i in range(5)]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sources = fetch_all(urls, client=client)

    assert [s.url for s in sources] == urls


def test_fetch_all_of_nothing_is_nothing():
    assert fetch_all([]) == []


# -- deduplication ---------------------------------------------------------


def test_identical_articles_collapse_to_one():
    a = Source(url="https://a.com", text="the same wire story, word for word")
    b = Source(url="https://b.com", text="the same wire story, word for word")

    assert len(deduplicate([a, b])) == 1


def test_whitespace_differences_still_count_as_duplicates():
    a = Source(url="https://a.com", text="the same   story")
    b = Source(url="https://b.com", text="The Same Story")

    assert len(deduplicate([a, b])) == 1


def test_different_articles_are_both_kept():
    a = Source(url="https://a.com", text="one story about boats")
    b = Source(url="https://b.com", text="another story about trains")

    assert len(deduplicate([a, b])) == 2


def test_failures_are_kept_so_they_can_be_reported():
    ok = Source(url="https://a.com", text="real content here")
    bad = Source(url="https://b.com", error="HTTP 500")

    assert len(deduplicate([ok, bad])) == 2


# -- chunking --------------------------------------------------------------


def test_short_text_is_a_single_chunk():
    assert len(chunk_text("just a few words here")) == 1


def test_long_text_is_split():
    text = "\n".join(f"paragraph {i} " + "word " * 50 for i in range(10))
    chunks = chunk_text(text, max_words=100)
    assert len(chunks) > 1


def test_no_chunk_greatly_exceeds_the_limit():
    text = "\n".join(f"paragraph {i} " + "word " * 50 for i in range(10))
    for chunk in chunk_text(text, max_words=100):
        assert len(chunk.split()) <= 100


def test_one_enormous_paragraph_is_split_rather_than_dropped():
    chunks = chunk_text("word " * 1000, max_words=100)
    assert len(chunks) == 10


def test_paragraphs_are_kept_whole_when_they_fit():
    text = "First paragraph here.\nSecond paragraph here."
    assert chunk_text(text, max_words=100) == [text]


def test_empty_text_gives_no_chunks():
    assert chunk_text("") == []


# -- the whole job ---------------------------------------------------------


def test_a_prepared_job_is_ready_for_the_laptop():
    job = Job(question="what happened?", urls=["https://example.com/a"])
    with _serving(ARTICLE) as client:
        job = prepare(job, client=client)

    assert job.state is JobState.PREPARED
    assert job.chunks
    assert job.prepared_at


def test_one_dead_link_does_not_ruin_the_batch():
    def handler(request):
        if "dead" in str(request.url):
            raise httpx.ConnectError("no such host")
        return httpx.Response(
            200, text=ARTICLE, headers={"content-type": "text/html"}
        )

    job = Job(
        question="what happened?",
        urls=["https://good.example/a", "https://dead.example/b"],
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        job = prepare(job, client=client)

    assert job.state is JobState.PREPARED
    assert len(job.good_sources) == 1
    assert len(job.failed_sources) == 1


def test_a_job_where_everything_failed_says_so():
    def handler(request):
        raise httpx.ConnectError("the internet is gone")

    job = Job(question="what happened?", urls=["https://a.example"])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        job = prepare(job, client=client)

    assert job.state is JobState.FAILED
    assert "none of the pages" in job.error


def test_preparation_never_raises():
    """It runs unattended. A traceback nobody sees helps nobody."""

    class Exploding:
        def get(self, *a, **k):
            raise RuntimeError("catastrophe")

    job = Job(question="q", urls=["https://a.example"])
    job = prepare(job, client=Exploding())  # type: ignore[arg-type]

    assert job.state is JobState.FAILED
    assert job.error


def test_a_job_with_no_urls_is_just_a_question():
    job = prepare(Job(question="what is 2+2?"))
    assert job.state is JobState.PREPARED
    assert job.prompt() == "what is 2+2?"


# -- the handover ----------------------------------------------------------


def test_the_prompt_carries_the_material_and_the_question():
    job = Job(question="does it run?", urls=["https://example.com/a"])
    with _serving(ARTICLE) as client:
        job = prepare(job, client=client)

    prompt = job.prompt()
    assert "first real paragraph" in prompt
    assert "QUESTION: does it run?" in prompt


def test_the_prompt_tells_the_model_not_to_invent():
    job = Job(question="q", urls=["https://example.com/a"])
    with _serving(ARTICLE) as client:
        job = prepare(job, client=client)

    assert "say so rather than guessing" in job.prompt()


def test_the_context_is_capped_so_it_cannot_overflow():
    big = "<p>" + "word " * 5000 + "</p>"
    job = Job(question="q", urls=["https://example.com/a"])
    with _serving(f"<html><title>T</title><body>{big}</body></html>") as client:
        job = prepare(job, client=client)

    assert len(job.context(max_words=100).split()) < 200


def test_the_source_is_named_in_the_context():
    job = Job(question="q", urls=["https://example.com/a"])
    with _serving(ARTICLE) as client:
        job = prepare(job, client=client)

    assert "The Real Title" in job.context()


def test_a_job_gets_a_stable_id_and_a_timestamp():
    job = Job(question="q")
    assert len(job.id) == 12
    assert job.created_at


def test_two_jobs_do_not_collide():
    assert Job(question="a").id != Job(question="b").id


# -- refusals must be named, not numbered -----------------------------------


class _FakeResponse:
    def __init__(self, status, headers=None, text=""):
        self.status_code = status
        self.headers = headers or {}
        self.text = text


def test_a_cloudflare_challenge_is_recognised():
    from apexis_shared.prepare import _is_bot_challenge

    assert _is_bot_challenge(_FakeResponse(403, {"cf-mitigated": "challenge"}))


def test_a_challenge_page_body_is_recognised():
    from apexis_shared.prepare import _is_bot_challenge

    assert _is_bot_challenge(_FakeResponse(403, text="<h1>Just a moment...</h1>"))


def test_an_ordinary_403_is_not_called_a_bot_check():
    from apexis_shared.prepare import _is_bot_challenge

    assert not _is_bot_challenge(_FakeResponse(403, {"server": "nginx"},
                                               "Forbidden"))


def test_a_broken_response_does_not_crash_the_detector():
    from apexis_shared.prepare import _is_bot_challenge

    class Broken:
        @property
        def headers(self):
            raise RuntimeError("nope")

    assert _is_bot_challenge(Broken()) is False


def test_we_look_like_a_browser_because_bot_strings_get_blocked():
    from apexis_shared.prepare import BROWSER_HEADERS

    assert "Mozilla/5.0" in BROWSER_HEADERS["User-Agent"]
    assert "APEXIS" not in BROWSER_HEADERS["User-Agent"]
