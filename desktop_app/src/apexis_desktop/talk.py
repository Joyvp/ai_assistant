"""Streaming APEXIS chat — the interface you actually talk to.

Separate from ``chat.py`` (which drives the non-streaming MockProvider and is
covered by the existing tests). This one prints tokens as they arrive.
"""

from __future__ import annotations

import os
import sys

from apexis_desktop import personality
from apexis_desktop.brain.ollama import OllamaError, OllamaProvider


BANNER = """\
\033[36m
    ###   ######  ####### #     # ###  #####
   #   #  #     # #        #   #   #  #     #
  #     # #     # #         # #    #  #
  ####### ######  #####      #     #   #####
  #     # #       #         # #    #        #
  #     # #       #        #   #   #  #     #
  #     # #       ####### #     # ###  #####
\033[0m
  \033[2mlocal · private · yours\033[0m
"""

HELP = """\
  \033[1mCommands\033[0m
    /help     show this
    /new      start a fresh conversation (clears context)
    /model    show the current model
    /models   list installed models
    /context  how many messages are in context
    /persona  list personalities
    /persona <name>  switch personality
    /exit     quit
"""

DIM = "\033[2m"
CYAN = "\033[36m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
OFF = "\033[0m"

EXIT_COMMANDS = {"/exit", "/quit", "/q"}


def _preflight(provider: OllamaProvider) -> int:
    """Check Ollama is up and the model is present. Return an exit code."""
    if not provider.is_available():
        print(f"{RED}  Ollama is not running.{OFF}\n")
        print("  Start it in another terminal:")
        print(f"    {BOLD}ollama serve{OFF}\n")
        print("  Not installed yet?")
        print(f"    {BOLD}curl -fsSL https://ollama.com/install.sh | sh{OFF}\n")
        return 1

    try:
        installed = provider.installed_models()
    except OllamaError as exc:
        print(f"{RED}  {exc}{OFF}")
        return 1

    if not installed:
        print(f"{YELLOW}  No models installed.{OFF}\n")
        print("  Pull a small one (about 2.2GB, good for 8GB RAM):")
        print(f"    {BOLD}ollama pull phi3:mini{OFF}\n")
        return 1

    # Accept a bare name matching a tagged model, e.g. "phi3" -> "phi3:mini".
    if not any(
        m == provider.model or m.split(":")[0] == provider.model.split(":")[0]
        for m in installed
    ):
        print(f"{YELLOW}  Model {provider.model!r} is not installed.{OFF}\n")
        print("  Installed:")
        for m in installed:
            print(f"    · {m}")
        print(f"\n  Pull it:  {BOLD}ollama pull {provider.model}{OFF}")
        print(f"  Or use one you have:  {BOLD}apexis talk --model {installed[0]}{OFF}\n")
        return 1

    return 0


def run_talk(
    provider: OllamaProvider | None = None,
    *,
    show_banner: bool = True,
    persona: str | None = None,
) -> int:
    """Run the streaming chat loop. Returns a process exit code."""
    provider = provider or OllamaProvider()

    if show_banner:
        print(BANNER)

    code = _preflight(provider)
    if code:
        return code

    current_persona = persona or os.getenv("APEXIS_PERSONA") or personality.DEFAULT_PERSONA

    print(f"  {DIM}model{OFF}  {provider.model}")
    print(f"  {DIM}host {OFF}  {provider.host}")
    print(f"  {DIM}style{OFF}  {current_persona}")
    print(f"  {DIM}/help for commands, /exit to quit{OFF}\n")

    while True:
        try:
            message = input(f"{GREEN}you{OFF} › ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n  {DIM}bye.{OFF}\n")
            return 0

        if not message:
            continue

        lowered = message.lower()

        if lowered in EXIT_COMMANDS:
            print(f"\n  {DIM}bye.{OFF}\n")
            return 0

        if lowered == "/help":
            print(HELP)
            continue

        if lowered == "/new":
            provider.reset()
            print(f"  {DIM}context cleared.{OFF}\n")
            continue

        if lowered == "/model":
            print(f"  {provider.model}\n")
            continue

        if lowered == "/models":
            try:
                for m in provider.installed_models():
                    marker = "→" if m == provider.model else " "
                    print(f"  {marker} {m}")
                print()
            except OllamaError as exc:
                print(f"  {RED}{exc}{OFF}\n")
            continue

        if lowered == "/context":
            print(f"  {provider.turns} messages in context\n")
            continue

        if lowered.startswith("/persona"):
            parts = message.split(maxsplit=1)
            if len(parts) == 1:
                print()
                for n in personality.available():
                    marker = "\u2192" if n == current_persona else " "
                    print(f"  {marker} {n:<11} {personality.describe(n)}")
                print(f"\n  {DIM}switch with:  /persona blunt{OFF}\n")
            else:
                choice = parts[1].strip().lower()
                if choice not in personality.available():
                    print(f"  {YELLOW}no persona {choice!r}{OFF}\n")
                else:
                    provider.system_prompt = personality.get(choice)
                    current_persona = choice
                    provider.reset()
                    print(f"  {DIM}persona -> {choice} (context cleared){OFF}\n")
            continue

        # --- generate -----------------------------------------------------
        print(f"{CYAN}apexis{OFF} › ", end="", flush=True)

        try:
            got_output = False
            for chunk in provider.stream(message):
                sys.stdout.write(chunk)
                sys.stdout.flush()
                got_output = True

            if not got_output:
                print(f"{DIM}(no response){OFF}", end="")

            print("\n")

        except OllamaError as exc:
            print(f"\n  {RED}{exc}{OFF}\n")
        except KeyboardInterrupt:
            # Ctrl-C cancels the current generation, not the session.
            print(f"\n  {DIM}(interrupted){OFF}\n")
        except Exception as exc:  # provider boundary
            print(f"\n  {RED}unexpected error: {exc}{OFF}\n")


def main() -> int:
    return run_talk()


if __name__ == "__main__":
    raise SystemExit(main())
