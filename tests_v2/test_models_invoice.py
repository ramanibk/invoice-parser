"""Tests for exact and validated invoice data models."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from errors import PipelineError
from models_invoice import Invoice, InvoiceAppointment, InvoiceServiceLine


def _service(**changes: object) -> InvoiceServiceLine:
    """Build a valid invoice service line with selected test overrides."""
    values = {"name": "Cat Spay", "cost": Decimal("125.00")}
    values.update(changes)
    return InvoiceServiceLine(**values)  # type: ignore[arg-type]


def _appointment(**changes: object) -> InvoiceAppointment:
    """Build a valid invoice appointment with selected test overrides."""
    values = {
        "service_date": date(2026, 8, 24),
        "identity_text": "Nebula Delgado, Alexa Camorlinga",
        "services": (_service(),),
        "total_cost": Decimal("125.00"),
    }
    values.update(changes)
    return InvoiceAppointment(**values)  # type: ignore[arg-type]


def test_service_line_preserves_exact_decimal_cost() -> None:
    """Retain the service amount as a two-place Decimal without float conversion."""
    line = _service(cost=Decimal("10.20"))

    assert line.cost == Decimal("10.20")
    assert line.cost.as_tuple().exponent == -2


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": ""}, "service name must be non-empty"),
        ({"name": 12}, "service name must be a string"),
        ({"cost": 125.0}, "service cost must be a Decimal"),
        ({"cost": Decimal("NaN")}, "service cost must be finite"),
        ({"cost": Decimal("Infinity")}, "service cost must be finite"),
        ({"cost": Decimal("-0.01")}, "service cost must be nonnegative"),
        ({"cost": Decimal("1.001")}, "at most two fractional places"),
    ],
)
def test_rejects_malformed_service_line(changes: dict[str, object], message: str) -> None:
    """Reject missing descriptions and inexact or invalid monetary values."""
    with pytest.raises(PipelineError, match=message):
        _service(**changes)


def test_invoice_appointment_preserves_ordered_services_and_total() -> None:
    """Retain invoice order, identity text, and the exact appointment total."""
    services = (
        _service(),
        _service(name="Rabies 1 year vaccine", cost=Decimal("10.00")),
    )
    appointment = _appointment(services=services, total_cost=Decimal("135.00"))

    assert appointment.services == services
    assert appointment.identity_text == "Nebula Delgado, Alexa Camorlinga"
    assert appointment.total_cost == Decimal("135.00")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"service_date": "2026-08-24"}, "service date must be a date"),
        ({"service_date": datetime(2026, 8, 24)}, "service date must be a date"),
        ({"identity_text": " "}, "identity must be non-empty"),
        ({"identity_text": None}, "identity must be a string"),
        ({"services": []}, "services must be a non-empty tuple"),
        ({"services": ()}, "services must be a non-empty tuple"),
        ({"services": ("Cat Spay",)}, "must contain only InvoiceServiceLine values"),
        ({"total_cost": "125.00"}, "appointment total must be a Decimal"),
    ],
)
def test_rejects_malformed_invoice_appointment(changes: dict[str, object], message: str) -> None:
    """Reject malformed dates, identities, service collections, and totals."""
    with pytest.raises(PipelineError, match=message):
        _appointment(**changes)


def test_invoice_preserves_source_identity_and_exact_total(tmp_path: Path) -> None:
    """Retain a validated absolute source path, appointments, currency, and total."""
    source_file = tmp_path / "invoice.pdf"
    appointments = (_appointment(),)

    invoice = Invoice(source_file, appointments, Decimal("125.00"))

    assert invoice.source_file == source_file
    assert invoice.appointments == appointments
    assert invoice.total_cost == Decimal("125.00")
    assert invoice.currency == "USD"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"source_file": "invoice.pdf"}, "source file must be a Path"),
        ({"source_file": Path("invoice.pdf")}, "absolute PDF path"),
        ({"source_file": Path("/tmp/invoice.txt")}, "absolute PDF path"),
        ({"appointments": []}, "appointments must be a non-empty tuple"),
        ({"appointments": ()}, "appointments must be a non-empty tuple"),
        ({"appointments": ("visit",)}, "must contain only InvoiceAppointment values"),
        ({"total_cost": Decimal("12.345")}, "at most two fractional places"),
        ({"currency": "EUR"}, "currency must be USD"),
    ],
)
def test_rejects_malformed_invoice(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    """Reject invalid source identity, appointment collection, total, or currency."""
    values = {
        "source_file": tmp_path / "invoice.pdf",
        "appointments": (_appointment(),),
        "total_cost": Decimal("125.00"),
        "currency": "USD",
    }
    values.update(changes)

    with pytest.raises(PipelineError, match=message):
        Invoice(**values)  # type: ignore[arg-type]
