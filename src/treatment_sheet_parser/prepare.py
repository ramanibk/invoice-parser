"""Run extraction, the Needs Invoice snapshot, and mapping-prompt preparation."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from treatment_sheet_parser.cat_mapping.errors import CatMappingPromptError
from treatment_sheet_parser.cat_mapping.prompt import generate_cat_mapping_prompt
from treatment_sheet_parser.needs_invoice.airtable import AirtableClient
from treatment_sheet_parser.needs_invoice.cli import AIRTABLE_ID_ENVIRONMENT
from treatment_sheet_parser.needs_invoice.errors import AirtableQueryError
from treatment_sheet_parser.needs_invoice.output import save_needs_invoice
from treatment_sheet_parser.needs_invoice.query import query_needs_invoice
from treatment_sheet_parser.nlf.cli import DEFAULT_SERVICE_OPTIONS
from treatment_sheet_parser.nlf.errors import ExtractionError, InvoiceError, TreatmentSheetError
from treatment_sheet_parser.nlf.extraction import DEFAULT_SERVICE_MAP, extract_manifest
from treatment_sheet_parser.nlf.service_mapping import ServiceMappingError
from treatment_sheet_parser.nlf.service_review import (
    InteractiveServiceDecider,
    load_airtable_service_options,
    prompt_odd_cost,
)
from treatment_sheet_parser.shared.output import DEFAULT_OUTPUTS
from treatment_sheet_parser.shared.run_id import make_run_id

LOCATION_CODE = "NLF"


class PrepareError(ValueError):
    """Report a condition that prevents creation of one paired prepare run."""


def main(argv: Sequence[str] | None = None) -> int:
    """Create paired run artifacts and print the first-pass cat-mapping prompt."""
    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    try:
        prompt = _prepare(arguments)
    except (
        AirtableQueryError,
        CatMappingPromptError,
        ExtractionError,
        InvoiceError,
        PrepareError,
        ServiceMappingError,
        TreatmentSheetError,
    ) as exc:
        parser.error(str(exc))
    print(prompt)
    return 0


def _prepare(arguments: argparse.Namespace) -> str:
    """Run all prepare stages after resolving configuration and validating run state."""
    appointment_date = arguments.date.isoformat()
    manifest, invoice = _input_paths(arguments.input_dir)
    _reject_incomplete_runs(arguments.outputs_dir, appointment_date)
    identifiers = _airtable_identifiers(arguments)
    client = AirtableClient(os.environ.get("AIRTABLE_TOKEN", ""), base_id=identifiers["base_id"])
    result = query_needs_invoice(
        client,
        arguments.date,
        LOCATION_CODE,
        appointments_table_id=identifiers["table_id"],
        cats_table_id=identifiers["cats_table_id"],
    )
    extraction_path = _extract(arguments, appointment_date, manifest, invoice)
    needs_invoice_path = save_needs_invoice(result, arguments.outputs_dir)
    _require_paired_paths(extraction_path, needs_invoice_path)
    return generate_cat_mapping_prompt(appointment_date, LOCATION_CODE, arguments.outputs_dir)


def _extract(
    arguments: argparse.Namespace, appointment_date: str, manifest: Path, invoice: Path
) -> Path:
    """Run extraction with interactive invoice-service decisions."""
    decider = InteractiveServiceDecider(load_airtable_service_options(arguments.service_options))
    return extract_manifest(
        manifest,
        date=appointment_date,
        outputs_dir=arguments.outputs_dir,
        invoice_path=invoice,
        service_map_path=arguments.service_map,
        unknown_service_decider=decider,
        odd_cost_decider=prompt_odd_cost,
    )


def _input_paths(input_dir: Path) -> tuple[Path, Path]:
    """Find the fixed manifest and sole invoice-named PDF in one input directory."""
    directory = input_dir.expanduser().resolve()
    if not directory.is_dir():
        raise PrepareError(f"input directory does not exist: {directory}")
    manifest = directory / "manifest.json"
    if not manifest.is_file():
        raise PrepareError(f"input directory must contain manifest.json: {directory}")
    try:
        invoices = tuple(
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".pdf"
            and "invoice" in path.name.casefold()
        )
    except OSError as exc:
        raise PrepareError(f"could not inspect input directory {directory}: {exc}") from exc
    if len(invoices) != 1:
        raise PrepareError(
            f"input directory must contain exactly one invoice-named PDF; found {len(invoices)}"
        )
    return manifest, invoices[0]


def _reject_incomplete_runs(outputs_dir: Path, appointment_date: str) -> None:
    """Reject pre-existing target runs containing only one required snapshot."""
    run_id = make_run_id(appointment_date, LOCATION_CODE)
    try:
        candidates = () if not outputs_dir.exists() else tuple(outputs_dir.iterdir())
        incomplete = [path for path in candidates if _is_incomplete_run(path, run_id)]
    except OSError as exc:
        raise PrepareError(f"could not inspect outputs directory {outputs_dir}: {exc}") from exc
    if incomplete:
        names = ", ".join(sorted(path.name for path in incomplete))
        raise PrepareError(f"cannot prepare beside incomplete run directories: {names}")


def _is_incomplete_run(path: Path, run_id: str) -> bool:
    """Return whether a canonical target run contains exactly one prepare artifact."""
    if not path.is_dir() or not _is_run_name(path.name, run_id):
        return False
    extraction_exists = (path / "extraction.json").exists()
    needs_invoice_exists = (path / "needs_invoice.json").exists()
    return extraction_exists != needs_invoice_exists


def _is_run_name(name: str, run_id: str) -> bool:
    """Accept an unsuffixed run name or a canonical positive numeric suffix."""
    if name == run_id:
        return True
    suffix = name.removeprefix(f"{run_id}.")
    return (
        name.startswith(f"{run_id}.")
        and suffix.isdigit()
        and int(suffix) > 0
        and str(int(suffix)) == suffix
    )


def _require_paired_paths(extraction: Path, needs_invoice: Path) -> None:
    """Require both prepare artifacts to have landed in the same run directory."""
    if extraction.parent != needs_invoice.parent:
        raise PrepareError("extraction and Needs Invoice outputs were not written to the same run")


def _airtable_identifiers(arguments: argparse.Namespace) -> dict[str, str]:
    """Resolve Airtable identifiers from explicit options or required environment values."""
    identifiers = {}
    for argument, environment_name in AIRTABLE_ID_ENVIRONMENT.items():
        value = getattr(arguments, argument) or os.environ.get(environment_name, "")
        if not value.strip():
            raise AirtableQueryError(f"{environment_name} must be set")
        identifiers[argument] = value
    return identifiers


def _date(value: str) -> date:
    """Parse a strictly zero-padded ISO calendar date for the prepare CLI."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD")
    return parsed


def _argument_parser() -> argparse.ArgumentParser:
    """Build the combined NLF prepare command-line interface."""
    parser = argparse.ArgumentParser(
        description="Prepare NLF extraction, Needs Invoice data, and a cat-mapping prompt"
    )
    parser.add_argument("--date", required=True, type=_date, metavar="YYYY-MM-DD")
    parser.add_argument("--outputs-dir", type=Path, default=DEFAULT_OUTPUTS)
    parser.add_argument("--service-map", type=Path, default=DEFAULT_SERVICE_MAP, metavar="JSON")
    parser.add_argument(
        "--service-options", type=Path, default=DEFAULT_SERVICE_OPTIONS, metavar="JSON"
    )
    parser.add_argument("--base-id")
    parser.add_argument("--table-id")
    parser.add_argument("--cats-table-id")
    parser.add_argument("input_dir", type=Path, metavar="INPUT_DIR")
    return parser
