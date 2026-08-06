"""Interactive APEXIS chat loop."""

from collections.abc import Callable

from apexis_desktop.brain.base import BrainProvider


EXIT_COMMANDS = {"/exit", "/quit", "exit", "quit"}


def run_chat(
    provider: BrainProvider,
    *,
    input_func: Callable[[str], str] = input,
    output_func: Callable[[str], None] = print,
) -> int:
    """Run an interactive chat session with a selected Brain provider."""

    output_func("APEXIS Chat")
    output_func(f"Brain: {provider.name}")
    output_func("Type /exit to leave. No conversation is saved.")

    while True:
        try:
            message = input_func("You: ")
        except (EOFError, KeyboardInterrupt):
            output_func("")
            output_func("Chat ended. No conversation was saved.")
            return 0

        cleaned = message.strip()
        if cleaned.lower() in EXIT_COMMANDS:
            output_func("Chat ended. No conversation was saved.")
            return 0

        if not cleaned:
            output_func("APEXIS: Please enter a message or /exit.")
            continue

        try:
            response = provider.respond(cleaned)
        except Exception as exc:  # Provider boundary: isolate implementation failures.
            output_func(f"APEXIS provider error: {exc}")
            continue

        output_func(f"APEXIS: {response}")
