"""Build and transactionally publish authoritative pipeline artifacts."""

import json
from dataclasses import asdict
from datetime import date as Date
from pathlib import Path
from uuid import uuid4

from errors import PipelineError
from extraction_output import build_extraction_payload
from models_airtable import AirtableCatRecord, AirtableSnapshot
from models_extraction import ExtractionCat
from models_invoice import Invoice
from pipeline_logging import OutputPlan

EXTRACTION_FILENAME = "extraction.json"
NEEDS_INVOICE_FILENAME = "needs_invoice.json"


def publish_run_snapshots(
    plan: OutputPlan,
    manifest_path: Path,
    run_date: Date,
    extraction_cats: tuple[ExtractionCat, ...],
    invoice: Invoice,
    airtable_snapshot: AirtableSnapshot,
) -> tuple[Path, Path]:
    """Publish extraction and Airtable snapshots as one new run directory."""
    if not isinstance(airtable_snapshot, AirtableSnapshot):
        raise PipelineError("run publication requires AirtableSnapshot")
    if airtable_snapshot.service_date != run_date:
        raise PipelineError("Airtable snapshot date must match the extraction run")
    extraction = _serialize_json(
        build_extraction_payload(plan, manifest_path, run_date, extraction_cats, invoice)
    )
    needs_invoice = _serialize_json(_snapshot_payload(airtable_snapshot))
    # Build both byte strings before touching the filesystem. The hidden sibling
    # directory keeps incomplete work outside the authoritative run path.
    staging = plan.output_dir / f".{plan.run_directory.name}.{uuid4().hex}.tmp"
    try:
        plan.output_dir.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        (staging / EXTRACTION_FILENAME).write_text(extraction, encoding="utf-8")
        (staging / NEEDS_INVOICE_FILENAME).write_text(needs_invoice, encoding="utf-8")
        # A same-filesystem directory rename exposes the validated pair at once.
        staging.replace(plan.run_directory)
    except OSError as exc:
        _remove_staging(staging)
        raise PipelineError(f"could not publish run snapshots: {exc}") from exc
    return (
        plan.run_directory / EXTRACTION_FILENAME,
        plan.run_directory / NEEDS_INVOICE_FILENAME,
    )


def _snapshot_payload(snapshot: AirtableSnapshot) -> dict[str, object]:
    """Convert a normalized Airtable snapshot to its stable JSON document."""
    return {
        "date": snapshot.service_date.isoformat(),
        "location_code": snapshot.location_code,
        "location": snapshot.location_name,
        "total_records": snapshot.appointment_record_count,
        "linked_cats": snapshot.linked_cat_count,
        "cats": [_cat_payload(record) for record in snapshot.cat_records],
    }


def _cat_payload(record: AirtableCatRecord) -> dict[str, object]:
    """Convert one typed Airtable cat record to comparison-ready JSON."""
    values = asdict(record)
    vouchers = values["vouchers"]
    # Keep the richer ID-label pairs and the legacy label-only list because each
    # serves a distinct downstream consumer.
    values["voucher_numbers"] = [item["voucher_number"] for item in vouchers]
    values["vouchers"] = list(vouchers)
    values["services"] = list(values["services"])
    cost = values["total_cost"]
    values["total_cost"] = None if cost is None else f"{cost:.2f}"
    return values


def _remove_staging(staging: Path) -> None:
    """Best-effort remove only the staging directory created by this attempt."""
    try:
        for path in staging.iterdir() if staging.is_dir() else ():
            path.unlink(missing_ok=True)
        staging.rmdir()
    except OSError:
        pass


def _serialize_json(payload: dict[str, object]) -> str:
    """Serialize a complete artifact before creating publication state."""
    try:
        return json.dumps(payload, indent=2) + "\n"
    except (TypeError, ValueError) as exc:
        raise PipelineError("could not serialize run artifact") from exc
