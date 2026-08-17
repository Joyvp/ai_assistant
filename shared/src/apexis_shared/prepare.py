"""The Pi's half of the work — everything that needs no intelligence.

Fetching a page, throwing away the navigation and the cookie banner, keeping
the article, splitting it into model-sized pieces, and noticing that two of
your five links are the same story syndicated twice. All of it is plain code,
which is exactly why it belongs on the machine that is always on rather than
the machine with the good model.

No external libraries: a Pi should not need a scraping stack to read a page,
and every dependency is another thing to break on a machine in a drawer.
"""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import httpx

from apexis_shared.jobs import Chunk, Job, JobState, Source


# Sized for phi3's context with room for the question and the answer.
DEFAULT_CHUNK_WORDS = 400
DEFAULT_TIMEOUT = 20.0

# A Pi on a home connection is slow. Fetching is IO-bound, so do several at
# once, but not so many that a cheap router notices.
DEFAULT_WORKERS = 4

# Whole elements whose contents are never article text.
_DROP_ELEMENTS = re.compile(
    r"<(script|style|nav|header|footer|aside|form|noscript|svg|iframe)\b[^>]*>"
    r".*?</\1>",
    re.IGNORECASE | re.DOTALL,
)

_COMMENTS = re.compile(r"<!--.*?-->", re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)

# Lines that are navigation furniture rather than prose.
_FURNITURE = re.compile(
    r"^(home|menu|search|log ?in|sign ?up|subscribe|share|tweet|advertisement|"
    r"cookie|accept all|skip to content|newsletter|follow us|read more|"
    r"related articles?|comments?)\s*$",
    re.IGNORECASE,
)


def extract_text(raw_html: str) -> tuple[str, str]:
    """Pull (title, readable text) out of an HTML document.

    Deliberately crude. A real readability implementation is a large
    dependency and a maintenance burden; dropping the obvious non-content
    elements and the one-word navigation lines gets most of the way there,
    and a model tolerates the rest.
    """
    title_match = _TITLE.search(raw_html)
    title = ""
    if title_match:
        title = html.unescape(_TAGS.sub("", title_match.group(1))).strip()

    body = _COMMENTS.sub(" ", raw_html)
    body = _DROP_ELEMENTS.sub(" ", body)

    # Keep paragraph boundaries; a wall of text is harder for a small model.
    body = re.sub(r"</(p|div|section|article|h[1-6]|li|tr)>", "\n", body,
                  flags=re.IGNORECASE)
    body = re.sub(r"<br\s*/?>", "\n", body, flags=re.IGNORECASE)

    body = _TAGS.sub(" ", body)
    body = html.unescape(body)

    lines: list[str] = []
    for line in body.splitlines():
        cleaned = " ".join(line.split())
        if not cleaned or _FURNITURE.match(cleaned):
            continue
        # Single words on their own line are almost always menu items.
        if len(cleaned.split()) < 3 and not cleaned.endswith((".", "?", "!")):
            continue
        lines.append(cleaned)

    return title, "\n".join(lines)


# Plenty of sites block on the User-Agent string alone, so looking like the
# browser the user would have opened themselves gets us through those. It does
# NOT get past a real bot challenge - Cloudflare's "Cf-Mitigated: challenge"
# wants JavaScript executed, and no header will fake that. Those pages are
# simply unreadable to a tool like this, and say so rather than pretending.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}


def _is_bot_challenge(response) -> bool:
    """Whether a refusal is a bot check rather than a real permission error."""
    try:
        headers = response.headers
        if headers.get("cf-mitigated"):
            return True
        server = str(headers.get("server", "")).lower()
        if "cloudflare" in server:
            return True
        body = str(getattr(response, "text", ""))[:4000].lower()
        return any(
            marker in body
            for marker in ("just a moment", "attention required",
                           "enable javascript", "checking your browser",
                           "captcha")
        )
    except Exception:
        return False


