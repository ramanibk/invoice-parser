"""Tests for complete terminal rendering of extracted review values."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from models_invoice import Invoice, InvoiceAppointment, InvoiceServiceLine
from models_treatment_sheet import MedicalFindings, TreatmentCat, TreatmentSheetAppointment
from review_output import print_invoice_extraction, print_treatment_sheet_extraction


def test_prints_complete_invoice_extraction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Render invoice identity, services, and exact totals before review."""
    invoice_path = tmp_path / "invoice.pdf"
    appointment = InvoiceAppointment(
        date(2026, 9, 3),
        "(F) Sample Cat",
        "26-7001",
        "Sample Owner",
        "(F) Sample Cat (26-7001) Sample Owner",
        (InvoiceServiceLine("Cat Spay", Decimal("125.00")),),
        Decimal("125.00"),
    )
    invoice = Invoice(invoice_path, (appointment,), Decimal("125.00"))

    print_invoice_extraction(invoice)

    output = capsys.readouterr().out
    assert "Invoice extraction" in output
    assert f'"source_file": "{invoice_path}"' in output
    assert '"service_date": "2026-09-03"' in output
    assert '"animal_display_name": "(F) Sample Cat"' in output
    assert '"animal_reference": "26-7001"' in output
    assert '"owner_name": "Sample Owner"' in output
    assert '"name": "Cat Spay"' in output
    assert output.count('"125.00"') == 3


def test_prints_complete_treatment_sheet_extraction(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Render cat identity, characteristics, and all medical fields before review."""
    source_path = tmp_path / "sample-cat.pdf"
    appointment = TreatmentSheetAppointment(
        date(2026, 9, 3),
        "Female",
        None,
        "Black",
        "7.70 lbs",
        MedicalFindings(exam="Healthy", client_communication="Return in two weeks"),
    )
    record = TreatmentCat(
        "26SEP03-NLF-1",
        "(F) Sample Cat",
        "Sample Cat",
        "Sample Owner",
        (appointment,),
    )

    print_treatment_sheet_extraction(record, source_path)

    output = capsys.readouterr().out
    assert "Treatment-sheet extraction: sample-cat.pdf" in output
    assert '"cat_id": "26SEP03-NLF-1"' in output
    assert '"display_name": "(F) Sample Cat"' in output
    assert '"microchip_number": null' in output
    assert '"weight": "7.70 lbs"' in output
    assert '"exam": "Healthy"' in output
    assert '"client_communication": "Return in two weeks"' in output
