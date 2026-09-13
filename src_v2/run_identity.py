"""Resolve short run dates and build stable identifiers for pipeline records."""

import re
from collections.abc import Callable
from datetime import date as Date

from errors import PipelineError
from global_constants import LOCATION_CODE, RUN_YEAR

MONTH_ABBREVIATIONS = (
    "JAN",
    "FEB",
    "MAR",
    "APR",
    "MAY",
    "JUN",
    "JUL",
    "AUG",
    "SEP",
    "OCT",
    "NOV",
    "DEC",
)


def resolve_run_date(
    value: str,
    *,
    today: Date | None = None,
    input_fn: Callable[[str], str] = input,
) -> Date:
    """Resolve a strict ``MM/DD`` value using the configured global run year."""
    current_date = today or Date.today()
    _confirm_run_year(current_date.year, input_fn)
    if re.fullmatch(r"\d{2}/\d{2}", value) is None:
        raise PipelineError("run date must use MM/DD format")
    month, day = (int(part) for part in value.split("/"))
    try:
        return Date(RUN_YEAR, month, day)
    except ValueError as exc:
        raise PipelineError(f"run date must be valid in {RUN_YEAR}") from exc


def _confirm_run_year(current_year: int, input_fn: Callable[[str], str]) -> None:
    """Require confirmation when the configured year differs from today's year."""
    if current_year == RUN_YEAR:
        return
    prompt = (
        f"Configured run year is {RUN_YEAR}, but the current year is {current_year}. "
        f"Use {RUN_YEAR}? [y/N] "
    )
    try:
        response = input_fn(prompt)
    except (EOFError, OSError) as exc:
        raise PipelineError(f"could not confirm configured run year {RUN_YEAR}") from exc
    if response.strip().casefold() not in {"y", "yes"}:
        raise PipelineError(f"configured run year {RUN_YEAR} was not confirmed")


def make_cat_id(run_date: Date, sequence: int) -> str:
    """Build a cat identifier from a run date and one-based manifest position."""
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        raise PipelineError("cat ID sequence must be a positive integer")
    return f"{make_run_id(run_date)}-{sequence}"


def make_run_id(run_date: Date) -> str:
    """Build the canonical run identifier for a resolved run date."""
    _validate_run_date(run_date)
    month = MONTH_ABBREVIATIONS[run_date.month - 1]
    return f"{run_date:%y}{month}{run_date:%d}-{LOCATION_CODE}"


def _validate_run_date(run_date: Date) -> None:
    """Reject values that are not dates in the configured run year."""
    if not isinstance(run_date, Date):
        raise PipelineError("run date must be a resolved date")
    if run_date.year != RUN_YEAR:
        raise PipelineError(f"run date year must be {RUN_YEAR}")
