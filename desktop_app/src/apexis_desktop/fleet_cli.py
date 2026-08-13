"""``apexis nodes`` — see the machines, connect the Pi, check they answer.

Every command here is read-only or writes one line to a config file. Nothing
installs anything, nothing runs as root, and nothing touches the Pi's system
state: connecting is just recording an address and confirming something
answers at it.
"""

from __future__ import annotations

import httpx

from apexis_desktop.nodes import (
    DEFAULT_PI_PORT,
    Fleet,
    Node,
    NodeError,
    forget_pi,
    load_fleet,
    resolves,
    save_pi,
    scan,
)
from apexis_desktop.orchestrator import LAPTOP_MODEL, PI_MODEL


DIM = "\033[2m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
OFF = "\033[0m"


def _status_line(node: Node, client: httpx.Client) -> str:
    models = node._tags(client)
    if models is None:
        return (
            f"  {RED}●{OFF} {node.name:<7} {DIM}{node.host}{OFF}\n"
            f"      offline"
        )

    wanted = PI_MODEL if node.role == "pi" else LAPTOP_MODEL
    has = any(m.split(":")[0] == wanted.split(":")[0] for m in models)

    listed = ", ".join(models) if models else "none installed"
    line = f"  {GREEN}●{OFF} {node.name:<7} {DIM}{node.host}{OFF}\n      {listed}"
    if not has:
        line += f"\n      {YELLOW}missing {wanted}{OFF} — ollama pull {wanted}"
    return line


def show(fleet: Fleet | None = None) -> int:
    """List every node and whether it is answering."""
    fleet = fleet or load_fleet()

    print()
    with httpx.Client(timeout=3.0, trust_env=False) as client:
        for node in fleet.nodes:
            print(_status_line(node, client))
            print()

    if fleet.pi is None:
        print(f"  {DIM}No Pi connected. Everything runs on this laptop.{OFF}")
        print(f"  {DIM}Don't know its address?  {BOLD}apexis nodes find{OFF}\n")

    return 0


def find() -> int:
    """Scan the local network for a machine running Ollama."""
    print(f"\n  {DIM}scanning your network — a few seconds...{OFF}\n")

    try:
        found = scan()
    except Exception as exc:  # scanning is best-effort
        print(f"  {RED}scan failed: {exc}{OFF}\n")
        return 1

    if not found:
        print(f"  {YELLOW}Nothing found running Ollama.{OFF}\n")
        print("  Check on the Pi:")
        print(f"    {BOLD}systemctl status ollama{OFF}")
        print(f"    {BOLD}hostname -I{OFF}          {DIM}(its address){OFF}\n")
        print("  If Ollama is running there but not showing up here, it is")
        print("  probably only listening to itself. On the Pi:\n")
        print(f"    {BOLD}sudo systemctl edit ollama{OFF}")
        print(f"    {DIM}[Service]{OFF}")
        print(f'    {DIM}Environment="OLLAMA_HOST=0.0.0.0:11434"{OFF}')
        print(f"    {BOLD}sudo systemctl restart ollama{OFF}\n")
        return 1

    # Anything answering on this machine is us, not the Pi.
    mine = {"127.0.0.1", "localhost"}
    others = [(ip, models) for ip, models in found if ip not in mine]

    print(f"  Found {len(found)} machine(s) running Ollama:\n")
    for ip, models in found:
        listed = ", ".join(models) if models else "no models"
        print(f"    {GREEN}●{OFF} {ip:<16} {DIM}{listed}{OFF}")
    print()

    if len(others) == 1:
        ip = others[0][0]
        print(f"  {BOLD}That's almost certainly your Pi.{OFF} Connect it with:\n")
        print(f"    {BOLD}apexis nodes connect {ip}{OFF}\n")
    elif others:
        print(f"  {DIM}Pick the one that is your Pi:{OFF}\n")
        for ip, _m in others:
            print(f"    {BOLD}apexis nodes connect {ip}{OFF}")
        print()

    return 0


