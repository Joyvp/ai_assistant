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
    return parser


def main() -> int:
    """Dispatch an APEXIS command."""

    args = build_parser().parse_args()

    if args.command == "chat":
        return run_chat(MockProvider())

    core_url = getattr(args, "status_url", None) or args.url
    return status_main(core_url)


if __name__ == "__main__":
    raise SystemExit(main())
