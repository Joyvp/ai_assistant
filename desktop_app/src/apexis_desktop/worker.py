"""The part that works while you don't.

Everything until now needed you sitting there. You typed, it answered. That
is a chat program with extra steps.

This is the loop that closes it: queue work, leave, come back to answers.

    apexis later "which small model for the pi" <links>
    apexis away
    ...
    apexis watch          (or a systemd timer, or the Pi)

The worker drains the queue one job at a time — prepare, answer, email if
you are out. It holds the model for a batch instead of loading it per job,
because loading phi3 five times to answer five questions is the exact waste
this project exists to avoid.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from apexis_shared.jobs import Job, JobState
from apexis_shared.prepare import prepare

from apexis_desktop import away, mail, research
from apexis_desktop.brain.lifecycle import ModelLifecycle
from apexis_desktop.brain.ollama import OllamaError, OllamaProvider
from apexis_desktop.nodes import load_fleet
from apexis_desktop.orchestrator import LAPTOP_MODEL


DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
CYAN = "\033[36m"
OFF = "\033[0m"

DEFAULT_INTERVAL = 60


def queued() -> list[Job]:
    """Jobs waiting to be worked on, oldest first."""
    return [j for j in reversed(research.all_jobs()) if j.state is JobState.QUEUED]


def add(question: str, urls: list[str]) -> Job:
    """Put a job on the queue without doing any of it."""
    job = Job(question=question, urls=urls)
    research.save(job)
    return job


def _answer_with(job: Job, provider) -> Job:
    """Run one prepared job against an already-loaded model."""
    chunks = []
    try:
        for piece in provider.stream_messages(
            [{"role": "user", "content": job.prompt()}]
        ):
            chunks.append(piece)
    except OllamaError as exc:
        job.state = JobState.FAILED
        job.error = str(exc)
        return job

    job.answer = "".join(chunks).strip()
    job.answered_by = LAPTOP_MODEL
    job.answered_at = datetime.now(timezone.utc).isoformat()
    job.state = JobState.DONE
    return job


def someone_is_using_the_model() -> bool:
    """True when a model is already resident on the laptop.

    Ollama keeps a model loaded for five minutes after the last request, so
    a resident model means somebody was talking to it very recently. A
    background drain that barges in would make their next reply crawl.
    """
    try:
        fleet = load_fleet()
        return ModelLifecycle(host=fleet.laptop.host).is_resident(LAPTOP_MODEL)
    except Exception:
        return False


def drain(*, verbose: bool = True, if_idle: bool = False) -> dict:
    """Work the whole queue in one model load. Returns a small summary.

    ``if_idle`` is for the automatic timer: it declines to run when someone
    is mid-conversation. A person typing at the machine outranks a queue.
    Manual runs never defer, because the user asked for it explicitly.
    """
    pending = queued()
    summary = {"done": 0, "failed": 0, "emailed": 0, "jobs": len(pending),
               "deferred": False}

    if not pending:
        return summary

    if if_idle and someone_is_using_the_model():
        summary["deferred"] = True
        if verbose:
            print(f"\n  {DIM}{LAPTOP_MODEL} is in use — leaving the queue "
                  f"for later{OFF}\n")
        return summary

    if verbose:
        word = "job" if len(pending) == 1 else "jobs"
        print(f"\n  {BOLD}{len(pending)} {word} queued{OFF}")

    # Gather first, with no model in memory at all. This is the Pi's share
    # of the work, and it is why none of it imports a model.
    for job in pending:
        if verbose:
            print(f"  {DIM}gathering: {job.question[:50]}{OFF}")
        prepared = prepare(job)
        research.save(prepared)

    ready = [j for j in (research.load(j.id) for j in pending)
             if j is not None and j.state is JobState.PREPARED]

    for job in pending:
        current = research.load(job.id)
        if current is not None and current.state is JobState.FAILED:
            summary["failed"] += 1

    if not ready:
        if verbose:
            print(f"  {YELLOW}nothing could be prepared{OFF}\n")
        return summary

    fleet = load_fleet()
    host = fleet.laptop.host
    lifecycle = ModelLifecycle(host=host)
    already_up = lifecycle.is_resident(LAPTOP_MODEL)

    if verbose:
        loads = "reusing" if already_up else "one load for all of them"
        print(f"  {DIM}waking {LAPTOP_MODEL} — {loads}{OFF}")

    try:
        with lifecycle.borrowed(LAPTOP_MODEL, unload_after=not already_up):
            provider = OllamaProvider(model=LAPTOP_MODEL, host=host)
            try:
                for job in ready:
                    answered = _answer_with(job, provider)
                    research.save(answered)

                    if answered.state is JobState.DONE:
                        summary["done"] += 1
                        if verbose:
                            print(f"  {GREEN}✓{OFF} {answered.question[:50]}")
                        if _report(answered):
                            summary["emailed"] += 1
                    else:
                        summary["failed"] += 1
                        if verbose:
                            print(f"  {RED}✗{OFF} {answered.question[:50]}")
            finally:
                provider.close()
    except (OllamaError, RuntimeError) as exc:
        if verbose:
            print(f"  {RED}the model is unreachable: {exc}{OFF}")
        return summary

    if verbose:
        state = "left loaded" if already_up else "unloaded"
        print(f"  {DIM}{LAPTOP_MODEL} {state}{OFF}")
        if summary["emailed"]:
            print(f"  {DIM}emailed {summary['emailed']} to "
                  f"{mail.owner()}{OFF}")
        print()

    return summary


def _report(job: Job) -> bool:
    """Email a finished job, but only if the user said they were out."""
    if not away.is_away():
        return False
    subject, body = mail.job_report(job)
    return mail.notify(subject, body)


def watch(
    interval: int = DEFAULT_INTERVAL,
    *,
    once: bool = False,
    if_idle: bool = False,
) -> int:
    """Keep draining the queue until interrupted.

    ``once`` drains and exits, which is the form a cron entry or systemd
    timer wants. The long-running form is for a terminal you leave open.
    """
    if once:
        drain(if_idle=if_idle)
        return 0

    print(f"\n  {CYAN}●{OFF} {BOLD}watching{OFF} "
          f"{DIM}— checking every {interval}s, ctrl-c to stop{OFF}")
    if away.is_away():
        print(f"  {DIM}you're away, so finished jobs will be emailed{OFF}")
    else:
        print(f"  {DIM}you're home, so nothing will be emailed{OFF}")
    print()

    try:
        while True:
            drain(if_idle=if_idle)
            time.sleep(interval)
    except KeyboardInterrupt:
        print(f"\n  {DIM}stopped{OFF}\n")
    return 0


def show_queue() -> int:
    pending = queued()

    print()
    if not pending:
        print(f"  {DIM}nothing queued{OFF}")
        print(f"  {DIM}apexis later \"your question\" https://a-link{OFF}\n")
        return 0

    word = "job" if len(pending) == 1 else "jobs"
    print(f"  {BOLD}{len(pending)} {word} waiting{OFF}")
    print()
    for job in pending:
        print(f"  {DIM}·{OFF} {job.question[:56]}")
        print(f"    {DIM}{len(job.urls)} link(s) · {job.id}{OFF}")
    print()
    print(f"  {DIM}work through them now:  {BOLD}apexis watch --once{OFF}\n")
    return 0


def later(question: str, urls: list[str]) -> int:
    if not question:
        print(f"\n  {DIM}usage: apexis later \"what changed?\" "
              f"https://example.com{OFF}\n")
        return 1

    job = add(question, urls)

    print()
    print(f"  {GREEN}queued{OFF}  {job.question}")
    print(f"  {DIM}{len(urls)} link(s) · nothing has run yet{OFF}")
    print()
    if away.is_away():
        print(f"  {DIM}you're away — the answer will be emailed to "
              f"{mail.owner()}{OFF}")
    else:
        print(f"  {DIM}run it with:  {BOLD}apexis watch --once{OFF}")
        print(f"  {DIM}or leave it for when you go out:  "
              f"{BOLD}apexis away{OFF}")
    print()
    return 0
