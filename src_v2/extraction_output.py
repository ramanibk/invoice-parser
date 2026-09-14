"""Build complete validated extraction JSON documents in memory."""

from dataclasses import asdict
from datetime import date as Date
from decimal import Decimal
from pathlib import Path
from typing import Any

from errors import PipelineError
from global_constants import LOCATION_CODE
from models_extraction import ExtractionAppointment, ExtractionCat
from models_invoice import Invoice
from models_validation import _require_date
from pipeline_logging import OutputPlan


def build_extraction_payload(
    plan: OutputPlan,
    manifest_path: Path,
    run_date: Date,
    extraction_cats: tuple[ExtractionCat, ...],
    invoice: Invoice,
) -> dict[str, Any]:
    """Build the complete JSON-compatible extraction document in memory."""
    _validate_payload_inputs(plan, manifest_path, run_date, extraction_cats, invoice)
    return {
        "run_id": plan.run_directory.name,
        "source_manifest": str(manifest_path),
        "input_parameters": {"date": run_date.isoformat(), "vet": LOCATION_CODE},
        "cats": [
            _build_cat_payload(extraction_cat, plan.run_directory.name)
            for extraction_cat in extraction_cats
        ],
        "invoice": {
            "source_file": str(invoice.source_file),
            "currency": invoice.currency,
            "total_cost": _money(invoice.total_cost),
        },
    }


def _validate_payload_inputs(
    plan: object,
    manifest_path: object,
    run_date: object,
    extraction_cats: object,
    invoice: object,
) -> None:
    """Reject malformed or cross-run inputs before creating any directory."""
    if not isinstance(plan, OutputPlan):
        raise PipelineError("extraction publication requires OutputPlan")
    _validate_manifest_path_and_date(manifest_path, run_date)
    _validate_extraction_cats(extraction_cats)
    if not isinstance(invoice, Invoice):
        raise PipelineError("extraction publication requires Invoice")


def _validate_manifest_path_and_date(manifest_path: object, run_date: object) -> None:
    """Require an absolute manifest path and a calendar run date."""
    if not isinstance(manifest_path, Path) or not manifest_path.is_absolute():
        raise PipelineError("extraction manifest path must be an absolute Path")
    _require_date(run_date, "extraction run date")


def _validate_extraction_cats(extraction_cats: object) -> None:
    """Require a complete immutable collection of extraction cats."""
    if not isinstance(extraction_cats, tuple) or not extraction_cats:
        raise PipelineError("extraction cats must be a non-empty tuple")
    if not all(isinstance(extraction_cat, ExtractionCat) for extraction_cat in extraction_cats):
        raise PipelineError("extraction cats must contain only ExtractionCat values")


def _build_cat_payload(extraction_cat: ExtractionCat, run_id: str) -> dict[str, Any]:
    """Convert one extraction cat to the stable artifact schema."""
    treatment_cat = extraction_cat.treatment_cat
    # A sequenced run-directory collision changes only the run prefix; retain the
    # cat's original one-based manifest sequence when rebuilding its artifact ID.
    sequence = treatment_cat.cat_id.rsplit("-", maxsplit=1)[-1]
    return {
        "cat_id": f"{run_id}-{sequence}",
        "display_name": treatment_cat.display_name,
        "cat_name": treatment_cat.cat_name,
        "owner_name": treatment_cat.owner_name,
        "appointments": {
            extraction_appointment.treatment_appointment.service_date.isoformat(): (
                _build_appointment_payload(extraction_appointment)
            )
            for extraction_appointment in extraction_cat.appointments
        },
    }


def _build_appointment_payload(
    extraction_appointment: ExtractionAppointment,
) -> dict[str, Any]:
    """Flatten treatment and invoice values for one dated visit."""
    treatment_appointment = extraction_appointment.treatment_appointment
    return {
        "gender": treatment_appointment.gender,
        "microchip_number": treatment_appointment.microchip_number,
        "color": treatment_appointment.color,
        "weight": treatment_appointment.weight,
        "medical_findings": asdict(treatment_appointment.medical_findings),
        # Service costs remain numeric for the established extraction schema;
        # aggregate totals use fixed strings to preserve currency precision.
        "services": {
            service.airtable_name: float(service.cost)
            for service in extraction_appointment.services
        },
        "total_cost": (
            None
            if extraction_appointment.total_cost is None
            else _money(extraction_appointment.total_cost)
        ),
    }


def _money(value: Decimal) -> str:
    """Render an already validated exact monetary value with two decimal places."""
    return f"{value:.2f}"
