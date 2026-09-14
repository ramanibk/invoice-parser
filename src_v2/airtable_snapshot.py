"""Build a normalized cat-by-cat snapshot from read-only Airtable records."""

from collections.abc import Mapping
from datetime import date as Date
from decimal import Decimal, InvalidOperation
from typing import cast
from urllib.request import urlopen

from airtable_query import Transport, query_appointments, query_records_by_ids
from errors import PipelineError
from global_constants import LOCATION_CODE, LOCATION_NAME
from models_airtable import AirtableCatRecord, AirtableSnapshot, AirtableVoucher
from models_validation import _is_airtable_record_id
from pipeline_config import AirtableConfig

CAT_FIELDS = (
    "Cat Name",
    "Gender",
    "Microchip",
    "Color",
    "Trapper or Owner",
    "Address",
    "Age",
    "Ear Tip",
    "Voucher",
)
APPOINTMENT_DISPLAY_FIELDS = ("Owner or Trapper",)
CAT_DISPLAY_FIELDS = ("Trapper or Owner", "Voucher")
APPOINTMENT_TYPES = {"Pet", "TNR", "BAC Foster", "Intake"}
CAT_AGES = {"Adult", "Neonate (0-4 Weeks)", "Weaned (4-8 Weeks)", "Juvenile", "Unknown"}
EAR_TIPS = {"None", "Yes - Left", "Yes - Right"}

Record = Mapping[str, object]


def query_airtable_snapshot(
    config: AirtableConfig, service_date: Date, *, transport: Transport = urlopen
) -> AirtableSnapshot:
    """Fetch and normalize the complete Airtable comparison scope."""
    appointments = query_appointments(config, service_date, transport=transport)
    appointments_by_cat = _index_appointments(appointments)
    cat_ids = tuple(appointments_by_cat)
    # Typed reads retain linked record IDs. Separate displayed-string reads
    # provide owner and voucher labels used as identity evidence by Codex.
    appointment_displays = _records_by_id(
        query_records_by_ids(
            config,
            config.appointments_table_id,
            tuple(str(item["id"]) for item in appointments),
            APPOINTMENT_DISPLAY_FIELDS,
            cell_format="string",
            transport=transport,
        )
    )
    cats = query_records_by_ids(
        config, config.cats_table_id, cat_ids, CAT_FIELDS, transport=transport
    )
    cat_displays = _records_by_id(
        query_records_by_ids(
            config,
            config.cats_table_id,
            cat_ids,
            CAT_DISPLAY_FIELDS,
            cell_format="string",
            transport=transport,
        )
    )
    # Responses need not follow request order, so join by stable Airtable IDs
    # while emitting cats in the original appointment-link order.
    cats_by_id = {str(item["id"]): item for item in cats}
    records = tuple(
        _normalize_cat(
            cats_by_id[cat_id],
            appointments_by_cat[cat_id],
            appointment_displays[str(appointments_by_cat[cat_id]["id"])],
            cat_displays[cat_id],
        )
        for cat_id in cat_ids
    )
    return AirtableSnapshot(service_date, LOCATION_CODE, LOCATION_NAME, len(appointments), records)


def _index_appointments(appointments: tuple[Record, ...]) -> dict[str, Record]:
    """Index each linked cat to one appointment and reject ambiguous links."""
    indexed: dict[str, Record] = {}
    for appointment in appointments:
        fields = _fields(appointment)
        cat_ids = _record_links(fields, "Cats")
        _validate_appointment(fields)
        for cat_id in dict.fromkeys(cat_ids):
            # Appointment services and cost belong to the appointment. Two
            # appointments for one cat would make the flattened snapshot unclear.
            if cat_id in indexed:
                raise PipelineError("an Airtable cat has multiple scoped appointments")
            indexed[cat_id] = appointment
    return indexed


def _validate_appointment(fields: Record) -> None:
    """Validate appointment values used by matching and snapshot output."""
    _required_text(fields, "Type")
    _choice(fields, "Type", APPOINTMENT_TYPES)
    _record_links(fields, "Owner or Trapper")
    _optional_text(fields, "Cat Address")
    _optional_text(fields, "Cat City")
    _string_list(fields, "Services")
    _string_list(fields, "Additional Services")
    _optional_money(fields.get("Cost"))


def _normalize_cat(
    cat: Record, appointment: Record, appointment_display: Record, cat_display: Record
) -> AirtableCatRecord:
    """Join one validated cat to its appointment and displayed linked values."""
    cat_fields = _fields(cat)
    appointment_fields = _fields(appointment)
    _validate_cat(cat_fields)
    owner = _displayed_link(appointment_fields, appointment_display, "Owner or Trapper")
    cat_owner = _displayed_link(cat_fields, cat_display, "Trapper or Owner")
    vouchers = _vouchers(cat_fields, cat_display)
    return AirtableCatRecord(
        airtable_cat_id=str(cat["id"]),
        airtable_appointment_id=str(appointment["id"]),
        cat_name=cast(str, cat_fields["Cat Name"]),
        appointment_type=cast(str, appointment_fields["Type"]),
        appointment_owner_or_trapper=owner,
        appointment_cat_address=cast(str | None, appointment_fields.get("Cat Address")),
        appointment_cat_city=cast(str | None, appointment_fields.get("Cat City")),
        cat_owner_or_trapper=cat_owner,
        cat_address=cast(str | None, cat_fields.get("Address")),
        gender=cast(str | None, cat_fields.get("Gender")),
        microchip_number=_microchip(cat_fields.get("Microchip")),
        color=_color(cat_fields.get("Color", [])),
        age=cast(str | None, cat_fields.get("Age")),
        ear_tip=cast(str | None, cat_fields.get("Ear Tip")),
        vouchers=vouchers,
        services=_services(appointment_fields),
        total_cost=_optional_money(appointment_fields.get("Cost")),
    )


