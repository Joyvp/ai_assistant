"""Streaming APEXIS chat — the interface you actually talk to.

Separate from ``chat.py`` (which drives the non-streaming MockProvider and is
covered by the existing tests). This one prints tokens as they arrive.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime

from apexis_desktop import personality
from apexis_desktop.memory import Memory
from apexis_desktop.brain.ollama import OllamaError, OllamaProvider
from apexis_desktop.nodes import load_fleet
from apexis_desktop.routed_chat import RoutedChat
from apexis_shared.routing import Tier


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

  \033[1mRouting\033[0m
    /nodes    which machines are up
    /route <task>    where would this go? (does not run it)
    /why      explain the last routing decision
    /direct   turn routing off — always use phi3

  \033[1mMemory\033[0m
    APEXIS saves facts about you as you talk. Every save is shown on
    screen, so nothing is remembered behind your back.

    /remember <fact>   remember something right now
    /facts             list what is remembered
    /forget <id>       delete one fact
    /memory            storage stats
    /auto              is automatic saving on?
    /auto on|off       turn automatic saving on or off

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
    memory: Memory | None = None,
    session: str | None = None,
    routed: bool = True,
) -> int:
    """Run the streaming chat loop. Returns a process exit code."""
    provider = provider or OllamaProvider()

    if show_banner:
        print(BANNER)

    code = _preflight(provider)
    if code:
        return code

    owns_memory = memory is None
    memory = memory or Memory()
    provider.memory = memory

    session = session or datetime.now().strftime("%Y%m%d-%H%M%S")

    current_persona = persona or os.getenv("APEXIS_PERSONA") or personality.DEFAULT_PERSONA

    fleet = load_fleet()
    chat = RoutedChat(
        fleet=fleet,
        system_prompt=personality.get(current_persona),
        memory=memory,
    )
    last: object | None = None  # most recent RoutedReply, for /why

    if routed:
        pi = f"pi {fleet.pi.host}" if fleet.pi else "no pi — laptop only"
        print(f"  {DIM}routing{OFF} on · {pi}")
    else:
        print(f"  {DIM}model{OFF}  {provider.model}")
    print(f"  {DIM}host {OFF}  {provider.host}")
    print(f"  {DIM}style{OFF}  {current_persona}")

    _stats = memory.stats()
    if _stats["facts"]:
        _auto = "auto-saving on" if memory.auto_capture else "auto-saving off"
        print(f"  {DIM}memory{OFF} {_stats['facts']} facts remembered, {_auto}")
    elif memory.auto_capture:
        print(f"  {DIM}memory{OFF} empty — it fills itself as you talk")
    print(f"  {DIM}/help for commands, /exit to quit{OFF}\n")

    while True:
        try:
            message = input(f"{GREEN}you{OFF} › ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n\n  {DIM}bye.{OFF}\n")
            if owns_memory:
                memory.close()
            return 0

        if not message:
            continue

        lowered = message.lower()

        if lowered in EXIT_COMMANDS:
            print(f"\n  {DIM}bye.{OFF}\n")
            if owns_memory:
                memory.close()
            return 0

        if lowered == "/help":
            print(HELP)
            continue

        if lowered == "/new":
            provider.reset()
            chat.reset()
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
            turns = chat.turns if routed else provider.turns
            print(f"  {turns} messages in context\n")
            continue

        if lowered == "/nodes":
            from apexis_desktop import fleet_cli

            fleet_cli.show(fleet)
            continue

        if lowered == "/direct":
            routed = not routed
            if routed:
                print(f"  {DIM}routing on — tasks go to the cheapest machine{OFF}\n")
            else:
                print(f"  {DIM}routing off — everything goes to "
                      f"{provider.model}{OFF}\n")
            continue

        if lowered.startswith("/route"):
            task = message[len("/route"):].strip()
            if not task:
                print(f"  {DIM}usage: /route build me a website{OFF}\n")
            else:
                plan = chat.route(task)
                d = plan.decision
                tier_name = d.tier.label
                if d.tier is Tier.PI_LOCAL and plan.where == "laptop":
                    tier_name = "cheap tier (no Pi — served here)"
                print(f"\n  would go to  {BOLD}{tier_name}{OFF}")
                print(f"  model        {plan.model} on the {plan.where}")
                print(f"  score        {d.complexity}")
                print(f"  because      {d.reason}")
                if d.signals:
                    print(f"  signals      {', '.join(d.signals)}")
                print()
            continue

        if lowered == "/why":
            if last is None:
                print(f"  {DIM}nothing routed yet{OFF}\n")
            else:
                d = last.decision
                tier_name = d.tier.label
                if d.tier is Tier.PI_LOCAL and last.where == "laptop":
                    # Honest about what actually happened: the cheap tier was
                    # chosen, but there is no Pi, so this laptop served it.
                    tier_name = "cheap tier (no Pi — served here)"
                print(f"\n  went to  {BOLD}{tier_name}{OFF}")
                print(f"  model    {last.model} on the {last.where}")
                print(f"  score    {d.complexity}")
                print(f"  because  {d.reason}")
                if d.signals:
                    print(f"  signals  {', '.join(d.signals)}")
                print(f"  took     {last.ms/1000:.1f}s")
                if last.freed_mb:
                    print(f"  freed    {last.freed_mb}MB")
                print()
            continue

        if lowered.startswith("/remember"):
            fact = message[len("/remember"):].strip()
            if not fact:
                print(f"  {DIM}usage: /remember my project is called APEXIS{OFF}\n")
            else:
                stored = memory.remember(fact)
                print(f"  {GREEN}remembered{OFF} [{stored.id}] {stored.text}\n")
            continue

        if lowered == "/facts":
            stored = memory.facts()
            if not stored:
                print(f"  {DIM}nothing remembered yet — try /remember{OFF}\n")
            else:
                print()
                for f in stored:
                    tag = f"{DIM}auto{OFF}" if f.auto else f"{DIM}told{OFF}"
                    print(f"  [{f.id}] {f.text}  {DIM}{f.when}{OFF} {tag}")
                print(f"\n  {DIM}auto = APEXIS noticed it; "
                      f"remove one with /forget <id>{OFF}\n")
            continue

        if lowered.startswith("/forget"):
            arg = message[len("/forget"):].strip()
            if not arg.isdigit():
                print(f"  {DIM}usage: /forget 3   (see ids with /facts){OFF}\n")
            elif memory.forget(int(arg)):
                print(f"  {DIM}forgot fact {arg}{OFF}\n")
            else:
                print(f"  {YELLOW}no fact with id {arg}{OFF}\n")
            continue

        if lowered == "/memory":
            st = memory.stats()
            print(f"\n  facts     {st['facts']}  ({st['auto']} saved automatically)")
            print(f"  messages  {st['messages']}")
            print(f"  sessions  {st['sessions']}")
            print(f"  autosave  {'on' if memory.auto_capture else 'off'}")
            print(f"  file      {memory.path}\n")
            continue

        if lowered.startswith("/auto"):
            arg = message[len("/auto"):].strip().lower()
            if arg in {"on", "off"}:
                memory.auto_capture = arg == "on"
                if arg == "on":
                    print(f"  {DIM}automatic saving on — new facts are announced"
                          f" as they are saved{OFF}\n")
                else:
                    print(f"  {DIM}automatic saving off — use /remember{OFF}\n")
            elif not arg:
                state = "on" if memory.auto_capture else "off"
                print(f"  automatic saving is {BOLD}{state}{OFF}\n")
            else:
                print(f"  {DIM}usage: /auto on   or   /auto off{OFF}\n")
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
                    chat.system_prompt = personality.get(choice)
                    current_persona = choice
                    provider.reset()
                    chat.reset()
                    print(f"  {DIM}persona -> {choice} (context cleared){OFF}\n")
            continue

        # --- notice anything durable, before answering ---------------------
        # Absorbing first means a fact mentioned in this very message is
        # already in the system prompt when the reply is generated: say
        # "i live in regina, whats the weather" and it knows on that turn.
        # Announced on screen, never silent — §15 rules out invisible memory.
        try:
            saved = memory.absorb(message)
        except Exception:
            saved = []  # memory must never break the conversation

        for fact in saved:
            print(
                f"  {DIM}· saved{OFF} {fact.text} "
                f"{DIM}[{fact.id}] — /forget {fact.id} to undo{OFF}"
            )
        if saved:
            print()

        # --- generate -----------------------------------------------------
        try:
            if routed:
                plan = chat.route(message, probe=True)
                last = plan

                # Show the destination before the reply, so a pause has a
                # visible reason: loading a 2.2GB model takes a few seconds.
                if plan.tier is Tier.CLOUD:
                    icon, colour = "☁", RED
                elif plan.tier is Tier.PI_LOCAL:
                    icon, colour = "▸", GREEN
                else:
                    icon, colour = "▲", YELLOW
                print(
                    f"  {colour}{icon}{OFF} {DIM}{plan.where} · {plan.model}"
                    f" · score {plan.decision.complexity}{OFF}",
                    flush=True,
                )
                shown = len(plan.notices)
                for note in plan.notices:
                    marker = f"{RED}!{OFF}" if plan.tier.leaves_home else f"{DIM}·{OFF}"
                    print(f"  {marker} {DIM}{note}{OFF}")

            print(f"{CYAN}apexis{OFF} › ", end="", flush=True)

            got_output = False
            _handoff_shown = False
            stream = chat.ask(message, plan) if routed else provider.stream(message)
            for chunk in stream:
                sys.stdout.write(chunk)
                sys.stdout.flush()
                got_output = True

            if not got_output:
                print(f"{DIM}(no response){OFF}", end="")

            print()

            if routed and plan.handoff:
                print(f"\n  {BOLD}Beyond the local models.{OFF} "
                      f"{DIM}Paste this anywhere:{OFF}\n")
                print(f"{DIM}{'─' * 60}{OFF}")
                print(plan.handoff)
                print(f"{DIM}{'─' * 60}{OFF}")
                from apexis_desktop import cloud as _cloud
                if _cloud.copy_to_clipboard(plan.handoff):
                    print(f"  {GREEN}copied to your clipboard{OFF}")
                print()

            if routed and plan.went_online:
                print(f"  {RED}! this answer came from the internet{OFF}")

            if routed:
                bits = [f"{plan.ms/1000:.1f}s"]
                if plan.freed_mb:
                    bits.append(f"{plan.freed_mb}MB freed")
                if plan.fell_back:
                    bits.append("fell back")
                print(f"  {DIM}{' · '.join(bits)}{OFF}")
                # Only notices added *during* the run — anything printed
                # before generation is not repeated.
                for note in plan.notices[shown:]:
                    print(f"  {DIM}{note}{OFF}")
            print()

            memory.log(session, "user", message)
            if routed and chat.history:
                memory.log(session, "assistant", chat.history[-1].assistant)
            elif not routed and provider._history:
                memory.log(session, "assistant", provider._history[-1]["content"])

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
