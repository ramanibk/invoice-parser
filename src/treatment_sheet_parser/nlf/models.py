"""Represent NLF treatment-sheet and invoice extraction data."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Service:
    """One raw invoiced service and its printed cost value.

    Attributes:
        name: Service description printed on the invoice.
        cost: A numeric dollar amount normalized to two places, or the exact
            non-numeric price token pending an operator error-or-skip decision.
    """

    name: str
    cost: str


@dataclass(frozen=True)
class InvoiceAppointment:
    """Invoice values for one animal visit.

    Attributes:
        service_date: Visit date in ``YYYY-MM-DD`` format.
        identity_text: Patient and owner text printed between the visit date and
            the invoice's species, weight, and gender columns. It is retained
            for matching the invoice row to a manifest entry.
        services: Services billed for this visit, in invoice order.
        total_cost: Exact appointment total normalized to two decimal places.
    """

    service_date: str
    identity_text: str
    services: tuple[Service, ...]
    total_cost: str


@dataclass(frozen=True)
class Invoice:
    """Validated NLF invoice values ready to merge into cat records.

    Attributes:
        source_file: Absolute path of the parsed invoice PDF.
        appointments: All invoice visits in printed order. Dates may differ.
        total_cost: Exact total for the complete invoice.
        currency: Currency used by every stored cost.
    """

    source_file: str
    appointments: tuple[InvoiceAppointment, ...]
    total_cost: str
    currency: str = "USD"


@dataclass(frozen=True)
class MedicalFindings:
    """Exact text from medical and procedural treatment-sheet fields."""

    services_received: str = ""
    appointment_animal_notes: str = ""
    exam: str = ""
    surgery_decline_reason: str = ""
    high_risk_waiver_reason: str = ""
    owner_response: str = ""
    medical_flag: str = ""
    drugs_administered: str = ""
    surgical_summary: str = ""
    internal_notes: str = ""
    notes: str = ""
    tests: str = ""
    rx: str = ""
    client_communication: str = ""


@dataclass(frozen=True)
class Appointment:
    """Values extracted from one appointment.

    Attributes:
        gender: Printed patient gender.
        microchip_number: Microchip digits, or ``None`` when the field is blank.
        color: Printed coat color, or ``None`` when the field is blank.
        weight: Weight with its ``lbs`` unit, or ``None`` when blank.
        medical_findings: Exact text from treatment-sheet medical fields.
        services: Canonical Airtable service values keyed to numeric costs.
        total_cost: Exact appointment total in dollars, or ``None`` without an invoice.
    """

    gender: str
    microchip_number: str | None
    color: str | None
    weight: str | None
    medical_findings: MedicalFindings = field(default_factory=MedicalFindings)
    services: dict[str, float] = field(default_factory=dict)
    total_cost: str | None = None


@dataclass(frozen=True)
class CatRecord:
    """Represent one NLF manifest cat and its parsed appointments.

    Attributes:
        cat_id: Generated ``YYMMMDD-VET-N`` identifier.
        display_name: Complete patient name exactly as printed in the PDF.
        cat_name: Cat name used by the caller; initially the full display name.
        owner_name: Printed owner name, or ``N/A`` for a blank owner field.
        appointments: Appointments keyed by ISO service date.
    """

    cat_id: str
    display_name: str
    cat_name: str
    owner_name: str
    appointments: dict[str, Appointment]

    def to_dict(self) -> dict[str, Any]:
        """Convert the record and nested appointments to plain dictionaries.

        Returns:
            A JSON-serializable dictionary containing the complete record.
        """
        return asdict(self)
