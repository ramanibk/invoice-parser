"""Tests for read-only invoice PDF parsing and monetary validation."""

from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, Mock

import invoice_extraction
import pytest
from errors import PipelineError
from invoice_extraction import (
    _parse_appointment,
    _parse_appointments,
    _parse_invoice_total,
    parse_invoice,
)
from models_invoice import Invoice


def _invoice_text() -> str:
    """Return representative extracted text with wrapped and repeated values."""
    return """8/24/2026 Summer No Ear Tip! Bay Area Cats Cat 8.56 Female Cat Spay $155.00
(26-7089)
Rabies 1 year vaccine [0] $10.00
Pregnant Surcharge w/LRS $60.00
Administered
Intubation (cat) $100.00
Total of this appointment: $325.00
Total of this invoice: $335.00
8/25/2026 Bea Jones Cat 9.00 Female Rabies Vaccine $10.00
Total of this appointment: $10.00
Total of this invoice: $335.00"""


def test_parse_invoice_returns_typed_exact_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Build a typed invoice while preserving order, identity, and Decimal costs."""
    invoice_path = tmp_path / "clinic invoice.pdf"
    invoice_path.write_bytes(b"%PDF-placeholder")
    monkeypatch.setattr(invoice_extraction, "_read_pdf_text", lambda _path: _invoice_text())

    invoice = parse_invoice(invoice_path)

    assert isinstance(invoice, Invoice)
    assert invoice.source_file == invoice_path
    assert [item.service_date for item in invoice.appointments] == [
        date(2026, 8, 24),
        date(2026, 8, 25),
    ]
    assert invoice.appointments[0].identity_text == "Summer No Ear Tip! Bay Area Cats"
    assert invoice.appointments[0].services[2].name == ("Pregnant Surcharge w/LRS Administered")
    assert invoice.appointments[0].services[2].cost == Decimal("60.00")
    assert invoice.total_cost == Decimal("335.00")
    assert not (tmp_path / "extraction.json").exists()


def test_reads_and_joins_text_from_every_pdf_page(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve page order and tolerate a page without extractable text."""
    invoice_path = tmp_path / "invoice.pdf"
    invoice_path.write_bytes(b"%PDF-placeholder")
    opened_pdf = MagicMock()
    opened_pdf.__enter__.return_value.pages = [
        Mock(extract_text=Mock(return_value="first page")),
        Mock(extract_text=Mock(return_value=None)),
        Mock(extract_text=Mock(return_value="third page")),
    ]
    monkeypatch.setattr(invoice_extraction.pdfplumber, "open", lambda _path: opened_pdf)

    text = invoice_extraction._read_pdf_text(invoice_path)

    assert text == "first page\n\nthird page"


def test_collects_appointments_only_from_line_anchored_dates() -> None:
    """Ignore dates embedded inside service descriptions or other invoice text."""
    text = """8/24/2026 Ada Smith Cat 8.00 Female Follow-up 8/20/2026 $10.00
Total of this appointment: $10.00
Total of this invoice: $10.00"""

    appointments = _parse_appointments(text)

    assert len(appointments) == 1
    assert appointments[0].service_date == date(2026, 8, 24)


def test_rejects_service_sum_mismatch() -> None:
    """Reject a visit whose service costs do not equal its printed total."""
    block = """8/24/2026 Ada Bay Area Cats Cat 8.00 Female Cat Spay $125.00
Total of this appointment: $130.00"""

    with pytest.raises(PipelineError, match="service costs do not match"):
        _parse_appointment(block)


def test_rejects_invoice_total_mismatch() -> None:
    """Reject an invoice whose appointment totals do not equal its printed total."""
    text = """8/24/2026 Ada Smith Cat 8.00 Female Cat Spay $125.00
Total of this appointment: $125.00
Total of this invoice: $130.00"""

    appointments = _parse_appointments(text)
    total = _parse_invoice_total(text)

    with pytest.raises(PipelineError, match="appointment totals do not match"):
        invoice_extraction._validate_invoice_total(appointments, total)


def test_rejects_inconsistent_repeated_invoice_totals() -> None:
    """Reject page footers that disagree about the invoice total."""
    text = "Total of this invoice: $125.00\nTotal of this invoice: $130.00"

    with pytest.raises(PipelineError, match="missing or inconsistent"):
        _parse_invoice_total(text)


def test_rejects_non_numeric_service_cost() -> None:
    """Reject an odd price token that cannot satisfy the strict v2 money model."""
    block = """8/24/2026 Ada Bay Area Cats Cat 8.00 Female Cat Spay $N/C
Total of this appointment: $0.00"""

    with pytest.raises(PipelineError, match="invoice service cost is invalid"):
        _parse_appointment(block)


def test_rejects_invalid_appointment_date() -> None:
    """Reject a syntactically matched date that is not a real calendar date."""
    block = """2/30/2026 Ada Bay Area Cats Cat 8.00 Female Cat Spay $125.00
Total of this appointment: $125.00"""

    with pytest.raises(PipelineError, match="appointment date is invalid"):
        _parse_appointment(block)


def test_rejects_missing_appointments() -> None:
    """Reject extracted invoice text without a date-led visit block."""
    with pytest.raises(PipelineError, match="invoice contains no appointments"):
        _parse_appointments("Total of this invoice: $0.00")


def test_rejects_missing_or_unresolved_source_path(tmp_path: Path) -> None:
    """Reject source identity mismatches before attempting PDF extraction."""
    with pytest.raises(PipelineError, match="absolute Path"):
        parse_invoice(Path("invoice.pdf"))
    with pytest.raises(PipelineError, match="existing PDF"):
        parse_invoice(tmp_path / "missing.pdf")


def test_translates_pdf_read_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Expose malformed PDF input as a controlled pipeline error."""
    invoice_path = tmp_path / "invoice.pdf"
    invoice_path.write_bytes(b"not a PDF")

    def fail_open(_path: Path) -> None:
        """Simulate pdfplumber rejecting malformed PDF syntax."""
        raise invoice_extraction.PDFSyntaxError("broken xref")

    monkeypatch.setattr(invoice_extraction.pdfplumber, "open", fail_open)

    with pytest.raises(PipelineError, match="could not read invoice"):
        parse_invoice(invoice_path)
