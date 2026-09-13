"""Persist validated Needs Invoice query results as JSON run artifacts."""

from __future__ import annotations

from pathlib import Path

from treatment_sheet_parser.needs_invoice.errors import AirtableQueryError
from treatment_sheet_parser.needs_invoice.query import NeedsInvoiceResult, result_document
from treatment_sheet_parser.shared.output import DEFAULT_OUTPUTS, create_run_directory, write_json
from treatment_sheet_parser.shared.run_id import make_run_id


def save_needs_invoice(
    result: NeedsInvoiceResult, outputs_dir: str | Path = DEFAULT_OUTPUTS
) -> Path:
    """Atomically save one validated Airtable snapshot in a shared run directory.

    The query layer constructs ``NeedsInvoiceResult`` only after validating all
    appointment and cat records. The snapshot shares the extraction run directory
    for its date and location. A numbered directory preserves earlier snapshots
    when the same date and location are queried more than once.

    Args:
        result: Complete validated query result to serialize.
        outputs_dir: Parent directory for generated run directories.

    Returns:
        Path to the published ``needs_invoice.json`` file.

    Raises:
        AirtableQueryError: If the output directory or JSON file cannot be written.
    """
    base = make_run_id(result.date, result.location_code)
    run_directory = create_run_directory(
        Path(outputs_dir), base, "needs_invoice.json", AirtableQueryError
    )
    return write_json(
        run_directory / "needs_invoice.json",
        result_document(result),
        AirtableQueryError,
        "Airtable output",
    )
