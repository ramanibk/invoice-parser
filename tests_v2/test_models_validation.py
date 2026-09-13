"""Tests for validation primitives shared by pipeline models."""

from datetime import date, datetime

import pytest
from errors import PipelineError
from models_validation import (
    _require_date,
    _require_non_empty_tuple_of,
    _require_string,
    _require_text,
)


def test_string_validation_preserves_empty_source_text() -> None:
    """Allow empty strings when a source field may legitimately be blank."""
    assert _require_string("", "medical finding") is None


@pytest.mark.parametrize("value", [None, 12, []])
def test_string_validation_rejects_other_types(value: object) -> None:
    """Reject values that cannot preserve an exact textual source field."""
    with pytest.raises(PipelineError, match="medical finding must be a string"):
        _require_string(value, "medical finding")


def test_text_validation_requires_non_whitespace_content() -> None:
    """Distinguish required text from source fields that may be empty."""
    with pytest.raises(PipelineError, match="identity must be non-empty"):
        _require_text(" \n", "identity")


def test_date_validation_rejects_datetime_subclass() -> None:
    """Accept a calendar date while rejecting a timestamp masquerading as one."""
    assert _require_date(date(2026, 8, 24), "service date") is None
    with pytest.raises(PipelineError, match="service date must be a date"):
        _require_date(datetime(2026, 8, 24), "service date")


def test_typed_tuple_validation_returns_original_values() -> None:
    """Return the validated tuple with its element type available to callers."""
    values = ("one", "two")

    assert _require_non_empty_tuple_of(values, str, "names") is values


@pytest.mark.parametrize("value", [(), [], ["one"]])
def test_typed_tuple_validation_rejects_empty_or_mutable_collection(value: object) -> None:
    """Require a non-empty immutable collection before inspecting its items."""
    with pytest.raises(PipelineError, match="names must be a non-empty tuple"):
        _require_non_empty_tuple_of(value, str, "names")


def test_typed_tuple_validation_rejects_wrong_item_class() -> None:
    """Reject a tuple containing any value outside its declared model class."""
    with pytest.raises(PipelineError, match="names must contain only str values"):
        _require_non_empty_tuple_of(("one", 2), str, "names")
