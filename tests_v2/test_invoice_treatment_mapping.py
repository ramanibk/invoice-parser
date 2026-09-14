"""Tests for one-to-one invoice and treatment-sheet reconciliation."""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from errors import PipelineError
from invoice_treatment_mapping import match_invoice_to_treatment_sheets
from models_invoice import Invoice, InvoiceAppointment, InvoiceServiceLine
from models_treatment_sheet import TreatmentCat, TreatmentSheetAppointment
from service_catalog import ServiceCatalog, ServiceCatalogEntry


def _catalog() -> ServiceCatalog:
    """Return a minimal catalog covering both supported Airtable service fields."""
    return ServiceCatalog(
        (
            ServiceCatalogEntry("Services", "Spay / Neuter", ("Cat Spay", "Cat Neuter")),
            ServiceCatalogEntry("Additional Services", "Exam", ("Wellness Exam",)),
        )
    )


def _invoice_appointment(
    *,
    cat_name: str = "Sample Cat",
    owner_name: str = "Owner, Sample",
    service_name: str = "Cat Spay",
    service_cost: str = "125.00",
) -> InvoiceAppointment:
    """Build one invoice visit with selected identity and service values."""
    service = InvoiceServiceLine(service_name, Decimal(service_cost))
    return InvoiceAppointment(
        date(2026, 9, 3),
        f"(F) {cat_name}",
        "26-7001",
        owner_name,
        f"(F) {cat_name} (26-7001) {owner_name}",
        (service,),
        Decimal(service_cost),
    )


def _invoice(path: Path, appointments: tuple[InvoiceAppointment, ...]) -> Invoice:
    """Build a complete invoice whose total equals its supplied visits."""
    total = sum((item.total_cost for item in appointments), Decimal())
    return Invoice(path, appointments, total)


def _record(
    *,
    cat_name: str = "Sample Cat",
    owner_name: str = "Sample Owner",
) -> TreatmentCat:
    """Build one treatment record for the shared test service date."""
    treatment = TreatmentSheetAppointment(date(2026, 9, 3), "Female", None, "Black", None)
    return TreatmentCat(
        "26SEP03-NLF-1",
        f"(F) {cat_name}",
        cat_name,
        owner_name,
        (treatment,),
    )


def test_matches_identity_and_maps_canonical_services(tmp_path: Path) -> None:
    """Match reordered owner tokens and attach canonical invoice billing values."""
    appointment = _invoice_appointment()
    invoice = _invoice(tmp_path / "invoice.pdf", (appointment,))

    extraction_cats = match_invoice_to_treatment_sheets(invoice, (_record(),), _catalog())

    visit = extraction_cats[0].appointments[0]
    assert visit.treatment_appointment.service_date == date(2026, 9, 3)
    assert [(service.airtable_name, service.cost) for service in visit.services] == [
        ("Spay / Neuter", Decimal("125.00"))
    ]
    assert visit.total_cost == Decimal("125.00")


def test_requires_invoice_only_for_latest_treatment_appointment(tmp_path: Path) -> None:
    """Retain an older treatment visit without requiring invoice billing values."""
    historical = TreatmentSheetAppointment(date(2026, 1, 23), "Female", None, "Black", None)
    latest = TreatmentSheetAppointment(date(2026, 9, 3), "Female", None, "Black", None)
    treatment_cat = TreatmentCat(
        "26SEP03-NLF-1",
        "Sample Cat",
        "Sample Cat",
        "Sample Owner",
        (historical, latest),
    )
    invoice = _invoice(tmp_path / "invoice.pdf", (_invoice_appointment(),))

    extraction_cats = match_invoice_to_treatment_sheets(invoice, (treatment_cat,), _catalog())

    historical_result, latest_result = extraction_cats[0].appointments
    assert historical_result.treatment_appointment is historical
    assert historical_result.services == ()
    assert historical_result.total_cost is None
    assert latest_result.treatment_appointment is latest
    assert latest_result.total_cost == Decimal("125.00")


def test_allows_unknown_owner_sentinel(tmp_path: Path) -> None:
    """Allow an N/A treatment owner when date and cat identity match."""
    invoice = _invoice(tmp_path / "invoice.pdf", (_invoice_appointment(),))

    extraction_cats = match_invoice_to_treatment_sheets(
        invoice,
        (_record(owner_name="N/A"),),
        _catalog(),
    )

    assert len(extraction_cats) == 1


@pytest.mark.parametrize(
    ("cat_name", "owner_name"),
    [("Different Cat", "Sample Owner"), ("Sample Cat", "Different Owner")],
)
def test_rejects_identity_mismatch(
    tmp_path: Path,
    cat_name: str,
    owner_name: str,
) -> None:
    """Reject treatment visits whose cat or owner cannot match the invoice."""
    invoice = _invoice(tmp_path / "invoice.pdf", (_invoice_appointment(),))

    with pytest.raises(PipelineError, match="no invoice appointment matches"):
        match_invoice_to_treatment_sheets(
            invoice,
            (_record(cat_name=cat_name, owner_name=owner_name),),
            _catalog(),
        )


def test_rejects_ambiguous_invoice_identity(tmp_path: Path) -> None:
    """Reject multiple unused invoice visits matching the same treatment visit."""
    appointments = (_invoice_appointment(), _invoice_appointment())
    invoice = _invoice(tmp_path / "invoice.pdf", appointments)

    with pytest.raises(PipelineError, match="multiple invoice appointments match"):
        match_invoice_to_treatment_sheets(invoice, (_record(),), _catalog())


def test_rejects_leftover_invoice_appointment(tmp_path: Path) -> None:
    """Reject an invoice visit that is absent from every treatment sheet."""
    appointments = (
        _invoice_appointment(),
        _invoice_appointment(cat_name="Unlisted Cat", owner_name="Other Owner"),
    )
    invoice = _invoice(tmp_path / "invoice.pdf", appointments)

    with pytest.raises(PipelineError, match="appointments not present in treatment sheets"):
        match_invoice_to_treatment_sheets(invoice, (_record(),), _catalog())


def test_rejects_unmapped_invoice_service(tmp_path: Path) -> None:
    """Reject an invoice description absent from the validated service catalog."""
    appointment = _invoice_appointment(service_name="Mystery Service")
    invoice = _invoice(tmp_path / "invoice.pdf", (appointment,))

    with pytest.raises(PipelineError, match="invoice service is not mapped"):
        match_invoice_to_treatment_sheets(invoice, (_record(),), _catalog())


def test_rejects_malformed_record_collection(tmp_path: Path) -> None:
    """Reject mutable or empty treatment record collections at the stage boundary."""
    invoice = _invoice(tmp_path / "invoice.pdf", (_invoice_appointment(),))

    with pytest.raises(PipelineError, match="non-empty tuple"):
        match_invoice_to_treatment_sheets(invoice, [], _catalog())  # type: ignore[arg-type]
