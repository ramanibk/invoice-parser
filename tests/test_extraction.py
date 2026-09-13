"""Tests for manifest validation, extraction runs, and invoice merging."""

import json
from pathlib import Path

import pytest

from treatment_sheet_parser.nlf import extraction as nlf_extraction
from treatment_sheet_parser.nlf.errors import ExtractionError
from treatment_sheet_parser.nlf.extraction import extract_manifest
from treatment_sheet_parser.nlf.manifest import contains_name
from treatment_sheet_parser.nlf.models import (
    Appointment,
    CatRecord,
    Invoice,
    InvoiceAppointment,
    Service,
)


def _write_manifest(directory: Path, **sheet_changes: str) -> Path:
    """Create a minimal manifest and placeholder PDF for an extraction test."""
    sheet = {
        "owner": "Alexa Camorlinga",
        "catName": "No ear tip",
        "fileName": "sheet.pdf",
    }
    sheet.update(sheet_changes)
    path = directory / "manifest.json"
    path.write_text(
        json.dumps({"date": "2026-08-24", "treatmentSheets": [sheet]}),
        encoding="utf-8",
    )
    (directory / "sheet.pdf").touch()
    return path


def _record(*, owner_name: str = "Alexa Camorlinga") -> CatRecord:
    """Build a representative parsed cat record for extraction tests."""
    return CatRecord(
        cat_id="unused",
        display_name="Alexa Black - No ear tip Camorlinga",
        cat_name="Alexa Black - No ear tip Camorlinga",
        owner_name=owner_name,
        appointments={
            "2026-08-24": Appointment(
                gender="Male",
                microchip_number=None,
                color="Black",
                weight="11.18 lbs",
            )
        },
    )


def test_extraction_uses_run_id_for_folder_and_cat_id(tmp_path, monkeypatch) -> None:
    """Use the run ID consistently in the output folder and cat ID."""
    manifest = _write_manifest(tmp_path)
    monkeypatch.setattr(nlf_extraction, "parse_treatment_sheet", lambda *args, **kwargs: _record())

    output = extract_manifest(manifest, date="2026-08-24", outputs_dir=tmp_path / "out")
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert output == tmp_path / "out" / "26AUG24-NLF" / "extraction.json"
    assert payload["run_id"] == "26AUG24-NLF"
    assert payload["cats"][0]["cat_id"] == "26AUG24-NLF-1"
    assert payload["cats"][0]["display_name"] == "Alexa Black - No ear tip Camorlinga"
    assert payload["cats"][0]["cat_name"] == "No ear tip"
    medical = payload["cats"][0]["appointments"]["2026-08-24"]["medical_findings"]
    assert medical["rx"] == ""
    assert medical["medical_flag"] == ""


def test_later_run_gets_incremented_run_id(tmp_path, monkeypatch) -> None:
    """Give a later run a suffix instead of replacing prior output."""
    manifest = _write_manifest(tmp_path)
    monkeypatch.setattr(nlf_extraction, "parse_treatment_sheet", lambda *args, **kwargs: _record())

    extract_manifest(manifest, date="2026-08-24", outputs_dir=tmp_path / "out")
    output = extract_manifest(manifest, date="2026-08-24", outputs_dir=tmp_path / "out")

    assert output.parent.name == "26AUG24-NLF.1"


def test_extraction_shares_existing_needs_invoice_directory(tmp_path, monkeypatch) -> None:
    """Place extraction beside the Airtable snapshot for the same run."""
    manifest = _write_manifest(tmp_path)
    run_directory = tmp_path / "out" / "26AUG24-NLF"
    run_directory.mkdir(parents=True)
    (run_directory / "needs_invoice.json").write_text("{}\n")
    monkeypatch.setattr(nlf_extraction, "parse_treatment_sheet", lambda *args, **kwargs: _record())

    output = extract_manifest(manifest, date="2026-08-24", outputs_dir=tmp_path / "out")

    assert output == run_directory / "extraction.json"
    assert (run_directory / "needs_invoice.json").read_text() == "{}\n"


