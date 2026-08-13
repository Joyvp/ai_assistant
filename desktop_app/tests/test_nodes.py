"""Tests for the node layer — addresses, probing, and two-machine routing."""

from __future__ import annotations

import json

import httpx
import pytest

from apexis_desktop import nodes
from apexis_desktop.nodes import Fleet, Node, NodeError, load_fleet, normalise_host
from apexis_desktop.orchestrator import LAPTOP_MODEL, PI_MODEL, Orchestrator
from apexis_shared.routing import NodeCapability, Tier


# -- address handling ------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        ("192.168.1.50", "http://192.168.1.50:11434"),
        ("192.168.1.50:11434", "http://192.168.1.50:11434"),
        ("192.168.1.50:9999", "http://192.168.1.50:9999"),
        ("http://192.168.1.50", "http://192.168.1.50:11434"),
        ("http://192.168.1.50:11434/", "http://192.168.1.50:11434"),
        ("  192.168.1.50  ", "http://192.168.1.50:11434"),
        ("apexis-pi.local", "http://apexis-pi.local:11434"),
        ("10.0.0.5", "http://10.0.0.5:11434"),
        ("172.16.4.4", "http://172.16.4.4:11434"),
        ("localhost", "http://localhost:11434"),
        ("127.0.0.1", "http://127.0.0.1:11434"),
    ],
)
def test_normalises_whatever_the_user_typed(given, expected):
    assert normalise_host(given) == expected


@pytest.mark.parametrize("public", ["8.8.8.8", "1.1.1.1", "http://93.184.216.34"])
def test_refuses_a_public_address(public):
    # A typo must not quietly point APEXIS at the internet.
    with pytest.raises(NodeError, match="private"):
        normalise_host(public)


def test_refuses_an_empty_address():
    with pytest.raises(NodeError):
        normalise_host("   ")


def test_refuses_a_strange_scheme():
    with pytest.raises(NodeError, match="scheme"):
        normalise_host("ftp://192.168.1.50")


def test_an_unresolvable_hostname_is_allowed():
    # Almost certainly a LAN name for a machine that is currently off.
    assert normalise_host("some-pi-that-does-not-exist") == (
        "http://some-pi-that-does-not-exist:11434"
    )


# -- probing ---------------------------------------------------------------


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _tags(*models):
    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(
                200, json={"models": [{"name": m} for m in models]}
            )
        return httpx.Response(404)

    return handler


def _dead(request):
    raise httpx.ConnectError("no route to host")


def test_a_node_that_answers_is_up():
    with _client(_tags("llama3.2:1b")) as c:
        assert Node("pi", "192.168.1.50").is_up(c)


def test_a_node_that_does_not_answer_is_down():
    with _client(_dead) as c:
        assert not Node("pi", "192.168.1.50").is_up(c)


def test_capability_reports_the_models():
    with _client(_tags("llama3.2:1b", "qwen2:0.5b")) as c:
        cap = Node("pi", "192.168.1.50").capability(c)

    assert cap.online is True
    assert cap.node == "pi"
    assert cap.models == ["llama3.2:1b", "qwen2:0.5b"]


def test_capability_of_a_dead_node_is_offline_not_an_error():
    with _client(_dead) as c:
        cap = Node("pi", "192.168.1.50").capability(c)

    assert cap.online is False
    assert cap.models == []


def test_has_model_matches_a_bare_name():
    with _client(_tags("llama3.2:1b")) as c:
        node = Node("pi", "192.168.1.50")
        assert node.has_model("llama3.2", c)
        assert node.has_model("llama3.2:1b", c)
        assert not node.has_model("phi3", c)


def test_a_node_serving_garbage_is_treated_as_down():
    def handler(request):
        return httpx.Response(200, text="not json")

    with _client(handler) as c:
        assert not Node("pi", "192.168.1.50").is_up(c)


def test_describe_mentions_offline():
    with _client(_dead) as c:
        assert "offline" in Node("pi", "192.168.1.50").describe(c)


