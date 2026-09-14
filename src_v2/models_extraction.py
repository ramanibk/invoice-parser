"""Represent invoice visits merged into validated treatment-sheet records."""

from dataclasses import dataclass
from decimal import Decimal

from errors import PipelineError
from models_treatment_sheet import TreatmentCat, TreatmentSheetAppointment
from models_validation import _require_money, _require_non_empty_tuple_of, _require_text


@dataclass(frozen=True)
class AirtableService:
    """Store one Airtable service name with its summed invoice cost."""

    airtable_name: str
    cost: Decimal

    def __post_init__(self) -> None:
        """Require a named service and an exact nonnegative cost."""
        _require_text(self.airtable_name, "Airtable service name")
        _require_money(self.cost, "Airtable service cost")


@dataclass(frozen=True)
class ExtractionAppointment:
    """Combine one treatment appointment with its invoice billing values."""

    treatment_appointment: TreatmentSheetAppointment
    services: tuple[AirtableService, ...]
    total_cost: Decimal

    def __post_init__(self) -> None:
        """Require a treatment visit, mapped services, and exact invoice total."""
        if not isinstance(self.treatment_appointment, TreatmentSheetAppointment):
            raise PipelineError("extraction appointment must contain a TreatmentSheetAppointment")
        _require_non_empty_tuple_of(self.services, AirtableService, "extraction services")
        _require_money(self.total_cost, "extraction appointment total")


@dataclass(frozen=True)
class ExtractionCat:
    """Store one treatment cat after every visit has matched the invoice."""

    treatment_cat: TreatmentCat
    appointments: tuple[ExtractionAppointment, ...]

    def __post_init__(self) -> None:
        """Require aligned treatment and extraction appointment collections."""
        if not isinstance(self.treatment_cat, TreatmentCat):
            raise PipelineError("extraction cat must contain a TreatmentCat")
        appointments = _require_non_empty_tuple_of(
            self.appointments,
            ExtractionAppointment,
            "extraction cat appointments",
        )
        treatment_dates = tuple(
            appointment.service_date for appointment in self.treatment_cat.appointments
        )
        extraction_dates = tuple(
            appointment.treatment_appointment.service_date for appointment in appointments
        )
        # Exact ordered equality prevents a caller from dropping, duplicating, or
        # reordering visits while retaining an otherwise valid treatment cat.
        if extraction_dates != treatment_dates:
            raise PipelineError("extraction appointments must align with treatment appointments")
