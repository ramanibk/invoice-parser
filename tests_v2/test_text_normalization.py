"""Tests for scalar PDF field line-wrap normalization."""

import pytest
from text_normalization import normalize_wrapped_text


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
