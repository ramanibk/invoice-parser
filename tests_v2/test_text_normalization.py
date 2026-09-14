"""Tests for scalar PDF field line-wrap normalization."""

import pytest
from text_normalization import (
    contains_name,
    identity_tokens,
    normalize_name,
    normalize_wrapped_text,
)


@pytest.mark.parametrize(
    ("extracted", "normalized"),
    [
        ("(F) Jelly Bean (26-\n7465)", "(F) Jelly Bean (26-7465)"),
        ("Castillo,\nKate", "Castillo, Kate"),
        ("Stella\nRose", "Stella Rose"),
        ("Rabies 1 year\nvaccine", "Rabies 1 year vaccine"),
        ("high-risk", "high-risk"),
        ("  repeated   spaces  ", "repeated spaces"),
    ],
)
def test_normalizes_visual_wrapping(extracted: str, normalized: str) -> None:
    """Collapse scalar field wrapping without losing hyphenated identifiers."""
    assert normalize_wrapped_text(extracted) == normalized


def test_normalizes_identity_text() -> None:
    """Normalize Unicode, punctuation, case, and spacing into stable tokens."""
    assert identity_tokens("  Désirée, SMITH  ") == ("désirée", "smith")
    assert normalize_name("  Désirée, SMITH  ") == "désirée smith"


def test_matches_only_contiguous_complete_name_tokens() -> None:
    """Accept complete name phrases without allowing substring false positives."""
    assert contains_name("(F) Jelly Bean Castillo", "Jelly Bean")
    assert not contains_name("(F) Jelly Beanie Castillo", "Jelly Bean")
