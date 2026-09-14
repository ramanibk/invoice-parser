"""Provide small validation primitives shared by pipeline data models."""

import re
from datetime import date as Date
from datetime import datetime as DateTime
from decimal import Decimal
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


def _require_money(value: object, field_name: str) -> None:
    """Require a finite nonnegative Decimal with at most two fractional places."""
    if not isinstance(value, Decimal):
        raise PipelineError(f"{field_name} must be a Decimal")
    if not value.is_finite():
        raise PipelineError(f"{field_name} must be finite")
    if value < 0:
        raise PipelineError(f"{field_name} must be nonnegative")
    if value.as_tuple().exponent < -2:
        raise PipelineError(f"{field_name} must have at most two fractional places")


def _require_microchip_number(value: object, field_name: str) -> None:
    """Require a null value or a normalized 9-to-15-digit microchip number."""
    if value is not None and (
        not isinstance(value, str) or re.fullmatch(r"\d{9,15}", value) is None
    ):
        raise PipelineError(f"{field_name} must contain 9 to 15 digits or be null")


def _require_tuple_of(
    value: object,
    item_type: type[ModelValue],
    field_name: str,
) -> tuple[ModelValue, ...]:
    """Return a tuple after validating every item against one type."""
    if not isinstance(value, tuple):
        raise PipelineError(f"{field_name} must be a tuple")
    if not all(isinstance(item, item_type) for item in value):
        raise PipelineError(f"{field_name} must contain only {item_type.__name__} values")
    return cast(tuple[ModelValue, ...], value)


def _require_non_empty_tuple_of(
    value: object,
    item_type: type[ModelValue],
    field_name: str,
) -> tuple[ModelValue, ...]:
    """Return a non-empty tuple after validating every item against one type."""
    if not isinstance(value, tuple) or not value:
        raise PipelineError(f"{field_name} must be a non-empty tuple")
    return _require_tuple_of(value, item_type, field_name)
