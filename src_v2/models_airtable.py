"""Represent normalized read-only Airtable values used by later stages."""

from dataclasses import dataclass
from datetime import date as Date
from decimal import Decimal

from errors import PipelineError
from global_constants import LOCATION_CODE, LOCATION_NAME
from models_validation import (
    _is_airtable_record_id,
    _require_date,
    _require_microchip_number,
    _require_money,
    _require_text,
    _require_tuple_of,
)


@dataclass(frozen=True)
class AirtableVoucher:
    """Pair one linked Airtable voucher identity with its displayed number."""

    airtable_voucher_id: str
    voucher_number: str

    def __post_init__(self) -> None:
        """Validate both the linked record ID and its required display value."""
        _require_record_id(self.airtable_voucher_id, "Airtable voucher ID")
        _require_text(self.voucher_number, "Airtable voucher number")


@dataclass(frozen=True)
class AirtableCatRecord:
    """Store one normalized cat joined to its scoped Airtable appointment."""

    # Appointment values are repeated for each linked cat because Airtable owns
    # them at appointment level while downstream matching operates per cat.
    airtable_cat_id: str
    airtable_appointment_id: str
    cat_name: str
    appointment_type: str
    appointment_owner_or_trapper: str | None = None
    appointment_cat_address: str | None = None
    appointment_cat_city: str | None = None
    cat_owner_or_trapper: str | None = None
    cat_address: str | None = None
    gender: str | None = None
    microchip_number: str | None = None
    color: str | None = None
    age: str | None = None
    ear_tip: str | None = None
    vouchers: tuple[AirtableVoucher, ...] = ()
    services: tuple[str, ...] = ()
    total_cost: Decimal | None = None

    def __post_init__(self) -> None:
        """Validate record identities and all normalized comparison values."""
        _require_record_id(self.airtable_cat_id, "Airtable cat ID")
        _require_record_id(self.airtable_appointment_id, "Airtable appointment ID")
        _require_text(self.cat_name, "Airtable cat name")
        _require_text(self.appointment_type, "Airtable appointment type")
        _validate_optional_fields(self)
        _require_microchip_number(self.microchip_number, "Airtable microchip number")
        _validate_vouchers(self.vouchers)
        _validate_services(self.services)
        if self.total_cost is not None:
            _require_money(self.total_cost, "Airtable appointment total")


@dataclass(frozen=True)
class AirtableSnapshot:
    """Store the complete normalized Airtable result for one pipeline run."""

    service_date: Date
    location_code: str
    location_name: str
    appointment_record_count: int
    cat_records: tuple[AirtableCatRecord, ...]

    def __post_init__(self) -> None:
        """Validate scope identity, source counts, and unique linked cat records."""
        _require_date(self.service_date, "Airtable snapshot service date")
        _validate_location(self.location_code, self.location_name)
        records = _require_tuple_of(
            self.cat_records, AirtableCatRecord, "Airtable snapshot cat records"
        )
        _validate_appointment_record_count(self.appointment_record_count, records)
        _validate_unique_cat_ids(records)

    @property
    def linked_cat_count(self) -> int:
        """Return the number of normalized linked cat records."""
        return len(self.cat_records)


def _require_record_id(value: object, field_name: str) -> None:
    """Require the syntactic shape of an Airtable record identifier."""
    if not _is_airtable_record_id(value):
        raise PipelineError(f"{field_name} must be an Airtable record ID")


def _validate_optional_fields(record: AirtableCatRecord) -> None:
    """Validate each optional normalized field without changing source text."""
    # Centralizing these similarly shaped fields keeps additions explicit while
    # preserving their exact display text for later identity comparison.
    names = (
        "appointment_owner_or_trapper",
        "appointment_cat_address",
        "appointment_cat_city",
        "cat_owner_or_trapper",
        "cat_address",
        "gender",
        "color",
        "age",
        "ear_tip",
    )
    for name in names:
        value = getattr(record, name)
        if value is not None:
            _require_text(value, f"Airtable {name.replace('_', ' ')}")


def _validate_vouchers(value: object) -> None:
    """Require typed vouchers with unique linked record identities."""
    vouchers = _require_tuple_of(value, AirtableVoucher, "Airtable vouchers")
    identifiers = [item.airtable_voucher_id for item in vouchers]
    if len(identifiers) != len(set(identifiers)):
        raise PipelineError("Airtable voucher IDs must be unique")


def _validate_services(value: object) -> None:
    """Require unique non-empty service names in their Airtable display order."""
    services = _require_tuple_of(value, str, "Airtable services")
    for service in services:
        _require_text(service, "Airtable service name")
    if len(services) != len(set(services)):
        raise PipelineError("Airtable service names must be unique")


def _validate_location(code: object, name: object) -> None:
    """Require the configured clinic code and its canonical Airtable name."""
    if code != LOCATION_CODE or name != LOCATION_NAME:
        raise PipelineError(f"Airtable location must be {LOCATION_CODE} / {LOCATION_NAME}")


def _validate_appointment_record_count(
    value: object,
    cat_records: tuple[AirtableCatRecord, ...],
) -> None:
    """Require a source count large enough for represented appointments."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PipelineError("Airtable appointment record count must be a nonnegative integer")
    # Several cats may share one appointment, so compare the count with distinct
    # appointment identities rather than the number of cat rows.
    appointment_ids = {item.airtable_appointment_id for item in cat_records}
    if len(appointment_ids) > value:
        raise PipelineError(
            "Airtable appointment record count cannot be smaller than represented appointments"
        )


def _validate_unique_cat_ids(cat_records: tuple[AirtableCatRecord, ...]) -> None:
    """Reject repeated cat identities within one frozen query scope."""
    identifiers = [item.airtable_cat_id for item in cat_records]
    if len(identifiers) != len(set(identifiers)):
        raise PipelineError("Airtable snapshot cat IDs must be unique")
