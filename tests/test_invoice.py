"""Tests for Nine Lives invoice parsing and monetary validation."""

import pytest

from treatment_sheet_parser.nlf.errors import InvoiceError
from treatment_sheet_parser.nlf.invoice import (
    _appointment,
    _appointments,
    _invoice_total,
)
from treatment_sheet_parser.nlf.models import Service


def test_appointment_includes_services_and_exact_total() -> None:
    """Parse an appointment's services and preserve its exact printed total."""
    block = """8/24/2026 Summer No Ear Tip! Bay Area Cats Cat 8.56 Female Cat Spay $155.00
(26-7089)
Rabies 1 year vaccine [0] $10.00
Pregnant Surcharge w/LRS $60.00
Administered
Intubation (cat) $100.00
Total of this appointment: $325.00
Total of this invoice: $325.00"""

    appointment = _appointment(block)

    assert appointment.service_date == "2026-08-24"
    assert appointment.services == (
        Service("Cat Spay", "155.00"),
        Service("Rabies 1 year vaccine [0]", "10.00"),
        Service("Pregnant Surcharge w/LRS Administered", "60.00"),
        Service("Intubation (cat)", "100.00"),
    )
    assert appointment.total_cost == "325.00"


def test_invoice_collects_appointments_from_multiple_dates() -> None:
    """Collect invoice appointments across multiple service dates."""
    text = """8/24/2026 Ada Smith Cat 8.00 Female Cat Spay $125.00
Total of this appointment: $125.00
8/25/2026 Bea Jones Cat 9.00 Female Rabies Vaccine $10.00
Total of this appointment: $10.00
Total of this invoice: $135.00"""

    appointments = _appointments(text)

    assert [item.service_date for item in appointments] == ["2026-08-24", "2026-08-25"]
    assert [item.identity_text for item in appointments] == ["Ada Smith", "Bea Jones"]


def test_service_sum_must_match_appointment_total() -> None:
    """Reject an appointment whose service sum differs from its total."""
    block = """8/24/2026 Ada Bay Area Cats Cat 8.00 Female Cat Spay $125.00
Total of this appointment: $130.00"""

    with pytest.raises(InvoiceError, match="service costs do not match"):
        _appointment(block)


def test_unmapped_service_still_counts_toward_service_total() -> None:
    """Include unfamiliar service lines when validating the appointment total."""
    block = """8/24/2026 Ada Bay Area Cats Cat 8.00 Female Unknown fee $25.00
Total of this appointment: $20.00"""

    with pytest.raises(InvoiceError, match="service costs do not match"):
        _appointment(block)


def test_invoice_total_must_be_consistent_across_pages() -> None:
    """Reject inconsistent invoice totals repeated across pages."""
    text = "Total of this invoice: $125.00\nTotal of this invoice: $130.00"

    with pytest.raises(InvoiceError, match="missing or inconsistent"):
        _invoice_total(text)


def test_non_numeric_service_cost_is_preserved_for_operator_decision() -> None:
    """Preserve a non-numeric cost for an explicit operator decision."""
    block = """8/24/2026 Ada Bay Area Cats Cat 8.00 Female Cat Spay $125.00
Rabies 1 year vaccine [0] $N/C
Total of this appointment: $125.00"""

    appointment = _appointment(block)

    assert appointment.services[-1] == Service("Rabies 1 year vaccine [0]", "N/C")
    assert appointment.total_cost == "125.00"
