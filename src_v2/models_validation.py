"""Provide small validation primitives shared by pipeline data models."""

from datetime import date as Date
from datetime import datetime as DateTime
from typing import TypeVar, cast

from errors import PipelineError

ModelValue = TypeVar("ModelValue")


def _require_string(value: object, field_name: str) -> None:
    """Require a value to be a string without changing its exact contents."""
    if not isinstance(value, str):
        raise PipelineError(f"{field_name} must be a string")


def _require_text(value: object, field_name: str) -> None:
    """Require a string containing at least one non-whitespace character."""
    _require_string(value, field_name)
    if not value.strip():
        raise PipelineError(f"{field_name} must be non-empty")


def _require_date(value: object, field_name: str) -> None:
    """Require a calendar date without accepting a datetime subclass."""
    if not isinstance(value, Date) or isinstance(value, DateTime):
        raise PipelineError(f"{field_name} must be a date")


def _require_non_empty_tuple_of(
    value: object,
    item_type: type[ModelValue],
    field_name: str,
) -> tuple[ModelValue, ...]:
    """Return a non-empty tuple after validating every item against one type."""
    if not isinstance(value, tuple) or not value:
        raise PipelineError(f"{field_name} must be a non-empty tuple")
    if not all(isinstance(item, item_type) for item in value):
        raise PipelineError(f"{field_name} must contain only {item_type.__name__} values")
    return cast(tuple[ModelValue, ...], value)