# -- fleet -----------------------------------------------------------------


def test_a_fleet_without_a_pi_is_valid():
    fleet = Fleet(laptop=Node("laptop", "127.0.0.1", role="laptop"))
    assert fleet.pi is None
    assert [n.name for n in fleet.nodes] == ["laptop"]


def test_pi_capability_without_a_pi_is_plainly_offline():
    fleet = Fleet(laptop=Node("laptop", "127.0.0.1", role="laptop"))
    cap = fleet.pi_capability()
    assert cap.online is False


def test_the_pi_comes_first_so_the_cheap_tier_is_checked_first():
    fleet = Fleet(
        laptop=Node("laptop", "127.0.0.1", role="laptop"),
        pi=Node("pi", "192.168.1.50"),
    )
    assert [n.name for n in fleet.nodes] == ["pi", "laptop"]


# -- configuration ---------------------------------------------------------


@pytest.fixture()
def config(tmp_path, monkeypatch):
    path = tmp_path / "nodes.json"
    monkeypatch.setattr(nodes, "CONFIG_PATH", path)
    monkeypatch.delenv("APEXIS_PI_HOST", raising=False)
    return path


def test_saving_the_pi_normalises_and_persists(config):
    saved = nodes.save_pi("192.168.1.50")
    assert saved == "http://192.168.1.50:11434"
    assert json.loads(config.read_text())["pi"] == saved


def test_a_saved_pi_is_loaded_into_the_fleet(config):
    nodes.save_pi("192.168.1.50")
    fleet = load_fleet()
    assert fleet.pi is not None
    assert fleet.pi.host == "http://192.168.1.50:11434"


def test_forgetting_the_pi_removes_it(config):
    nodes.save_pi("192.168.1.50")
    assert nodes.forget_pi() is True
    assert load_fleet().pi is None


def test_forgetting_when_there_is_no_pi_is_not_an_error(config):
    assert nodes.forget_pi() is False


def test_no_config_means_laptop_only(config):
    assert load_fleet().pi is None


def test_the_environment_beats_the_config_file(config, monkeypatch):
    nodes.save_pi("192.168.1.50")
    monkeypatch.setenv("APEXIS_PI_HOST", "192.168.1.99")
    assert load_fleet().pi.host == "http://192.168.1.99:11434"


def test_an_explicit_argument_beats_everything(config, monkeypatch):
    nodes.save_pi("192.168.1.50")
    monkeypatch.setenv("APEXIS_PI_HOST", "192.168.1.99")
    fleet = load_fleet(pi_host="192.168.1.77")
    assert fleet.pi.host == "http://192.168.1.77:11434"


def test_a_corrupt_config_does_not_stop_apexis_starting(config):
    config.write_text("{ this is not json")
    assert load_fleet().pi is None


def test_a_bad_saved_address_does_not_stop_apexis_starting(config):
    config.write_text(json.dumps({"pi": "8.8.8.8"}))
    # A public address saved by hand must be ignored, not crash the CLI.
    assert load_fleet().pi is None


def test_saving_the_pi_keeps_other_settings(config):
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(json.dumps({"something": "else"}))
    nodes.save_pi("192.168.1.50")
    data = json.loads(config.read_text())
    assert data["something"] == "else"
    assert data["pi"] == "http://192.168.1.50:11434"


# -- routing across two machines ------------------------------------------


class FakeProvider:
    def __init__(self, model: str, host: str) -> None:
        self.model = model
        self.host = host

    def respond(self, task: str) -> str:
        return f"[{self.model}@{self.host}] {task}"

    def close(self) -> None:
        pass


