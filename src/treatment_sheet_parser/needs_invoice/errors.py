"""Report failures from the read-only Needs Invoice workflow."""


class AirtableQueryError(ValueError):
    """Raised when an Airtable query fails or returns malformed data."""
