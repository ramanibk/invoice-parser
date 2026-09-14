"""Normalize visual PDF line wrapping in scalar extracted fields."""

import re
import unicodedata

HYPHENATED_LINE_WRAP = re.compile(r"(?<=\w)-[ \t]*(?:\r\n|\r|\n)[ \t]*(?=\w)")


def normalize_wrapped_text(value: str) -> str:
    """Collapse visual wrapping and reconnect tokens split after a hyphen."""
    # Preserve meaningful hyphens while removing only the line break introduced
    # by PDF layout, then normalize all remaining whitespace in one operation.
    reconnected = HYPHENATED_LINE_WRAP.sub("-", value)
    return " ".join(reconnected.split())


def identity_tokens(value: str) -> tuple[str, ...]:
    """Return normalized Unicode word tokens for identity comparison."""
    # NFKC makes visually equivalent compatibility characters comparable before
    # punctuation is discarded by tokenization.
    compatible = unicodedata.normalize("NFKC", value).casefold()
    return tuple(re.findall(r"[\w]+", compatible))


def contains_name(display_name: str, expected_name: str) -> bool:
    """Return whether an expected name is one contiguous whole-token phrase."""
    display_tokens = identity_tokens(display_name)
    expected_tokens = identity_tokens(expected_name)
    width = len(expected_tokens)
    # A contiguous token window accepts decorations such as sex markers while
    # preventing a partial token or reordered name from matching.
    return bool(expected_tokens) and any(
        display_tokens[index : index + width] == expected_tokens
        for index in range(len(display_tokens))
    )


def normalize_name(value: str) -> str:
    """Normalize an identity for punctuation- and case-insensitive comparison."""
    return " ".join(identity_tokens(value))
