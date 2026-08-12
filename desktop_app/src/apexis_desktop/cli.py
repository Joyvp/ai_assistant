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
