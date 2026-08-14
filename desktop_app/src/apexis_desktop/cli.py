"""Top-level APEXIS command-line interface."""

from __future__ import annotations

import argparse
import os

from apexis_desktop.brain.mock import MockProvider
from apexis_desktop.chat import run_chat
from apexis_desktop.status import DEFAULT_CORE_URL, status_main


def build_parser() -> argparse.ArgumentParser:
    """Create the APEXIS command tree."""

    parser = argparse.ArgumentParser(prog="apexis")
    parser.add_argument(
        "--url",
        default=os.environ.get("APEXIS_CORE_URL", DEFAULT_CORE_URL),
        help="APEXIS Core base URL",
    )

    commands = parser.add_subparsers(dest="command")

    status_parser = commands.add_parser("status", help="Check Headquarters status")
    status_parser.add_argument("--url", dest="status_url", help="Override Core URL")

    commands.add_parser("chat", help="Start local chat with the Mock Brain")

    later_parser = commands.add_parser(
        "later", help="Queue a question to be answered while you're out"
    )
    later_parser.add_argument("words", nargs="*", help="the question and any links")

    watch_parser = commands.add_parser(
        "watch", help="Work through the queue"
    )
    watch_parser.add_argument(
        "--once", action="store_true", help="drain once and exit"
    )
    watch_parser.add_argument(
        "--interval", type=int, default=60, help="seconds between checks"
    )

    commands.add_parser("queue", help="What's waiting to be worked on")

    away_parser = commands.add_parser(
        "away", help="Tell APEXIS you're going out (it may email you)"
    )
    away_parser.add_argument("note", nargs="*", help="optional: where you're going")

    commands.add_parser("home", help="Tell APEXIS you're back")

    email_parser = commands.add_parser(
        "email", help="How APEXIS reaches you when you're out"
    )
    email_parser.add_argument(
        "action",
        nargs="?",
        default="show",
        choices=["show", "setup", "test", "check", "doctor", "outbox",
                 "from", "password", "to", "approve", "drop"],
        help="what to do",
    )
    # Google displays app passwords as "abcd efgh ijkl mnop", so people
    # paste them with spaces. Take every remaining word and join it.
    email_parser.add_argument("value", nargs="*", help="the value to set")

    research_parser = commands.add_parser(
        "research",
        help="Ask a question about some web pages — gather, then think once",
    )
    research_parser.add_argument(
        "action",
        nargs="?",
        help="the question, or: list, show <id>, answer <id>, prep <question>",
    )
    research_parser.add_argument(
        "words", nargs="*", help="more of the question, and any URLs"
    )

    cloud_parser = commands.add_parser(
        "cloud", help="Tier 3 — what happens when a task is too hard"
    )
    cloud_parser.add_argument(
        "action",
        nargs="?",
        default="show",
        choices=["show", "on", "off", "handoff", "api", "key", "provider", "providers"],
        help="show (default), on, off, handoff, api, key, provider <name>",
    )
    cloud_parser.add_argument("value", nargs="?", help="key or provider name")

    nodes_parser = commands.add_parser(
        "nodes", help="See your machines, or connect the Pi"
    )
    nodes_parser.add_argument(
        "action",
        nargs="?",
        default="show",
        choices=["show", "find", "connect", "disconnect", "ping"],
        help="show (default), find, connect <address>, disconnect, ping",
    )
    nodes_parser.add_argument(
        "address",
        nargs="?",
        help="Pi address, e.g. 192.168.1.50 or apexis-pi.local",
    )

    talk_parser = commands.add_parser(
        "talk", help="Talk to the real local model (Ollama)"
    )
    talk_parser.add_argument(
        "--model",
        help="Model to use (default: $APEXIS_MODEL or phi3:mini)",
    )
    talk_parser.add_argument(
        "--host",
        help="Ollama host (default: $APEXIS_OLLAMA_HOST or http://127.0.0.1:11434)",
    )
    talk_parser.add_argument(
        "--keep-alive",
        dest="keep_alive",
        help="How long Ollama keeps the model loaded (default: 5m)",
    )
    talk_parser.add_argument(
        "--persona",
        help="Personality: casual, blunt, technical, friendly, assistant, custom",
    )
    talk_parser.add_argument(
        "--no-banner",
        action="store_true",
        help="Skip the startup banner",
    )

    return parser


def main() -> int:
    """Dispatch an APEXIS command."""

    args = build_parser().parse_args()

    if args.command == "chat":
        return run_chat(MockProvider())

    if args.command == "later":
        from apexis_desktop import research, worker

        question, urls = research.split_question(args.words)
        return worker.later(question, urls)

    if args.command == "watch":
        from apexis_desktop import worker

        return worker.watch(args.interval, once=args.once)

    if args.command == "queue":
        from apexis_desktop import worker

        return worker.show_queue()

    if args.command == "away":
        from apexis_desktop import mail_cli

        return mail_cli.go_away(" ".join(args.note))

    if args.command == "home":
        from apexis_desktop import mail_cli

        return mail_cli.come_home()

    if args.command == "email":
        from apexis_desktop import mail_cli

        return mail_cli.main(args.action, " ".join(args.value) or None)

    if args.command == "research":
        from apexis_desktop import research

        return research.main(args.action, args.words)

    if args.command == "cloud":
        from apexis_desktop import cloud_cli

        return cloud_cli.main(args.action, args.value)

    if args.command == "nodes":
        from apexis_desktop import fleet_cli

        return fleet_cli.main(args.action, args.address)

    if args.command == "talk":
        # Imported lazily so `apexis status` does not pay for it.
        from apexis_desktop import personality
        from apexis_desktop.brain.ollama import OllamaProvider
        from apexis_desktop.talk import run_talk

        provider = OllamaProvider(
            model=args.model,
            host=args.host,
            keep_alive=args.keep_alive,
            system_prompt=personality.get(args.persona) if args.persona else None,
        )
        try:
            return run_talk(
                provider,
                show_banner=not args.no_banner,
                persona=args.persona,
            )
        finally:
            provider.close()

    core_url = getattr(args, "status_url", None) or args.url
    return status_main(core_url)


if __name__ == "__main__":
    raise SystemExit(main())
