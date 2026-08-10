"""Deterministic wake-word alias normalization and prefix matching."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Protocol


class WakeWordLike(Protocol):
    id: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class WakeWordMatch:
    wake_word_id: str
    command: str


def _is_separator(character: str) -> bool:
    return character.isspace() or unicodedata.category(character)[0] in {"P", "Z"}


def normalize_for_match(text: str) -> str:
    """Normalize and remove separators used only to compare wake-word prefixes."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    return "".join(character for character in normalized if not _is_separator(character))


def _strip_separators(text: str) -> str:
    start = 0
    end = len(text)
    while start < end and _is_separator(text[start]):
        start += 1
    while end > start and _is_separator(text[end - 1]):
        end -= 1
    return text[start:end]


def match_wake_word_prefix(
    transcript: str,
    wake_words: tuple[WakeWordLike, ...],
) -> WakeWordMatch | None:
    """Return the longest alias prefix match with a non-empty remaining command."""
    compared = normalize_for_match(transcript)
    candidates: list[tuple[int, str, str]] = []
    for wake_word in wake_words:
        for alias in wake_word.aliases:
            normalized_alias = normalize_for_match(alias)
            if compared.startswith(normalized_alias):
                candidates.append((len(normalized_alias), wake_word.id, normalized_alias))
    if not candidates:
        return None
    alias_length, wake_word_id, normalized_alias = max(candidates, key=lambda item: item[0])
    original_end = len(transcript)
    for index in range(1, len(transcript) + 1):
        normalized_prefix = normalize_for_match(transcript[:index])
        if len(normalized_prefix) >= alias_length and normalized_prefix.startswith(
            normalized_alias
        ):
            original_end = index
            break
    command = _strip_separators(transcript[original_end:])
    if not command:
        return None
    return WakeWordMatch(wake_word_id=wake_word_id, command=command)