@pytest.mark.parametrize(
    ("sheet_changes", "owner_name", "message"),
    [
        ({}, "Different Owner", "owner mismatch"),
        ({"catName": "A different cat"}, "Alexa Camorlinga", "cat mismatch"),
    ],
)
def test_identity_mismatch_writes_no_output(
    tmp_path, monkeypatch, sheet_changes, owner_name, message
) -> None:
    """Write no output when parsed identity differs from the manifest."""
    manifest = _write_manifest(tmp_path, **sheet_changes)
    outputs = tmp_path / "out"
    monkeypatch.setattr(
        nlf_extraction,
        "parse_treatment_sheet",
        lambda *args, **kwargs: _record(owner_name=owner_name),
    )

    with pytest.raises(ExtractionError, match=message):
        extract_manifest(manifest, date="2026-08-24", outputs_dir=outputs)

    assert not outputs.exists()


def test_input_date_must_match_manifest(tmp_path) -> None:
    """Reject an input date that differs from the manifest date."""
    manifest = _write_manifest(tmp_path)

    with pytest.raises(ExtractionError, match="does not match"):
        extract_manifest(manifest, date="2026-08-25", outputs_dir=tmp_path / "out")


def test_input_date_requires_iso_format(tmp_path) -> None:
    """Reject an input date that is not in ISO format."""
    manifest = _write_manifest(tmp_path)

    with pytest.raises(ExtractionError, match="YYYY-MM-DD"):
        extract_manifest(manifest, date="08-24", outputs_dir=tmp_path / "out")


def test_cat_match_uses_complete_words() -> None:
    """Match cat names by complete words rather than substrings."""
    assert contains_name("Alexa Black - No ear tip Camorlinga", "No ear tip")
    assert not contains_name("Vane 10", "Vane 1")


def test_invoice_services_are_merged_into_extraction(tmp_path, monkeypatch) -> None:
    """Merge matching invoice services and totals into extracted appointments."""
    manifest = _write_manifest(tmp_path)
    invoice_path = tmp_path / "invoice.pdf"
    invoice_path.touch()
    invoice = Invoice(
        str(invoice_path),
        (
            InvoiceAppointment(
                "2026-08-24",
                "Alexa Black - No ear tip Camorlinga, Alexa",
                (Service("Cat Neuter", "155.00"), Service("Microchip", "10.00")),
                "165.00",
            ),
        ),
        "165.00",
    )
    monkeypatch.setattr(nlf_extraction, "parse_treatment_sheet", lambda *args, **kwargs: _record())
    monkeypatch.setattr(nlf_extraction, "parse_invoice", lambda path: invoice)

    output = extract_manifest(
        manifest, date="2026-08-24", outputs_dir=tmp_path / "out", invoice_path=invoice_path
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    appointment = payload["cats"][0]["appointments"]["2026-08-24"]

    assert appointment["services"] == {"Spay / Neuter": 155.0, "Microchip": 10.0}
    assert appointment["total_cost"] == "165.00"
    assert payload["invoice"]["total_cost"] == "165.00"


def test_invoice_identity_mismatch_writes_no_output(tmp_path, monkeypatch) -> None:
    """Write no output when no invoice appointment matches a manifest cat."""
    manifest = _write_manifest(tmp_path)
    invoice_path = tmp_path / "invoice.pdf"
    invoice_path.touch()
    invoice = Invoice(
        str(invoice_path),
        (InvoiceAppointment("2026-08-24", "Different Cat", (), "0.00"),),
        "0.00",
    )
    outputs = tmp_path / "out"
    monkeypatch.setattr(nlf_extraction, "parse_treatment_sheet", lambda *args, **kwargs: _record())
    monkeypatch.setattr(nlf_extraction, "parse_invoice", lambda path: invoice)

    with pytest.raises(ExtractionError, match="no invoice appointment matches"):
        extract_manifest(
            manifest, date="2026-08-24", outputs_dir=outputs, invoice_path=invoice_path
        )

    assert not outputs.exists()
