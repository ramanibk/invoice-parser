"""Tests for validated treatment appointments with optional invoice billing."""

from datetime import date
from decimal import Decimal

import pytest
from errors import PipelineError
from models_extraction import AirtableService, ExtractionAppointment
from models_treatment_sheet import TreatmentSheetAppointment


def _treatment_appointment() -> TreatmentSheetAppointment:
    """Build a valid treatment visit shared by extraction model tests."""
    return TreatmentSheetAppointment(date(2026, 1, 23), "Female", None, "Black", None)


def test_accepts_historical_appointment_without_invoice_values() -> None:
    """Allow an informational visit with neither services nor a total."""
    appointment = ExtractionAppointment(_treatment_appointment(), (), None)

    assert appointment.services == ()
    assert appointment.total_cost is None


def test_rejects_unbilled_appointment_with_services() -> None:
    """Reject partial billing data containing services but no invoice total."""
    services = (AirtableService("Exam", Decimal("25.00")),)

    with pytest.raises(PipelineError, match="unbilled.*cannot contain services"):
        ExtractionAppointment(_treatment_appointment(), services, None)


def test_rejects_billed_appointment_without_services() -> None:
    """Reject partial billing data containing an invoice total but no services."""
    with pytest.raises(PipelineError, match="billed.*must contain services"):
        ExtractionAppointment(_treatment_appointment(), (), Decimal("25.00"))
