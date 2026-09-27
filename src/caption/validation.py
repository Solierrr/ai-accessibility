"""Verificações locais de uma sugestão de texto alternativo."""

import re

from src.moderation.models import CaptionSuggestion

DEFAULT_ALT_TEXT_CHARS = 150
_URL = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_INSTRUCTION = re.compile(
    r"(?:ignore|desconsidere)\s+(?:as?\s+)?(?:instruções|regras)|"
    r"(?:clique|acesse)\s+(?:aqui|no link)",
    re.IGNORECASE,
)


def validated_alt_text(
    suggestion: CaptionSuggestion, *, max_alt_chars: int = DEFAULT_ALT_TEXT_CHARS
) -> str | None:
    text = " ".join(suggestion.alt_text.split())
    if (
        suggestion.uncertain
        or not 1 <= len(text) <= max_alt_chars
        or _URL.search(text)
        or _INSTRUCTION.search(text)
        or any(ord(char) < 32 for char in text)
    ):
        return None
    return text
