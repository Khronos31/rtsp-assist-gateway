from __future__ import annotations

from gateway.config import WakeWordConfig
from gateway.matcher import (
    WakeWordMatch,
    match_wake_word_prefix,
    normalize_for_match,
)

WORDS = (
    WakeWordConfig(
        id="computer",
        aliases=("ねえコンピューター", "ねえコンピュータ"),
    ),
    WakeWordConfig(id="jarvis", aliases=("ヘイジャービス", "ヘイ、ジャーヴィス")),
)


def test_normalization_absorbs_width_case_whitespace_and_punctuation() -> None:
    fullwidth = "\uff28\uff45\uff59, \uff2a\uff41\uff52\uff56\uff49\uff53!"
    assert normalize_for_match(fullwidth) == "heyjarvis"
    assert normalize_for_match("ヘイ、 ジャーヴィス") == "ヘイジャーヴィス"


def test_multiple_aliases_return_canonical_id_and_original_command() -> None:
    assert match_wake_word_prefix("ねえコンピューター、電気を消して。", WORDS) == WakeWordMatch(
        wake_word_id="computer",
        command="電気を消して",
    )


def test_longest_alias_wins() -> None:
    words = (
        WakeWordConfig(id="short", aliases=("hey",)),
        WakeWordConfig(id="long", aliases=("hey jarvis",)),
    )
    result = match_wake_word_prefix("Hey, Jarvis: turn on the light", words)
    assert result is not None
    assert result.wake_word_id == "long"
    assert result.command == "turn on the light"


def test_alias_only_and_non_prefix_do_not_match() -> None:
    assert match_wake_word_prefix("ねえコンピューター。", WORDS) is None
    assert match_wake_word_prefix("ところでねえコンピュータに聞いて", WORDS) is None


def test_combining_character_normalization_preserves_command_boundary() -> None:
    words = (WakeWordConfig(id="guide", aliases=("がいど",)),)
    result = match_wake_word_prefix("か\u3099いど、開始して", words)
    assert result == WakeWordMatch(wake_word_id="guide", command="開始して")
