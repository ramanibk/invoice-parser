"""Tests for complete atomic extraction artifact publication."""

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from errors import PipelineError
from extraction_output import publish_extraction
from models_extraction import AirtableService, ExtractionAppointment, ExtractionCat
from models_invoice import Invoice, InvoiceAppointment, InvoiceServiceLine
from models_treatment_sheet import MedicalFindings, TreatmentCat, TreatmentSheetAppointment
from pipeline_logging import plan_output_paths


def _extraction_cat() -> ExtractionCat:
    """Build one fully reconciled cat for publication tests."""
    treatment = TreatmentSheetAppointment(
        date(2026, 9, 3),
        "Female",
        "123456789012345",
        "Black",
        "7.50 lbs",
        MedicalFindings(exam="Healthy"),
    )
    cat = TreatmentCat(
        "26SEP03-NLF-1",
        "(F) Sample Cat",
        "Sample Cat",
        "Sample Owner",
        (treatment,),
    )
    visit = ExtractionAppointment(
        treatment,
        (AirtableService("Spay / Neuter", Decimal("125.00")),),
        Decimal("125.00"),
    )
    return ExtractionCat(cat, (visit,))


def _invoice(path: Path) -> Invoice:
    """Build the invoice metadata represented by the extraction cat."""
    service = InvoiceServiceLine("Cat Spay", Decimal("125.00"))
    appointment = InvoiceAppointment(
        date(2026, 9, 3),
        "Sample Cat",
        "26-7001",
        "Sample Owner",
        "Sample Cat (26-7001) Sample Owner",
        (service,),
        Decimal("125.00"),
    )
    return Invoice(path, (appointment,), Decimal("125.00"))


def _plan(output_dir: Path):
    """Plan deterministic publication paths for the shared test date."""
    started = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    return plan_output_paths(output_dir, date(2026, 9, 3), started_at=started)


def test_publishes_complete_extraction_json(tmp_path: Path) -> None:
    """Create the run directory only when the complete artifact can be published."""
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    plan = _plan(output_dir)
    manifest_path = (tmp_path / "inputs" / "manifest.json").resolve()
    invoice = _invoice((tmp_path / "inputs" / "invoice.pdf").resolve())

    artifact = publish_extraction(
        plan,
        manifest_path,
        date(2026, 9, 3),
        (_extraction_cat(),),
        invoice,
    )

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert artifact == output_dir / "26SEP03-NLF" / "extraction.json"
    assert payload["run_id"] == "26SEP03-NLF"
    assert payload["source_manifest"] == str(manifest_path)
    assert payload["cats"][0]["cat_id"] == "26SEP03-NLF-1"
    visit = payload["cats"][0]["appointments"]["2026-09-03"]
    assert visit["medical_findings"]["exam"] == "Healthy"
    assert visit["services"] == {"Spay / Neuter": 125.0}
    assert visit["total_cost"] == "125.00"
    assert payload["invoice"]["total_cost"] == "125.00"
    assert not (artifact.parent / ".extraction.json.tmp").exists()


def test_rejects_malformed_records_without_creating_run_directory(tmp_path: Path) -> None:
    """Leave no run directory when publication receives an incomplete record set."""
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()
    plan = _plan(output_dir)

    with pytest.raises(PipelineError, match="non-empty tuple"):
        publish_extraction(
            plan,
            (tmp_path / "manifest.json").resolve(),
            date(2026, 9, 3),
            (),
            _invoice((tmp_path / "invoice.pdf").resolve()),
        )

    assert not plan.run_directory.exists()
