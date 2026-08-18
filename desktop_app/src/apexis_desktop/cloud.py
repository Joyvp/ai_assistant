"""Tier 3 — what happens when a task is beyond both local models.

Two ways to answer something phi3 cannot, and neither costs money:

*   **handoff** (the default) — APEXIS admits it is out of its depth, builds a
    complete prompt including the relevant context, and hands it to you to
    paste into whatever free chat you already use. Nothing leaves the machine
    on its own. No account, no key, no signup, no per-token anything.

*   **api** — a real call to a provider with a free tier. Off unless you set a
    key. Announced every single time.

Design rules, in order of importance:

1.  **Never go online silently.** The spec excludes internet access from V1 and
    forbids quiet behaviour. Every online call is announced before it happens.
2.  **Never claim to have gone online when it did not.** The old code printed
    "Going online to Claude" and then quietly ran on phi3. Lying in the
    reassuring direction is still lying.
3.  **Facts stay home.** Remembered facts about you are injected into local
    prompts freely. They are *not* sent to a third party unless you say so.
    Most free tiers train on what you send them.
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field

import httpx


CONFIG_PATH = pathlib.Path.home() / ".config" / "apexis" / "cloud.json"

# Every one of these is OpenAI-compatible, so a single code path serves them
# all. Free tiers as of writing; check before relying on the numbers.
PROVIDERS: dict[str, dict[str, str]] = {
    "groq": {
        "label": "Groq",
        "url": "https://api.groq.com/openai/v1/chat/completions",
        # llama-3.3-70b-versatile was shut down on 2026-08-16. Hosted model
        # IDs are not stable and a dead one returns 404, which reads like a
        # broken URL rather than a retired model. See models_url below: the
        # error path now asks the provider what it actually hosts.
        "model": "openai/gpt-oss-120b",
        "models_url": "https://api.groq.com/openai/v1/models",
        "fallback_models": "openai/gpt-oss-20b,qwen/qwen3.6-27b",
        "key_env": "GROQ_API_KEY",
        "signup": "https://console.groq.com",
        "free": "14,400 requests/day, no credit card",
        "trains": "no — Groq states it does not train on API data",
    },
    "openrouter": {
        "label": "OpenRouter",
        "url": "https://openrouter.ai/api/v1/chat/completions",
        "model": "meta-llama/llama-3.3-70b-instruct:free",
        "models_url": "https://openrouter.ai/api/v1/models",
        "key_env": "OPENROUTER_API_KEY",
        "signup": "https://openrouter.ai/keys",
        "free": "50 requests/day, no credit card",
        "trains": "varies by model — check the model page",
    },
    "gemini": {
        "label": "Google Gemini",
        "url": "https://generativelanguage.googleapis.com/v1beta/openai/"
               "chat/completions",
        "model": "gemini-2.0-flash",
        "key_env": "GEMINI_API_KEY",
        "signup": "https://aistudio.google.com/apikey",
        "free": "1,500 requests/day, no credit card",
        "trains": "yes — Google may train on free-tier prompts",
    },
    "anthropic": {
        "label": "Claude",
        "url": "https://api.anthropic.com/v1/chat/completions",
        "model": "claude-haiku-4-5",
        "key_env": "ANTHROPIC_API_KEY",
        "signup": "https://console.anthropic.com",
        "free": "no free tier — pay per token",
        "trains": "no — Anthropic does not train on API data by default",
    },
}

DEFAULT_PROVIDER = "groq"

# What tier 3 does when it is not configured. "handoff" needs no account, so
# it is the honest default for a project with no budget.
DEFAULT_MODE = "handoff"

MODES = ("off", "handoff", "api")


class CloudError(RuntimeError):
    """Raised when an online call was attempted and failed."""


@dataclass
class CloudResult:
    """The outcome of a tier-3 attempt."""

    mode: str
    text: str = ""
    prompt: str = ""
    provider: str = ""
    went_online: bool = False
    notices: list[str] = field(default_factory=list)

    @property
    def answered(self) -> bool:
        """True if there is a real answer, rather than a prompt to go paste."""
        return bool(self.text)


# -- configuration ---------------------------------------------------------


def _read(path: pathlib.Path | None = None) -> dict:
    path = path or CONFIG_PATH
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(config: dict, path: pathlib.Path | None = None) -> None:
    path = path or CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")


def get_mode(path: pathlib.Path | None = None) -> str:
    mode = _read(path).get("mode", DEFAULT_MODE)
    return mode if mode in MODES else DEFAULT_MODE


def set_mode(mode: str, path: pathlib.Path | None = None) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {', '.join(MODES)}")
    config = _read(path)
    config["mode"] = mode
    _write(config, path)


def get_provider(path: pathlib.Path | None = None) -> str:
    name = _read(path).get("provider", DEFAULT_PROVIDER)
    return name if name in PROVIDERS else DEFAULT_PROVIDER


def set_provider(name: str, path: pathlib.Path | None = None) -> None:
    if name not in PROVIDERS:
        raise ValueError(f"unknown provider {name!r}")
    config = _read(path)
    config["provider"] = name
    _write(config, path)


def api_key(provider: str | None = None, path: pathlib.Path | None = None) -> str:
    """The key for a provider: environment first, then the config file.

    Environment first so a key can be supplied for one run without ever being
    written to disk.
    """
    provider = provider or get_provider(path)
    spec = PROVIDERS[provider]

    from_env = os.getenv(spec["key_env"])
    if from_env:
        return from_env.strip()

    return str(_read(path).get("keys", {}).get(provider, "")).strip()


def set_api_key(
    provider: str, key: str, path: pathlib.Path | None = None
) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")

    config = _read(path)
    config.setdefault("keys", {})[provider] = key.strip()
    _write(config, path)

    # A file with a credential in it should not be world-readable.
    try:
        (path or CONFIG_PATH).chmod(0o600)
    except OSError:
        pass


def is_configured(path: pathlib.Path | None = None) -> bool:
    """True if an actual online call is possible right now."""
    return get_mode(path) == "api" and bool(api_key(path=path))


# -- the handoff -----------------------------------------------------------

HANDOFF_HEADER = """\
The local models could not handle this one. Here is a complete prompt —
paste it into whatever assistant you like, then bring the answer back.
"""


def build_prompt(
    task: str,
    *,
    history: list[tuple[str, str]] | None = None,
    facts: str = "",
) -> str:
    """Assemble a self-contained prompt for a human to paste elsewhere.

    Self-contained matters: whatever you paste this into has none of your
    conversation, so the context has to travel with the question.
    """
    parts: list[str] = []

    if facts:
        parts.append(facts.strip())

    if history:
        lines = []
        for user, assistant in history[-3:]:
            lines.append(f"Me: {user}")
            if assistant:
                lines.append(f"Assistant: {assistant}")
        if lines:
            parts.append("Earlier in this conversation:\n" + "\n".join(lines))

    parts.append(f"My question:\n{task.strip()}")
    return "\n\n".join(parts)


def copy_to_clipboard(text: str) -> bool:
    """Best-effort clipboard copy. Returns False if no tool is available."""
    import shutil
    import subprocess

    for command in (
        ["xclip", "-selection", "clipboard"],
        ["xsel", "--clipboard", "--input"],
        ["wl-copy"],
        ["pbcopy"],
    ):
        if shutil.which(command[0]) is None:
            continue
        try:
            subprocess.run(command, input=text.encode(), check=True, timeout=5)
            return True
        except (subprocess.SubprocessError, OSError):
            continue

    return False


# -- the online call -------------------------------------------------------


def set_setting(key: str, value: str, *, path: pathlib.Path | None = None) -> None:
    """Store an override in cloud.json."""
    target = path or CONFIG_PATH
    try:
        data = json.loads(target.read_text())
    except (OSError, ValueError):
        data = {}
    data[key] = value
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2))
    try:
        target.chmod(0o600)
    except OSError:
        pass


def model_for(provider: str, *, path: pathlib.Path | None = None) -> str:
    """The model to use, preferring a user override over the built-in default.

    The built-in is a snapshot of what the provider hosted when this was
    written. Providers retire models, so the user must be able to move on
    without waiting for a new release.
    """
    target = path or CONFIG_PATH
    try:
        data = json.loads(target.read_text())
    except (OSError, ValueError):
        data = {}
    override = str(data.get(f"model_{provider}", "")).strip()
    return override or PROVIDERS[provider]["model"]


def ask_online(
    task: str,
    *,
    provider: str | None = None,
    key: str | None = None,
    system: str = "",
    timeout: float = 60.0,
    client: httpx.Client | None = None,
    path: pathlib.Path | None = None,
) -> str:
    """Send one message to a cloud provider. Raises CloudError on failure.

    Callers must announce this *before* calling. Nothing here prints, because
    a function that both performs and announces an action is one refactor away
    from performing it without announcing.
    """
    provider = provider or get_provider(path)
    if provider not in PROVIDERS:
        raise CloudError(f"unknown provider {provider!r}")

    spec = PROVIDERS[provider]
    key = key or api_key(provider, path)
    if not key:
        raise CloudError(
            f"no API key for {spec['label']}. Set {spec['key_env']} or run "
            "apexis cloud key"
        )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": task})

    owns = client is None
    client = client or httpx.Client(timeout=timeout, trust_env=False)

    try:
        response = client.post(
            spec["url"],
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            json={"model": model_for(provider, path=path), "messages": messages},
        )

        if response.status_code == 401:
            raise CloudError(f"{spec['label']} rejected the key")
        if response.status_code == 404:
            # A 404 here means the MODEL is gone, not the URL. Hosted models
            # get retired on a few weeks' notice, so ask the provider what it
            # hosts now rather than making the user guess.
            raise CloudError(
                f"{spec['label']} no longer has "
                f"{model_for(provider, path=path)!r}. "
                f"Hosted models get retired - see: apexis cloud models"
            )
        if response.status_code == 429:
            raise CloudError(
                f"{spec['label']} rate limit reached — free tier is "
                f"{spec['free']}. Try again later."
            )
        response.raise_for_status()

        payload = response.json()
        return payload["choices"][0]["message"]["content"].strip()

    except CloudError:
        raise
    except httpx.HTTPError as exc:
        raise CloudError(f"could not reach {spec['label']}: {exc}") from exc
    except (KeyError, IndexError, ValueError) as exc:
        raise CloudError(f"{spec['label']} sent something unexpected: {exc}") from exc
    finally:
        if owns:
            client.close()


def current_provider(*, path: pathlib.Path | None = None) -> str:
    return get_provider(path)


def available_models(
    provider: str = "",
    *,
    path: pathlib.Path | None = None,
    client: httpx.Client | None = None,
    timeout: float = 20.0,
) -> list[str]:
    """Ask the provider which models it actually hosts right now.

    Written because a hardcoded model ID died one day before it was used.
    A list in the source is a snapshot; this is the truth.
    """
    provider = provider or current_provider(path=path)
    if provider not in PROVIDERS:
        raise CloudError(f"unknown provider {provider!r}")

    spec = PROVIDERS[provider]
    url = spec.get("models_url", "")
    if not url:
        raise CloudError(f"{spec['label']} has no model list endpoint")

    key = api_key(provider, path=path)
    if not key:
        raise CloudError(f"no {spec['label']} key set - apexis cloud key <key>")

    owns = client is None
    client = client or httpx.Client(timeout=timeout, trust_env=False)
    try:
        response = client.get(url, headers={"Authorization": f"Bearer {key}"})
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        names = []
        for row in rows or []:
            name = row.get("id") if isinstance(row, dict) else str(row)
            if name:
                names.append(str(name))
        return sorted(names)
    except CloudError:
        raise
    except httpx.HTTPError as exc:
        raise CloudError(f"could not reach {spec['label']}: {exc}") from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise CloudError(f"{spec['label']} sent something unexpected: {exc}") from exc
    finally:
        if owns:
            client.close()


def handle(
    task: str,
    *,
    history: list[tuple[str, str]] | None = None,
    facts: str = "",
    send_facts: bool = False,
    system: str = "",
    path: pathlib.Path | None = None,
    client: httpx.Client | None = None,
) -> CloudResult:
    """Deal with a task the local models cannot manage.

    Returns a result describing what actually happened — never a claim that
    something happened when it did not.
    """
    mode = get_mode(path)

    if mode == "off":
        return CloudResult(
            mode="off",
            notices=[
                "This is beyond the local models, and the internet is turned "
                "off. Answering locally anyway — expect a weaker result. "
                "Turn tier 3 on with:  apexis cloud on"
            ],
        )

    # Facts about you stay home unless explicitly permitted.
    shared_facts = facts if send_facts else ""

    if mode == "handoff":
        prompt = build_prompt(task, history=history, facts=shared_facts)
        return CloudResult(mode="handoff", prompt=prompt)

    provider = get_provider(path)
    spec = PROVIDERS[provider]
    online_notice = (
        f"Going online to {spec['label']} now. This request is leaving your "
        f"network."
    )
    text = ask_online(
        task,
        provider=provider,
        system=(system + ("\n\n" + shared_facts if shared_facts else "")).strip(),
        path=path,
        client=client,
    )
    return CloudResult(
        mode="api",
        text=text,
        provider=provider,
        went_online=True,
        notices=[online_notice],
    )