class FakeLifecycle:
    """Records loads and unloads without touching a real Ollama."""

    def __init__(self, host: str = "http://127.0.0.1:11434") -> None:
        self.host = host
        self.loaded: list[str] = []
        self.unloaded: list[str] = []
        self._resident: set[str] = set()

    def resident_mb(self) -> int:
        return 0

    def is_resident(self, model: str) -> bool:
        return model in self._resident

    def load(self, model: str, **_kw) -> float:
        self.loaded.append(model)
        self._resident.add(model)
        return 0.0

    def unload(self, model: str) -> bool:
        self.unloaded.append(model)
        self._resident.discard(model)
        return True

    from contextlib import contextmanager

    @contextmanager
    def borrowed(self, model: str, *, unload_after: bool = True):
        self.load(model)
        try:
            yield
        finally:
            if unload_after:
                self.unload(model)


@pytest.fixture()
def two_machines():
    """A fleet with a Pi, and a lifecycle recorder per host."""
    fleet = Fleet(
        laptop=Node("laptop", "127.0.0.1", role="laptop"),
        pi=Node("pi", "192.168.1.50"),
    )
    lifecycles: dict[str, FakeLifecycle] = {}

    orch = Orchestrator(
        fleet=fleet,
        lifecycle=FakeLifecycle(),
        provider_factory=FakeProvider,
        laptop_capability=lambda: NodeCapability(
            node="laptop", online=True, models=[LAPTOP_MODEL]
        ),
    )

    def lifecycle_for(host: str) -> FakeLifecycle:
        if host not in lifecycles:
            lifecycles[host] = FakeLifecycle(host)
        return lifecycles[host]

    orch._lifecycle_for = lifecycle_for  # type: ignore[method-assign]
    return orch, lifecycles, fleet


def test_a_trivial_task_runs_on_the_pi(two_machines):
    orch, lifecycles, fleet = two_machines
    result = orch.handle("hey")

    assert result.tier is Tier.PI_LOCAL
    assert fleet.pi.host in result.reply
    assert PI_MODEL in result.reply


def test_a_complex_task_runs_on_the_laptop(two_machines):
    orch, lifecycles, fleet = two_machines
    result = orch.handle("refactor this function for me")

    assert result.tier is Tier.LAPTOP
    assert fleet.laptop.host in result.reply
    assert LAPTOP_MODEL in result.reply


def test_the_pi_keeps_its_model_loaded(two_machines):
    """The whole point of an always-on tier: no cold start on trivial work."""
    orch, lifecycles, fleet = two_machines
    orch.handle("hey")

    pi_life = lifecycles[fleet.pi.host]
    assert pi_life.loaded == [PI_MODEL]
    assert pi_life.unloaded == []


def test_the_laptop_gives_its_ram_back(two_machines):
    orch, lifecycles, fleet = two_machines
    orch.handle("refactor this function for me")

    laptop_life = lifecycles[fleet.laptop.host]
    assert laptop_life.loaded == [LAPTOP_MODEL]
    assert laptop_life.unloaded == [LAPTOP_MODEL]


def test_the_laptop_model_is_never_loaded_on_the_pi(two_machines):
    """A 2.2GB model must not be sent to a 4GB Pi."""
    orch, lifecycles, fleet = two_machines
    orch.handle("refactor this function for me")

    assert fleet.pi.host not in lifecycles or not lifecycles[fleet.pi.host].loaded


def test_the_record_says_which_machine_ran_it(two_machines):
    orch, _lifecycles, _fleet = two_machines
    result = orch.handle("hey")
    assert "on pi" in result.record.attempts[-1].detail


def test_without_a_pi_the_pi_tier_runs_on_the_laptop():
    fleet = Fleet(laptop=Node("laptop", "127.0.0.1", role="laptop"))
    orch = Orchestrator(
        fleet=fleet,
        lifecycle=FakeLifecycle(),
        provider_factory=FakeProvider,
        laptop_capability=lambda: NodeCapability(
            node="laptop", online=True, models=[LAPTOP_MODEL]
        ),
    )
    result = orch.handle("hey")

    # The decision still stands; it is simply served locally.
    assert result.tier is Tier.PI_LOCAL
    assert "127.0.0.1" in result.reply


