"""Tests for short run-date resolution and stable run identifiers."""

from datetime import date

import pytest
from errors import PipelineError
from run_identity import make_cat_id, make_run_id, resolve_run_date


def test_resolves_short_date_in_configured_year() -> None:
    """Combine a strict month/day value with the global run year."""
    assert resolve_run_date("08/24", today=date(2026, 9, 13)) == date(2026, 8, 24)


@pytest.mark.parametrize("value", ["8/24", "08-24", "2026-08-24"])
def test_rejects_noncanonical_short_date(value: str) -> None:
    """Reject date inputs that do not exactly use zero-padded MM/DD format."""
    with pytest.raises(PipelineError, match="MM/DD"):
        resolve_run_date(value, today=date(2026, 9, 13))


def test_rejects_invalid_date_in_configured_year() -> None:
    """Reject a calendar date that does not exist in the configured year."""
    with pytest.raises(PipelineError, match="valid in 2026"):
        resolve_run_date("02/29", today=date(2026, 9, 13))


def test_confirms_configured_year_after_current_year_changes() -> None:
    """Explain a system-year mismatch and continue only after confirmation."""
    prompts: list[str] = []

    def confirm(prompt: str) -> str:
        prompts.append(prompt)
        return "yes"

    resolved = resolve_run_date("08/24", today=date(2027, 1, 1), input_fn=confirm)

    assert resolved == date(2026, 8, 24)
    assert prompts == [
        "Configured run year is 2026, but the current year is 2027. Use 2026? [y/N] "
    ]


def test_rejects_unconfirmed_configured_year() -> None:
    """Stop date resolution when the user rejects the configured year."""
    with pytest.raises(PipelineError, match="was not confirmed"):
        resolve_run_date("08/24", today=date(2027, 1, 1), input_fn=lambda _prompt: "no")


def test_builds_stable_run_and_cat_ids() -> None:
    """Build identities from the resolved date and manifest position."""
    run_date = date(2026, 8, 24)

    assert make_run_id(run_date) == "26AUG24-NLF"
    assert make_cat_id(run_date, 4) == "26AUG24-NLF-4"


def test_rejects_identity_with_wrong_year() -> None:
    """Prevent an identity from bypassing the configured run year."""
    with pytest.raises(PipelineError, match="year must be 2026"):
        make_run_id(date(2025, 8, 24))


@pytest.mark.parametrize("sequence", [0, -1, 1.5, True])
def test_rejects_invalid_cat_sequence(sequence: object) -> None:
    """Require a genuine positive integer for a manifest-position identity."""
    with pytest.raises(PipelineError, match="positive integer"):
        make_cat_id(date(2026, 8, 24), sequence)  # type: ignore[arg-type]
