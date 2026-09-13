"""Create manifest-position cat identifiers for NLF extraction."""

from treatment_sheet_parser.shared.run_id import make_run_id


def make_cat_id(date: str, sequence: int) -> str:
    """Build an NLF cat ID from a date and one-based manifest position."""
    if sequence < 1:
        raise ValueError("cat ID sequence must be at least 1")
    try:
        run_id = make_run_id(date, "NLF")
    except ValueError as exc:
        raise ValueError("cat ID date must use YYYY-MM-DD format") from exc
    return f"{run_id}-{sequence}"
