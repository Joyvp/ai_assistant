"""``apexis research`` — the Pi gathers, the laptop thinks.

Ask a question with some links. The Pi (or this laptop, if there is no Pi)
fetches and prepares the material; then phi3 loads **once** and answers over
the lot. One model load instead of one per page, over text that was collected
while nobody was waiting.

The preparation deliberately contains no model code, so it runs anywhere —
which is why this works today with the Pi switched off, and simply gets
faster and more autonomous when the Pi is on.
"""

from __future__ import annotations

import json
import pathlib
import re
from datetime import datetime, timezone

from apexis_shared.jobs import Job, JobState
from apexis_shared.prepare import prepare

from apexis_desktop import away, mail
from apexis_desktop.brain.ollama import OllamaError, OllamaProvider
from apexis_desktop.brain.lifecycle import ModelLifecycle
from apexis_desktop.nodes import load_fleet
from apexis_desktop.orchestrator import LAPTOP_MODEL


DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
CYAN = "\033[36m"
OFF = "\033[0m"


def jobs_dir() -> pathlib.Path:
    import os

    base = os.getenv("XDG_DATA_HOME")
    root = pathlib.Path(base) if base else pathlib.Path.home() / ".local" / "share"
    path = root / "apexis" / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(job: Job) -> pathlib.Path:
    path = jobs_dir() / f"{job.id}.json"
    path.write_text(job.model_dump_json(indent=2))
    return path


def load(job_id: str) -> Job | None:
    path = jobs_dir() / f"{job_id}.json"
    try:
        return Job.model_validate_json(path.read_text())
    except (OSError, ValueError):
        return None


def all_jobs() -> list[Job]:
    found: list[Job] = []
    for path in sorted(jobs_dir().glob("*.json")):
        try:
            found.append(Job.model_validate_json(path.read_text()))
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda j: j.created_at, reverse=True)


_URL = re.compile(r"https?://\S+")


def split_question(words: list[str]) -> tuple[str, list[str]]:
    """Separate the question from the links in a free-form command line."""
    urls = [w for w in words if _URL.match(w)]
    question = " ".join(w for w in words if not _URL.match(w)).strip()
    return question, urls


def ask(question: str, urls: list[str], *, answer_now: bool = True) -> int:
    """Create a job, prepare it, and optionally answer it immediately."""
    if not question:
        print(f"\n  {DIM}usage: apexis research \"what changed?\" "
              f"https://example.com/a https://example.com/b{OFF}\n")
        return 1

    fleet = load_fleet()
    on_pi = fleet.pi is not None and fleet.pi.is_up()

    job = Job(question=question, urls=urls)

    print()
    print(f"  {BOLD}{question}{OFF}")
    if urls:
        where = "the Pi" if on_pi else "this laptop"
        print(f"  {DIM}gathering {len(urls)} page(s) on {where}...{OFF}")

    # The Pi has no APEXIS service on it, so preparation runs here for now.
    # It is pure IO, so the only thing the Pi would add today is doing it
    # while the laptop is shut - which is the scheduling half, not this half.
    job = prepare(job)
    save(job)

    if job.state is JobState.FAILED:
        print(f"  {RED}{job.error}{OFF}\n")
        for source in job.failed_sources:
            print(f"    {DIM}{source.url} — {source.error}{OFF}")
        print()
        return 1

    if urls:
        print(f"  {GREEN}✓{OFF} {job.summary()}")
        dropped = len(job.urls) - len(job.sources)
        if dropped > 0:
            word = "page" if dropped == 1 else "pages"
            print(f"    {DIM}{dropped} duplicate {word} collapsed — "
                  f"same text, read once{OFF}")
        for source in job.failed_sources:
            print(f"    {YELLOW}skipped{OFF} {DIM}{source.url} — "
                  f"{source.error}{OFF}")
        print()

    if not answer_now:
        print(f"  {DIM}saved as {job.id} — answer it later with:{OFF}")
        print(f"    {BOLD}apexis research answer {job.id}{OFF}\n")
        return 0

    return answer(job)