def fetch(url: str, *, timeout: float = DEFAULT_TIMEOUT,
          client: httpx.Client | None = None) -> Source:
    """Fetch one page. Never raises — a failure is recorded on the Source.

    A job with three good pages and two dead links is still worth answering,
    so one bad URL must not take down the batch.
    """
    now = datetime.now(timezone.utc).isoformat()
    owns = client is None
    client = client or httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
        headers=BROWSER_HEADERS,
    )

    try:
        response = client.get(url)
        response.raise_for_status()

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type and "text" not in content_type:
            return Source(
                url=url, fetched_at=now,
                error=f"not a readable page ({content_type or 'unknown type'})",
            )

        title, text = extract_text(response.text)
        if not text.strip():
            return Source(url=url, title=title, fetched_at=now,
                          error="page had no readable text")

        return Source(url=url, title=title, text=text, fetched_at=now)

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        # A bare "HTTP 403" sends people hunting for a bug on their end. This
        # is a site refusing robots, which is a different thing entirely and
        # is not fixable from here.
        if status in (403, 503) and _is_bot_challenge(exc.response):
            return Source(
                url=url, fetched_at=now,
                error="the site blocks automated readers (bot check)",
            )
        if status == 403:
            return Source(url=url, fetched_at=now,
                          error="the site refused us (HTTP 403)")
        if status == 404:
            return Source(url=url, fetched_at=now,
                          error="page not found (HTTP 404)")
        return Source(url=url, fetched_at=now, error=f"HTTP {status}")
    except httpx.HTTPError as exc:
        return Source(url=url, fetched_at=now, error=f"could not fetch: {exc}")
    except Exception as exc:  # a Pi should not die on one weird page
        return Source(url=url, fetched_at=now, error=f"unexpected: {exc}")
    finally:
        if owns:
            client.close()


def fetch_all(
    urls: list[str],
    *,
    workers: int = DEFAULT_WORKERS,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> list[Source]:
    """Fetch several pages at once, preserving the order given."""
    if not urls:
        return []

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=min(workers, len(urls))) as pool:
        return list(
            pool.map(lambda u: fetch(u, timeout=timeout, client=client), urls)
        )


def deduplicate(sources: list[Source]) -> list[Source]:
    """Drop sources whose text is identical to one already seen.

    The same wire story appears on a dozen sites. Summarising it twelve times
    wastes the laptop's one model load.
    """
    seen: set[str] = set()
    kept: list[Source] = []

    for source in sources:
        if not source.ok:
            kept.append(source)  # keep failures for the report
            continue
        fingerprint = source.fingerprint
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        kept.append(source)

    return kept


def chunk_text(text: str, *, max_words: int = DEFAULT_CHUNK_WORDS) -> list[str]:
    """Split text into pieces of at most ``max_words``, on paragraph breaks.

    Splitting mid-sentence costs the model more than a slightly uneven chunk
    does, so paragraphs are kept whole unless one is enormous on its own.
    """
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    count = 0

    for paragraph in paragraphs:
        words = paragraph.split()

        if len(words) > max_words:
            if current:
                chunks.append("\n".join(current))
                current, count = [], 0
            for i in range(0, len(words), max_words):
                chunks.append(" ".join(words[i:i + max_words]))
            continue

        if count + len(words) > max_words and current:
            chunks.append("\n".join(current))
            current, count = [], 0

        current.append(paragraph)
        count += len(words)

    if current:
        chunks.append("\n".join(current))

    return chunks


def prepare(
    job: Job,
    *,
    max_words: int = DEFAULT_CHUNK_WORDS,
    workers: int = DEFAULT_WORKERS,
    client: httpx.Client | None = None,
) -> Job:
    """Do the Pi's whole half of the work: fetch, clean, dedupe, chunk.

    Returns the same job, filled in and marked PREPARED. Never raises: a job
    that fails entirely comes back FAILED with a reason, because this runs
    unattended at 3am and a traceback nobody sees helps nobody.
    """
    try:
        sources = fetch_all(job.urls, workers=workers, client=client)
        sources = deduplicate(sources)

        chunks: list[Chunk] = []
        for source in sources:
            if not source.ok:
                continue
            for index, piece in enumerate(
                chunk_text(source.text, max_words=max_words)
            ):
                chunks.append(
                    Chunk(source_url=source.url, index=index, text=piece)
                )

        job.sources = sources
        job.chunks = chunks
        job.prepared_at = datetime.now(timezone.utc).isoformat()

        if not chunks and job.urls:
            job.state = JobState.FAILED
            job.error = "none of the pages could be read"
        else:
            job.state = JobState.PREPARED

    except Exception as exc:
        job.state = JobState.FAILED
        job.error = f"preparation failed: {exc}"

    return job