def connect(address: str) -> int:
    """Record the Pi's address after checking something answers there."""
    try:
        node = Node("pi", address, role="pi")
    except NodeError as exc:
        print(f"\n  {RED}{exc}{OFF}\n")
        return 1

    # Split "cannot find that name" from "found it, nothing listening" —
    # different problems, different fixes.
    from urllib.parse import urlparse

    parsed = urlparse(node.host)
    hostname = parsed.hostname or address
    port = parsed.port or DEFAULT_PI_PORT
    ip = resolves(hostname)

    if ip is None:
        print(f"\n  {RED}Cannot find a machine called {hostname!r}.{OFF}\n")
        print("  The name does not resolve on this network. Hostnames like")
        print(f"  {DIM}{hostname}{OFF} only work if your router publishes them,")
        print("  and many do not.\n")
        print(f"  {BOLD}Use the IP address instead.{OFF} On the Pi, run:\n")
        print(f"    {BOLD}hostname -I{OFF}\n")
        print("  Take the first number, then:\n")
        print(f"    {BOLD}apexis nodes connect 192.168.1.47{OFF}\n")
        print(f"  Or let APEXIS look for it:\n")
        print(f"    {BOLD}apexis nodes find{OFF}\n")
        return 1

    if ip != hostname:
        print(f"\n  {DIM}{hostname} resolves to {ip}{OFF}")

    print(f"  {DIM}checking {node.host} ...{OFF}")

    models = node._tags()
    if models is None:
        print(f"\n  {RED}Found {ip}, but nothing is answering on "
              f"port {port}.{OFF}\n")
        print("  The machine is reachable, so this is Ollama, not the network.")
        print("  On the Pi:\n")
        print(f"    {BOLD}systemctl status ollama{OFF}      {DIM}is it running?{OFF}")
        print(f"    {BOLD}curl localhost:{port}/api/tags{OFF}   "
              f"{DIM}does it answer itself?{OFF}\n")
        print("  If it answers on the Pi but not from here, it is listening")
        print("  only to itself. On the Pi:\n")
        print(f"    {BOLD}sudo systemctl edit ollama{OFF}\n")
        print("  add these two lines, save, then restart it:\n")
        print(f"    {DIM}[Service]{OFF}")
        print(f'    {DIM}Environment="OLLAMA_HOST=0.0.0.0:11434"{OFF}\n')
        print(f"    {BOLD}sudo systemctl restart ollama{OFF}\n")
        return 1

    saved = save_pi(node.host)
    print(f"  {GREEN}connected{OFF} — saved as {saved}\n")

    if models:
        print("  Models on the Pi:")
        for m in models:
            print(f"    · {m}")
    else:
        print(f"  {YELLOW}No models installed on the Pi yet.{OFF}")

    has_pi_model = any(m.split(":")[0] == PI_MODEL.split(":")[0] for m in models)
    if not has_pi_model:
        print(f"\n  {YELLOW}APEXIS wants {PI_MODEL} there.{OFF} On the Pi, run:")
        print(f"    {BOLD}ollama pull {PI_MODEL}{OFF}")
        print(f"  {DIM}about 1.3GB — comfortable on a 4GB Pi{OFF}")

    print(f"\n  {DIM}Check any time with:  apexis nodes{OFF}\n")
    return 0


def disconnect() -> int:
    """Forget the Pi. APEXIS falls back to laptop-only."""
    if forget_pi():
        print(f"\n  {DIM}Pi forgotten. Everything runs on this laptop now.{OFF}\n")
        return 0

    print(f"\n  {DIM}No Pi was connected.{OFF}\n")
    return 0


def ping() -> int:
    """Time a real round trip to each node."""
    import time

    fleet = load_fleet()
    print()

    with httpx.Client(timeout=5.0, trust_env=False) as client:
        for node in fleet.nodes:
            started = time.perf_counter()
            up = node.is_up(client)
            ms = (time.perf_counter() - started) * 1000

            if up:
                print(f"  {GREEN}●{OFF} {node.name:<7} {ms:6.0f}ms  {DIM}{node.host}{OFF}")
            else:
                print(f"  {RED}●{OFF} {node.name:<7} {'--':>6}    {DIM}{node.host}{OFF}")

    print()
    return 0


def main(action: str = "show", address: str | None = None) -> int:
    if action == "find":
        return find()

    if action == "connect":
        if not address:
            print(f"\n  {DIM}usage: apexis nodes connect 192.168.1.50{OFF}\n")
            return 1
        return connect(address)

    if action == "disconnect":
        return disconnect()

    if action == "ping":
        return ping()

    return show()
