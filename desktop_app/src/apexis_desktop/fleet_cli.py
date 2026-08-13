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
    save_pi,
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
        print(f"  {DIM}Connect one:  apexis nodes connect 192.168.1.50{OFF}\n")

    return 0


def connect(address: str) -> int:
    """Record the Pi's address after checking something answers there."""
    try:
        node = Node("pi", address, role="pi")
    except NodeError as exc:
        print(f"\n  {RED}{exc}{OFF}\n")
        return 1

    print(f"\n  {DIM}checking {node.host} ...{OFF}")

    models = node._tags()
    if models is None:
        print(f"  {RED}Nothing answered.{OFF}\n")
        print("  On the Pi, check Ollama is running and listening to the")
        print("  network rather than only to itself:\n")
        print(f"    {BOLD}sudo systemctl status ollama{OFF}")
        print(f"    {BOLD}curl localhost:{DEFAULT_PI_PORT}/api/tags{OFF}\n")
        print("  If that works on the Pi but not from here, Ollama is bound")
        print("  to localhost. Fix it with:\n")
        print(f"    {BOLD}sudo systemctl edit ollama{OFF}\n")
        print("  and add:\n")
        print(f"    {DIM}[Service]{OFF}")
        print(f'    {DIM}Environment="OLLAMA_HOST=0.0.0.0:11434"{OFF}\n')
        print(f"  then  {BOLD}sudo systemctl restart ollama{OFF}\n")
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
