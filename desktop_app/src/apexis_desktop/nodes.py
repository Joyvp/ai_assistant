"""Where the machines are, and whether they are actually up.

APEXIS is two computers. The Pi holds the small always-on model; the laptop
holds the big one and wakes up when the work justifies it. Something has to
know the address of each, notice when one is unplugged, and report that
honestly instead of hanging.

**The Pi does not run an APEXIS service.** It runs Ollama with its HTTP API
bound to the LAN, and the laptop talks to that directly. One less daemon to
write, one less thing to keep alive on a machine that is meant to be boring.
It also means the Pi is useful the moment Ollama is installed.

**Nothing here reaches the internet.** Both addresses are private LAN
addresses, checked on construction. Tier 3 is the only path off this network,
and it is off by default and always announced.
"""

from __future__ import annotations

import ipaddress
import json
import os
import pathlib
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from apexis_shared.routing import NodeCapability


DEFAULT_PI_PORT = 11434
DEFAULT_LAPTOP_HOST = "http://127.0.0.1:11434"

# The Pi answers or it does not. A long timeout here would stall the whole
# router waiting on a machine that is switched off.
PROBE_TIMEOUT = 2.0

CONFIG_PATH = pathlib.Path.home() / ".config" / "apexis" / "nodes.json"


class NodeError(RuntimeError):
    """Raised when a node is misconfigured — not when it is merely down."""


def _is_private(host: str) -> bool:
    """True if ``host`` is a LAN address, loopback, or a .local name.

    Guards against a typo in the config quietly pointing APEXIS at a public
    address. A hostname that does not resolve is treated as private: it is
    almost certainly a LAN name that is currently offline, and refusing to
    start because the Pi is unplugged would be worse than useless.
    """
    if host in {"localhost", ""} or host.endswith(".local"):
        return True

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        try:
            resolved = socket.gethostbyname(host)
        except OSError:
            return True
        try:
            address = ipaddress.ip_address(resolved)
        except ValueError:
            return True

    return address.is_private or address.is_loopback or address.is_link_local


def normalise_host(value: str, *, default_port: int = DEFAULT_PI_PORT) -> str:
    """Turn whatever the user typed into a full base URL.

    Accepts ``192.168.1.50``, ``192.168.1.50:11434``, ``apexis-pi.local`` and
    ``http://192.168.1.50:11434`` and returns the same thing for all of them.
    """
    text = value.strip().rstrip("/")
    if not text:
        raise NodeError("node address is empty")

    if "://" not in text:
        text = f"http://{text}"

    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"}:
        raise NodeError(f"unsupported scheme {parsed.scheme!r} in {value!r}")
    if not parsed.hostname:
        raise NodeError(f"could not read a hostname from {value!r}")

    if not _is_private(parsed.hostname):
        raise NodeError(
            f"{parsed.hostname} is not a private address. APEXIS nodes must be "
            "on your own network — the internet is tier 3 only, and it asks "
            "first."
        )

    port = parsed.port or default_port
    return f"{parsed.scheme}://{parsed.hostname}:{port}"


@dataclass
class Node:
    """One machine that can run a model."""

    name: str
    host: str
    role: str = "pi"

    def __post_init__(self) -> None:
        self.host = normalise_host(self.host)

    # -- probing -----------------------------------------------------------

    def _tags(self, client: httpx.Client | None = None) -> list[str] | None:
        """Model names this node has, or None if it did not answer."""
        owns = client is None
        client = client or httpx.Client(timeout=PROBE_TIMEOUT, trust_env=False)
        try:
            response = client.get(f"{self.host}/api/tags", timeout=PROBE_TIMEOUT)
            response.raise_for_status()
            return [m["name"] for m in response.json().get("models", [])]
        except (httpx.HTTPError, ValueError, KeyError):
            return None
        finally:
            if owns:
                client.close()

    def is_up(self, client: httpx.Client | None = None) -> bool:
        return self._tags(client) is not None

    def capability(self, client: httpx.Client | None = None) -> NodeCapability:
        """What this node can do right now, in the shape the router wants."""
        models = self._tags(client)
        return NodeCapability(
            node="pi" if self.role == "pi" else "laptop",
            online=models is not None,
            models=models or [],
        )

    def has_model(self, model: str, client: httpx.Client | None = None) -> bool:
        models = self._tags(client)
        if models is None:
            return False
        base = model.split(":")[0]
        return any(m.split(":")[0] == base for m in models)

    # -- display -----------------------------------------------------------

    def describe(self, client: httpx.Client | None = None) -> str:
        models = self._tags(client)
        if models is None:
            return f"{self.name:<8} {self.host:<28} offline"
        listed = ", ".join(models) if models else "no models installed"
        return f"{self.name:<8} {self.host:<28} up · {listed}"


@dataclass
class Fleet:
    """The set of machines APEXIS can reach."""

    laptop: Node
    pi: Node | None = None

    @property
    def nodes(self) -> list[Node]:
        return [n for n in (self.pi, self.laptop) if n is not None]

    def node_for(self, role: str) -> Node | None:
        if role == "pi":
            return self.pi
        return self.laptop

    def pi_capability(self, client: httpx.Client | None = None) -> NodeCapability:
        """The Pi's capability, or a plainly-offline one when there is no Pi."""
        if self.pi is None:
            return NodeCapability(node="pi", online=False, models=[])
        return self.pi.capability(client)

    def laptop_capability(self, client: httpx.Client | None = None) -> NodeCapability:
        return self.laptop.capability(client)


# -- configuration ---------------------------------------------------------


def _read_config(path: pathlib.Path | None = None) -> dict[str, str]:
    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, str)}


def save_pi(host: str, path: pathlib.Path | None = None) -> str:
    """Remember where the Pi lives. Returns the normalised address."""
    normalised = normalise_host(host)
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    config = _read_config(path)
    config["pi"] = normalised
    path.write_text(json.dumps(config, indent=2) + "\n")
    return normalised


def forget_pi(path: pathlib.Path | None = None) -> bool:
    """Remove the saved Pi address. Returns False if there was none."""
    path = path or CONFIG_PATH
    config = _read_config(path)
    if "pi" not in config:
        return False

    del config["pi"]
    path.write_text(json.dumps(config, indent=2) + "\n")
    return True


def load_fleet(
    *,
    pi_host: str | None = None,
    laptop_host: str | None = None,
    path: pathlib.Path | None = None,
) -> Fleet:
    """Build the fleet from an explicit argument, the environment, or config.

    Precedence: explicit argument, then ``$APEXIS_PI_HOST``, then the saved
    config file. No Pi configured is a normal state, not an error — APEXIS
    runs laptop-only until you connect one.
    """
    laptop = Node(
        "laptop",
        laptop_host or os.getenv("APEXIS_OLLAMA_HOST", DEFAULT_LAPTOP_HOST),
        role="laptop",
    )

    chosen = pi_host or os.getenv("APEXIS_PI_HOST") or _read_config(path).get("pi")
    if not chosen:
        return Fleet(laptop=laptop, pi=None)

    try:
        pi = Node("pi", chosen, role="pi")
    except NodeError:
        # A bad saved address must not stop APEXIS from starting.
        return Fleet(laptop=laptop, pi=None)

    return Fleet(laptop=laptop, pi=pi)
