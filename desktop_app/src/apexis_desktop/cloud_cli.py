"""``apexis cloud`` — see and change what tier 3 does."""

from __future__ import annotations

from apexis_desktop import cloud


DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
OFF = "\033[0m"


MODE_BLURB = {
    "off": "nothing leaves this machine, ever. Hard tasks get a weaker "
           "local answer.",
    "handoff": "hard tasks produce a prompt for you to paste into any free "
               "chat. Nothing is sent automatically.",
    "api": "hard tasks are sent to a provider over the internet, announced "
           "every time.",
}


def show() -> int:
    mode = cloud.get_mode()
    provider = cloud.get_provider()
    spec = cloud.PROVIDERS[provider]
    has_key = bool(cloud.api_key())

    print()
    print(f"  {BOLD}Tier 3 — when a task is beyond phi3{OFF}")
    print()

    colour = {"off": DIM, "handoff": GREEN, "api": YELLOW}[mode]
    print(f"  mode      {colour}{mode}{OFF}")
    print(f"            {DIM}{MODE_BLURB[mode]}{OFF}")
    print()

    if mode == "api":
        print(f"  provider  {spec['label']}")
        print(f"  model     {DIM}{spec['model']}{OFF}")
        print(f"  key       {GREEN + 'set' + OFF if has_key else RED + 'missing' + OFF}")
        print(f"  free tier {DIM}{spec['free']}{OFF}")
        print(f"  training  {DIM}{spec['trains']}{OFF}")
        if not has_key:
            print()
            print(f"  {YELLOW}No key, so nothing can actually go online.{OFF}")
            print(f"  {DIM}Get one free at {spec['signup']}{OFF}")
            print(f"  {DIM}then:  apexis cloud key YOUR_KEY{OFF}")
        print()

    print(f"  {DIM}change with:{OFF}")
    print(f"    {BOLD}apexis cloud off{OFF}      {DIM}fully offline{OFF}")
    print(f"    {BOLD}apexis cloud handoff{OFF}  {DIM}give me a prompt to paste{OFF}")
    print(f"    {BOLD}apexis cloud api{OFF}      {DIM}call a provider directly{OFF}")
    print()
    return 0


def providers() -> int:
    print()
    print(f"  {BOLD}Providers{OFF}  {DIM}(all OpenAI-compatible){OFF}")
    print()
    current = cloud.get_provider()
    for name, spec in cloud.PROVIDERS.items():
        marker = "→" if name == current else " "
        print(f"  {marker} {BOLD}{name}{OFF}")
        print(f"      {DIM}{spec['free']}{OFF}")
        print(f"      {DIM}trains on your data: {spec['trains']}{OFF}")
        print(f"      {DIM}{spec['signup']}{OFF}")
        print()
    print(f"  {DIM}switch with:  apexis cloud provider groq{OFF}\n")
    return 0


def set_mode(mode: str) -> int:
    cloud.set_mode(mode)
    print(f"\n  tier 3 is now {BOLD}{mode}{OFF}")
    print(f"  {DIM}{MODE_BLURB[mode]}{OFF}\n")

    if mode == "api" and not cloud.api_key():
        spec = cloud.PROVIDERS[cloud.get_provider()]
        print(f"  {YELLOW}You have no key for {spec['label']} yet,{OFF}")
        print(f"  {YELLOW}so nothing can go online until you add one.{OFF}\n")
        print(f"  {DIM}Free, no credit card: {spec['signup']}{OFF}")
        print(f"  {DIM}Then:  apexis cloud key YOUR_KEY{OFF}\n")

    return 0


def main(action: str = "show", value: str | None = None) -> int:
    if action in {"off", "handoff", "api"}:
        return set_mode(action)

    if action == "on":
        # "on" is ambiguous, so pick the one that costs nothing.
        return set_mode("handoff")

    if action == "provider":
        if not value:
            return providers()
        try:
            cloud.set_provider(value)
        except ValueError as exc:
            print(f"\n  {RED}{exc}{OFF}\n")
            return 1
        spec = cloud.PROVIDERS[value]
        print(f"\n  provider is now {BOLD}{spec['label']}{OFF}")
        print(f"  {DIM}{spec['free']}{OFF}\n")
        return 0

    if action == "key":
        if not value:
            print(f"\n  {DIM}usage: apexis cloud key YOUR_KEY{OFF}\n")
            return 1
        provider = cloud.get_provider()
        cloud.set_api_key(provider, value)
        print(f"\n  {GREEN}key saved{OFF} for {cloud.PROVIDERS[provider]['label']}")
        print(f"  {DIM}stored in {cloud.CONFIG_PATH} (readable only by you){OFF}\n")
        if cloud.get_mode() != "api":
            print(f"  {DIM}turn it on with:  apexis cloud api{OFF}\n")
        return 0

    return show()
