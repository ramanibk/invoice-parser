"""Orchestrate validated NLF treatment-sheet and invoice extraction runs."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

from treatment_sheet_parser.nlf.errors import ExtractionError
from treatment_sheet_parser.nlf.invoice import parse_invoice
from treatment_sheet_parser.nlf.invoice_matching import merge_invoice
from treatment_sheet_parser.nlf.manifest import (
    ManifestSheet,
    extract_sheets,
    load_manifest,
    validate_date,
)
from treatment_sheet_parser.nlf.models import CatRecord, Invoice
from treatment_sheet_parser.nlf.service_mapping import (
    OddCostDecider,
    UnknownServiceDecider,
    load_service_reference,
    write_service_reference,
)
from treatment_sheet_parser.nlf.treatment_sheet import parse_treatment_sheet
from treatment_sheet_parser.shared.output import DEFAULT_OUTPUTS, create_run_directory, write_json
from treatment_sheet_parser.shared.run_id import make_run_id

NLF_CODE = "NLF"
DEFAULT_SERVICE_MAP = Path(__file__).resolve().parent / "service_mapping.json"


def extract_manifest(
    manifest_path: str | Path,
    *,
    date: str,
    outputs_dir: str | Path = DEFAULT_OUTPUTS,
    invoice_path: str | Path | None = None,
    service_map_path: str | Path = DEFAULT_SERVICE_MAP,
    unknown_service_decider: UnknownServiceDecider | None = None,
    odd_cost_decider: OddCostDecider | None = None,
) -> Path:
    """Validate and extract a complete manifest into one JSON run artifact.

    All manifest entries, treatment sheets, and optional invoice mappings are
    validated before a run directory is reserved. This ordering guarantees that
    malformed or mismatched input cannot leave partial extraction output.

    Args:
        manifest_path: Source manifest JSON; relative sheet paths resolve beside it.
        date: Strict ISO run date, which must equal the manifest date.
        outputs_dir: Parent directory in which the run directory is created.
        invoice_path: Optional invoice PDF to merge into parsed appointments.
        service_map_path: JSON reference for canonical service mappings.
        unknown_service_decider: Explicit policy for unmapped invoice services.
        odd_cost_decider: Explicit policy for non-numeric service costs.

    Returns:
        Path to the atomically published ``extraction.json`` file.

    Raises:
        ExtractionError: If validation, identity matching, or output writing fails.
    """
    resolved_manifest = Path(manifest_path).expanduser().resolve()
    manifest = load_manifest(resolved_manifest)
    validate_date(date, manifest.date)
    records = extract_sheets(resolved_manifest.parent, manifest.sheets, date, parse_treatment_sheet)
    invoice = _parse_invoice(invoice_path)
    if invoice is not None:
        reference = load_service_reference(service_map_path)
        records = merge_invoice(
            records,
            invoice,
            reference,
            unknown_service_decider,
            odd_cost_decider,
        )
        write_service_reference(reference)
    run_directory = create_run_directory(
        Path(outputs_dir), make_run_id(date, NLF_CODE), "extraction.json", ExtractionError
    )
    payload = _payload(resolved_manifest, date, run_directory.name, records, invoice)
    return write_json(run_directory / "extraction.json", payload, ExtractionError, "extraction")


def _parse_invoice(path: str | Path | None) -> Invoice | None:
    """Parse an NLF invoice when a path is supplied.

    Returns ``None`` without resolving a parser when invoice processing was not
    requested.
    """
    return parse_invoice(path) if path is not None else None


def _payload(
    manifest_path: Path,
    date: str,
    run_id: str,
    records: list[tuple[ManifestSheet, CatRecord]],
    invoice: Invoice | None,
) -> dict[str, Any]:
    """Build the complete JSON-serializable payload for a validated run.

    Manifest identities replace parser-supplied identity fields, and cat IDs are
    derived from the final run ID so sequenced run directories remain consistent.
    Invoice metadata is included only when an invoice was provided.
    """
    payload = {
        "run_id": run_id,
        "source_manifest": str(manifest_path),
        "input_parameters": {"date": date, "vet": NLF_CODE},
        "cats": [
            _cat_payload(sheet, record, run_id, index)
            for index, (sheet, record) in enumerate(records, start=1)
        ],
    }
    if invoice is not None:
        payload["invoice"] = {
            "source_file": invoice.source_file,
            "currency": invoice.currency,
            "total_cost": invoice.total_cost,
        }
    return payload


def _cat_payload(
    sheet: ManifestSheet, record: CatRecord, run_id: str, sequence: int
) -> dict[str, Any]:
    """Create one output cat payload from parsed and manifest values.

    The manifest is authoritative for cat and owner names after the parsed PDF
    identity has already been checked against it.
    """
    result = asdict(record)
    result.update(
        {
            "cat_id": f"{run_id}-{sequence}",
            "cat_name": sheet.cat_name,
            "owner_name": sheet.owner_name,
        }
    )
    return result
