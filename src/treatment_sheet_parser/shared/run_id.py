"""Create stable date-and-location identifiers shared by run artifacts."""

from datetime import datetime

# ``strftime("%b")`` is locale-dependent. An explicit table guarantees that
# generated IDs use the required English, uppercase month codes everywhere.
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


def make_run_id(date: str, location: str) -> str:
    """Build a validated ``YYMMMDD-LOCATION`` run identifier.

    Args:
        date: Date in strict ``YYYY-MM-DD`` format.
        location: Canonical clinic or location code.

    Returns:
        The formatted run identifier.

    Raises:
        ValueError: If the date is not in canonical ISO format.
    """
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError("run ID date must use YYYY-MM-DD format") from exc
    # ``strptime`` accepts some non-zero-padded values. Round-tripping rejects
    # those so IDs are always derived from one canonical input representation.
    if parsed_date.strftime("%Y-%m-%d") != date:
        raise ValueError("run ID date must use YYYY-MM-DD format")
    month = MONTH_ABBREVIATIONS[parsed_date.month - 1]
    date_code = f"{parsed_date:%y}{month}{parsed_date:%d}"
    return f"{date_code}-{location}"