def test_without_a_pi_nothing_stays_resident():
    """Laptop-only: never hold RAM the user is using for their desktop."""
    fleet = Fleet(laptop=Node("laptop", "127.0.0.1", role="laptop"))
    life = FakeLifecycle()
    orch = Orchestrator(
        fleet=fleet,
        lifecycle=life,
        provider_factory=FakeProvider,
        laptop_capability=lambda: NodeCapability(
            node="laptop", online=True, models=[LAPTOP_MODEL]
        ),
    )
    orch.handle("hey")

    assert life.unloaded == [PI_MODEL]


# -- a node going away -----------------------------------------------------


class DeadLifecycle(FakeLifecycle):
    """A node that is switched off, unplugged, or off the wifi."""

    def load(self, model: str, **_kw) -> float:
        raise RuntimeError(f"could not load {model}: Connection refused")

    from contextlib import contextmanager

    @contextmanager
    def borrowed(self, model: str, *, unload_after: bool = True):
        self.load(model)
        yield  # never reached


def _mixed_fleet(pi_alive: bool):
    """A fleet whose Pi is either working or dead."""
    fleet = Fleet(
        laptop=Node("laptop", "127.0.0.1", role="laptop"),
        pi=Node("pi", "192.168.1.50"),
    )
    laptop_life = FakeLifecycle(fleet.laptop.host)
    pi_life = (FakeLifecycle if pi_alive else DeadLifecycle)(fleet.pi.host)

    orch = Orchestrator(
        fleet=fleet,
        lifecycle=laptop_life,
        provider_factory=FakeProvider,
        laptop_capability=lambda: NodeCapability(
            node="laptop", online=True, models=[LAPTOP_MODEL]
        ),
    )
    orch._lifecycle_for = lambda host: (  # type: ignore[method-assign]
        pi_life if host == fleet.pi.host else laptop_life
    )
    return orch, laptop_life, pi_life


def test_an_unplugged_pi_does_not_crash_the_session():
    """A Pi on a shelf drops off the wifi. That is a Tuesday, not a crash."""
    orch, _laptop, _pi = _mixed_fleet(pi_alive=False)
    result = orch.handle("hey")  # would have routed to the Pi
    assert result.reply


def test_an_unplugged_pi_falls_back_to_the_laptop():
    orch, laptop_life, _pi = _mixed_fleet(pi_alive=False)
    result = orch.handle("hey")

    assert result.tier is Tier.LAPTOP
    assert laptop_life.loaded == [LAPTOP_MODEL]


def test_an_unplugged_pi_says_so_out_loud():
    orch, _laptop, _pi = _mixed_fleet(pi_alive=False)
    result = orch.handle("hey")

    assert any("Pi did not answer" in n for n in result.notices)


def test_the_failed_pi_attempt_is_recorded():
    orch, _laptop, _pi = _mixed_fleet(pi_alive=False)
    result = orch.handle("hey")

    attempts = result.record.attempts
    assert attempts[0].tier is Tier.PI_LOCAL
    assert attempts[0].ok is False
    assert attempts[-1].ok is True


def test_falling_back_still_releases_the_laptop_ram():
    orch, laptop_life, _pi = _mixed_fleet(pi_alive=False)
    orch.handle("hey")
    assert laptop_life.unloaded == [LAPTOP_MODEL]


def test_a_working_pi_is_not_disturbed_by_the_fallback_path():
    orch, laptop_life, pi_life = _mixed_fleet(pi_alive=True)
    result = orch.handle("hey")

    assert result.tier is Tier.PI_LOCAL
    assert pi_life.loaded == [PI_MODEL]
    assert laptop_life.loaded == []


def test_the_default_lifecycle_follows_the_configured_laptop_address():
    """A non-default port must not send unload requests into the void."""
    fleet = Fleet(laptop=Node("laptop", "127.0.0.1:9999", role="laptop"))
    orch = Orchestrator(fleet=fleet, provider_factory=FakeProvider)
    assert orch.lifecycle.host == "http://127.0.0.1:9999"
