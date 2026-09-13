"""Command-line interface for NLF manifest extraction runs."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from treatment_sheet_parser.nlf.errors import ExtractionError, InvoiceError, TreatmentSheetError
from treatment_sheet_parser.nlf.extraction import DEFAULT_SERVICE_MAP, extract_manifest
from treatment_sheet_parser.nlf.service_mapping import ServiceMappingError
from treatment_sheet_parser.nlf.service_review import (
    InteractiveServiceDecider,
    load_airtable_service_options,
    prompt_odd_cost,
)
from treatment_sheet_parser.shared.output import DEFAULT_OUTPUTS

DEFAULT_SERVICE_OPTIONS = Path(__file__).resolve().parent / "airtable_service_options.json"


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments, run extraction, and print the created JSON path.

    Args:
        argv: Optional argument sequence; ``None`` uses the process arguments.

    Returns:
        Zero after a successful extraction. Domain failures are reported through
        ``argparse`` as command-line usage errors.
    """
    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    try:
        unknown_decider = _unknown_decider(arguments.invoice, arguments.service_options)
        output_path = extract_manifest(
            arguments.manifest,
            date=arguments.date,
            outputs_dir=arguments.outputs_dir,
            invoice_path=arguments.invoice,
            service_map_path=arguments.service_map,
            unknown_service_decider=unknown_decider,
            odd_cost_decider=prompt_odd_cost,
        )
    except (ExtractionError, InvoiceError, ServiceMappingError, TreatmentSheetError) as exc:
        parser.error(str(exc))
    print(output_path)
    return 0


def _unknown_decider(invoice: Path | None, options_path: Path):
    """Create an interactive unknown-service decider when invoice work is enabled.

    The Airtable option snapshot is not read for treatment-sheet-only runs.
    Returns ``None`` when ``invoice`` is absent.
    """
    if invoice is None:
        return None
    return InteractiveServiceDecider(load_airtable_service_options(options_path))


def _argument_parser() -> argparse.ArgumentParser:
    """Build the extraction CLI parser with all supported paths and selectors.

    Filesystem arguments are converted to ``Path`` objects; semantic validation
    remains in the extraction and parser layers.
    """
    parser = argparse.ArgumentParser(
        description="Extract and validate NLF treatment sheets listed in one manifest.json"
    )
    parser.add_argument("--date", required=True, metavar="YYYY-MM-DD")
    parser.add_argument("--outputs-dir", type=Path, default=DEFAULT_OUTPUTS)
    parser.add_argument("--invoice", type=Path, metavar="INVOICE_PDF")
    parser.add_argument(
        "--service-map",
        type=Path,
        default=DEFAULT_SERVICE_MAP,
        metavar="JSON",
    )
    parser.add_argument(
        "--service-options",
        type=Path,
        default=DEFAULT_SERVICE_OPTIONS,
        metavar="JSON",
    )
    parser.add_argument("manifest", type=Path, metavar="MANIFEST_JSON")
    return parser
