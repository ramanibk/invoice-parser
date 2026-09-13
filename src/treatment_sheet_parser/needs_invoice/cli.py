"""Command-line entry point for the read-only Airtable Needs Invoice query."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from treatment_sheet_parser.needs_invoice.airtable import AirtableClient
from treatment_sheet_parser.needs_invoice.errors import AirtableQueryError
from treatment_sheet_parser.needs_invoice.output import save_needs_invoice
from treatment_sheet_parser.needs_invoice.query import query_needs_invoice
from treatment_sheet_parser.shared.output import DEFAULT_OUTPUTS

# CLI option destinations mapped to their fallback environment variables.
AIRTABLE_ID_ENVIRONMENT = {
    "base_id": "AIRTABLE_BASE_ID",
    "table_id": "AIRTABLE_APPOINTMENTS_TABLE_ID",
    "cats_table_id": "AIRTABLE_CATS_TABLE_ID",
}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the read-only Needs_Invoice query and print its saved JSON path.

    Args:
        argv: Optional argument sequence; ``None`` uses the process arguments.

    Returns:
        Zero on success. Configuration and response errors are converted to
        ``argparse`` usage errors.
    """
    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    try:
        identifiers = _airtable_identifiers(arguments)
        client = AirtableClient(
            os.environ.get("AIRTABLE_TOKEN", ""), base_id=identifiers["base_id"]
        )
        result = query_needs_invoice(
            client,
            arguments.date,
            arguments.location,
            appointments_table_id=identifiers["table_id"],
            cats_table_id=identifiers["cats_table_id"],
        )
        output_path = save_needs_invoice(result, arguments.outputs_dir)
    except AirtableQueryError as exc:
        parser.error(str(exc))
    print(output_path)
    return 0


def _argument_parser() -> argparse.ArgumentParser:
    """Build the read-only Airtable query CLI parser.

    The date receives strict type conversion while location and Airtable IDs are
    validated by the query/client layers.
    """
    parser = argparse.ArgumentParser(description="Run the read-only Needs_Invoice Airtable query")
    parser.add_argument("date", type=_date, metavar="YYYY-MM-DD")
    parser.add_argument("location", metavar="LOCATION_CODE")
    parser.add_argument("--base-id")
    parser.add_argument("--table-id")
    parser.add_argument("--cats-table-id")
    parser.add_argument("--outputs-dir", type=Path, default=DEFAULT_OUTPUTS)
    return parser


def _airtable_identifiers(arguments: argparse.Namespace) -> dict[str, str]:
    """Resolve CLI Airtable identifiers, falling back to required environment values."""
    identifiers = {}
    for argument, environment_name in AIRTABLE_ID_ENVIRONMENT.items():
        value = getattr(arguments, argument) or os.environ.get(environment_name, "")
        if not value.strip():
            raise AirtableQueryError(f"{environment_name} must be set")
        identifiers[argument] = value
    return identifiers


def _date(value: str) -> date:
    """Parse a canonical ISO calendar date for ``argparse``.

    A round-trip check rejects non-zero-padded forms. Invalid input raises
    ``ArgumentTypeError`` so the CLI reports the faulty argument cleanly.
    """
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD")
    return parsed
