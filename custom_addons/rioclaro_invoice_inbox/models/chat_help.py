"""Shared recognition for explicit chatbot capability-help requests."""

import re
import unicodedata

_HELP_COMMANDS = frozenset({"/start", "/help", "/ayuda"})
_HELP_QUESTIONS = frozenset(
    {
        "ayuda",
        "como me puedes ayudar",
        "que puedes hacer",
        "que puedes hacer por mi",
        "que puedo consultar",
        "que consultas puedes hacer",
        "que sabes hacer",
        "menu",
    }
)


def is_capability_help_request(text: str | None) -> bool:
    """Return whether text is a supported help command or capability question."""
    if not text:
        return False
    command = text.strip().split(maxsplit=1)[0].split("@", 1)[0].casefold()
    if command in _HELP_COMMANDS:
        return True
    normalized = unicodedata.normalize("NFD", text.casefold())
    normalized = "".join(
        character for character in normalized if not unicodedata.combining(character)
    )
    normalized = " ".join(re.sub(r"[^\w\s]", " ", normalized).split())
    return normalized in _HELP_QUESTIONS
