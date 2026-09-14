"""Tests for paired extraction and Airtable snapshot publication."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from errors import PipelineError
from models_airtable import AirtableCatRecord, AirtableSnapshot, AirtableVoucher
from models_extraction import AirtableService, ExtractionAppointment, ExtractionCat
from models_invoice import Invoice, InvoiceAppointment, InvoiceServiceLine
from models_treatment_sheet import TreatmentCat, TreatmentSheetAppointment
from pipeline_logging import plan_output_paths
from run_artifacts import publish_run_snapshots


def _inputs(tmp_path: Path):
    """Build aligned extraction, invoice, and Airtable publication inputs."""
    run_date = date(2026, 9, 3)
    treatment_appointment = TreatmentSheetAppointment(run_date, "Female", None, "Black", None)
    treatment_cat = TreatmentCat(
        "26SEP03-NLF-1",
        "Sample Cat",
        "Sample Cat",
        "Sample Owner",
        (treatment_appointment,),
    )
    invoice_service = InvoiceServiceLine("Cat Spay", Decimal("125.00"))
    invoice_appointment = InvoiceAppointment(
        run_date,
        "Sample Cat",
        "26-1",
        "Sample Owner",
        "Sample Cat Sample Owner",
        (invoice_service,),
        Decimal("125.00"),
    )
    invoice = Invoice(tmp_path / "invoice.pdf", (invoice_appointment,), Decimal("125.00"))
    extraction = (
        ExtractionCat(
            treatment_cat,
            (
                ExtractionAppointment(
                    treatment_appointment,
                    (AirtableService("Spay / Neuter", Decimal("125.00")),),
                    Decimal("125.00"),
                ),
            ),
        ),
    )
    snapshot = AirtableSnapshot(
        run_date,
        "NLF",
        "Nine Lives Foundation",
        1,
        (
            AirtableCatRecord(
                "recCat1",
                "recAppointment1",
                "Sample Cat",
                "Pet",
                vouchers=(AirtableVoucher("recVoucher1", "V-1"),),
                services=("Spay / Neuter",),
                total_cost=Decimal("125.00"),
            ),
        ),
    )
    return run_date, extraction, invoice, snapshot


def test_publishes_both_authoritative_snapshots_together(tmp_path: Path) -> None:
    """Expose one completed run directory containing both required snapshots."""
    run_date, extraction, invoice, snapshot = _inputs(tmp_path)
    output_dir = tmp_path / "outputs"
    plan = plan_output_paths(
        output_dir, run_date, started_at=datetime(2026, 9, 14, tzinfo=timezone.utc)
    )
    manifest = (tmp_path / "manifest.json").resolve()

    extraction_path, airtable_path = publish_run_snapshots(
        plan, manifest, run_date, extraction, invoice, snapshot
    )

    assert extraction_path.is_file()
    assert airtable_path.is_file()
    document = json.loads(airtable_path.read_text(encoding="utf-8"))
    assert document["cats"][0]["airtable_cat_id"] == "recCat1"
    assert document["cats"][0]["voucher_numbers"] == ["V-1"]
    assert document["cats"][0]["services"] == ["Spay / Neuter"]
    assert document["cats"][0]["total_cost"] == "125.00"


def test_rejects_cross_date_snapshot_without_creating_run(tmp_path: Path) -> None:
    """Reject mismatched authoritative state before creating any run directory."""
    run_date, extraction, invoice, snapshot = _inputs(tmp_path)
    output_dir = tmp_path / "outputs"
    plan = plan_output_paths(output_dir, run_date)
    mismatched = AirtableSnapshot(
        date(2026, 9, 4),
        snapshot.location_code,
        snapshot.location_name,
        snapshot.appointment_record_count,
        snapshot.cat_records,
    )

    with pytest.raises(PipelineError, match="date must match"):
        publish_run_snapshots(
            plan,
            (tmp_path / "manifest.json").resolve(),
            run_date,
            extraction,
            invoice,
            mismatched,
        )

    assert not plan.run_directory.exists()