def answer(job: Job) -> int:
    """Load the laptop model once, answer, unload."""
    fleet = load_fleet()
    host = fleet.laptop.host
    lifecycle = ModelLifecycle(host=host)

    already_up = lifecycle.is_resident(LAPTOP_MODEL)
    if already_up:
        print(f"  {DIM}{LAPTOP_MODEL} is already loaded — reusing it{OFF}")
    else:
        print(f"  {DIM}waking {LAPTOP_MODEL} — one load for the whole job{OFF}")
    print(f"{CYAN}apexis{OFF} › ", end="", flush=True)

    collected: list[str] = []
    try:
        with lifecycle.borrowed(LAPTOP_MODEL, unload_after=True):
            provider = OllamaProvider(model=LAPTOP_MODEL, host=host)
            try:
                import sys

                for chunk in provider.stream_messages(
                    [{"role": "user", "content": job.prompt()}]
                ):
                    sys.stdout.write(chunk)
                    sys.stdout.flush()
                    collected.append(chunk)
            finally:
                provider.close()
    except (OllamaError, RuntimeError) as exc:
        print(f"\n  {RED}{exc}{OFF}\n")
        job.state = JobState.FAILED
        job.error = str(exc)
        save(job)
        return 1

    text = "".join(collected).strip()
    job.answer = text
    job.answered_by = LAPTOP_MODEL
    job.answered_at = datetime.now(timezone.utc).isoformat()
    job.state = JobState.DONE
    save(job)

    print("\n")
    if job.good_sources:
        print(f"  {DIM}sources:{OFF}")
        for source in job.good_sources:
            print(f"    {DIM}· {source.title or source.url}{OFF}")
    state = "left loaded" if already_up else "unloaded"
    print(f"  {DIM}job {job.id} · {LAPTOP_MODEL} {state}{OFF}\n")

    _report_if_away(job)
    return 0


def _report_if_away(job) -> None:
    """Email the answer, but only if the user said they were out.

    A machine that emails you while you are looking at it is spam, so the
    user's own away switch is the gate. Failure here is silent by design:
    a notification must never be able to break the job it describes.
    """
    if not away.is_away():
        return

    subject, body = mail.job_report(job)
    if mail.notify(subject, body):
        print(f"  {DIM}you're away — emailed to {mail.owner()}{OFF}\n")
    else:
        print(f"  {YELLOW}you're away, but email isn't set up{OFF}")
        print(f"  {DIM}apexis email setup{OFF}\n")


def show_list() -> int:
    found = all_jobs()
    if not found:
        print(f"\n  {DIM}no research jobs yet{OFF}")
        print(f"  {DIM}apexis research \"what changed in python 3.13?\" "
              f"https://docs.python.org/3/whatsnew/3.13.html{OFF}\n")
        return 0

    print()
    for job in found[:20]:
        colour = {
            JobState.DONE: GREEN,
            JobState.PREPARED: YELLOW,
            JobState.QUEUED: DIM,
            JobState.FAILED: RED,
        }[job.state]
        print(f"  {colour}●{OFF} {job.id}  {job.question[:48]}")
        print(f"      {DIM}{job.state.label} · {job.summary()}{OFF}")
    print()
    return 0


def show_one(job_id: str) -> int:
    job = load(job_id)
    if job is None:
        print(f"\n  {RED}no job {job_id}{OFF}\n")
        return 1

    print()
    print(f"  {BOLD}{job.question}{OFF}")
    print(f"  {DIM}{job.state.label} · {job.summary()}{OFF}\n")

    for source in job.sources:
        mark = f"{GREEN}✓{OFF}" if source.ok else f"{RED}✗{OFF}"
        detail = f"{source.words} words" if source.ok else source.error
        print(f"    {mark} {source.title or source.url}  {DIM}{detail}{OFF}")

    if job.answer:
        print(f"\n{job.answer}\n")
    print()
    return 0


def main(action: str | None = None, words: list[str] | None = None) -> int:
    words = words or []

    if action in {None, "list"}:
        return show_list()

    if action == "show":
        return show_one(words[0]) if words else show_list()

    if action == "answer":
        if not words:
            print(f"\n  {DIM}usage: apexis research answer <job-id>{OFF}\n")
            return 1
        job = load(words[0])
        if job is None:
            print(f"\n  {RED}no job {words[0]}{OFF}\n")
            return 1
        return answer(job)

    if action == "prep":
        question, urls = split_question(words)
        return ask(question, urls, answer_now=False)

    # Anything else is the question itself.
    question, urls = split_question([action, *words])
    return ask(question, urls)
