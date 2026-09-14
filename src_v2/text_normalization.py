"""Normalize visual PDF line wrapping in scalar extracted fields."""

import re

HYPHENATED_LINE_WRAP = re.compile(r"(?<=\w)-[ \t]*(?:\r\n|\r|\n)[ \t]*(?=\w)")


def normalize_wrapped_text(value: str) -> str:
    """Collapse visual wrapping and reconnect tokens split after a hyphen."""
    reconnected = HYPHENATED_LINE_WRAP.sub("-", value)
    return " ".join(reconnected.split())