def _validate_cat(fields: Record) -> None:
    """Validate the typed Cat-table values used for comparison."""
    _required_text(fields, "Cat Name")
    for name in ("Gender", "Address", "Age", "Ear Tip"):
        _optional_text(fields, name)
    _optional_choice(fields, "Age", CAT_AGES)
    _optional_choice(fields, "Ear Tip", EAR_TIPS)
    _string_list(fields, "Color")
    _record_links(fields, "Trapper or Owner")
    _record_links(fields, "Voucher")


def _records_by_id(records: tuple[Record, ...]) -> dict[str, Record]:
    """Index displayed fields by their already validated record IDs."""
    return {str(record["id"]): _fields(record) for record in records}


def _fields(record: Record) -> Record:
    """Return one record's fields after low-level response validation."""
    return cast(Record, record["fields"])


def _record_links(fields: Record, name: str) -> tuple[str, ...]:
    """Validate and return an optional linked-record field."""
    value = fields.get(name, [])
    if not isinstance(value, list) or any(not _is_airtable_record_id(item) for item in value):
        raise PipelineError(f"Airtable record has invalid {name} links")
    return tuple(cast(list[str], value))


def _displayed_link(typed: Record, displayed: Record, name: str) -> str | None:
    """Require linked-record presence to agree with its displayed label."""
    links = _record_links(typed, name)
    value = displayed.get(name)
    _optional_text(displayed, name)
    # A missing label is valid only when the typed record has no linked IDs.
    if bool(links) != (value is not None):
        raise PipelineError(f"Airtable record has inconsistent {name} links")
    return cast(str | None, value)


def _vouchers(typed: Record, displayed: Record) -> tuple[AirtableVoucher, ...]:
    """Pair voucher IDs with display labels in Airtable link order."""
    identifiers = _record_links(typed, "Voucher")
    value = displayed.get("Voucher")
    _optional_text(displayed, "Voucher")
    labels = () if value is None else tuple(item.strip() for item in cast(str, value).split(","))
    # Positional pairing is safe only when every linked voucher has one label.
    if len(labels) != len(identifiers) or any(not label for label in labels):
        raise PipelineError("Airtable record has invalid Voucher display values")
    pairs = zip(identifiers, labels, strict=True)
    return tuple(AirtableVoucher(item, label) for item, label in pairs)


def _services(fields: Record) -> tuple[str, ...]:
    """Combine both Airtable service fields while preserving first occurrence."""
    values = (
        *_string_list(fields, "Services"),
        *_string_list(fields, "Additional Services"),
    )
    # Preserve Airtable field order while preventing the same option from being
    # repeated when it appears in both service fields.
    return tuple(dict.fromkeys(values))


def _string_list(fields: Record, name: str) -> tuple[str, ...]:
    """Validate and return one optional list of non-empty strings."""
    value = fields.get(name, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise PipelineError(f"Airtable record has an invalid {name}")
    return tuple(cast(list[str], value))


def _required_text(fields: Record, name: str) -> None:
    """Require one present non-empty string field."""
    value = fields.get(name)
    if not isinstance(value, str) or not value:
        raise PipelineError(f"Airtable record has an invalid {name}")


def _optional_text(fields: Record, name: str) -> None:
    """Validate one optional string field when present."""
    value = fields.get(name)
    if value is not None and (not isinstance(value, str) or not value):
        raise PipelineError(f"Airtable record has an invalid {name}")


def _choice(fields: Record, name: str, choices: set[str]) -> None:
    """Require one field to contain a supported Airtable choice."""
    if fields.get(name) not in choices:
        raise PipelineError(f"Airtable record has an invalid {name}")


def _optional_choice(fields: Record, name: str, choices: set[str]) -> None:
    """Validate an optional Airtable choice when present."""
    value = fields.get(name)
    if value is not None and value not in choices:
        raise PipelineError(f"Airtable record has an invalid {name}")


def _microchip(value: object) -> str | None:
    """Normalize an optional integral Airtable microchip number."""
    if value is None:
        return None
    invalid_type = isinstance(value, bool) or not isinstance(value, (int, float))
    if invalid_type or not float(value).is_integer():
        raise PipelineError("Airtable record has an invalid Microchip")
    return str(int(value))


def _color(value: object) -> str | None:
    """Join a validated Airtable color list into comparison text."""
    values = _string_list({"Color": value}, "Color")
    return " / ".join(values) if values else None


def _optional_money(value: object) -> Decimal | None:
    """Convert an optional finite Airtable numeric cost to exact decimal money."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PipelineError("Airtable record has an invalid Cost")
    try:
        # Convert through text so a binary float does not introduce extra digits.
        money = Decimal(str(value))
    except InvalidOperation as exc:
        raise PipelineError("Airtable record has an invalid Cost") from exc
    if not money.is_finite():
        raise PipelineError("Airtable record has an invalid Cost")
    return money
