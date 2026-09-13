"""Validate Needs Invoice records, values, identities, and linked relationships."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date

from treatment_sheet_parser.needs_invoice.airtable import is_record_id
from treatment_sheet_parser.needs_invoice.config import (
    APPOINTMENT_FIELD_MAP,
    APPOINTMENT_TYPES,
    CAT_AGES,
    CAT_FIELD_MAP,
    EAR_TIPS,
)
from treatment_sheet_parser.needs_invoice.errors import AirtableQueryError

# These aliases keep record-oriented signatures compact without hiding their shape.
Record = Mapping[str, object]
Records = tuple[Record, ...]
DisplayFields = Mapping[str, Mapping[str, object]]


def validate_appointments(records: Records, appointment_date: date, location: str) -> None:
    """Validate appointment fields and require the requested date and location.

    Args:
        records: Typed Airtable appointment records.
        appointment_date: Exact date every returned appointment must contain.
        location: Canonical clinic name every returned appointment must contain.

    Raises:
        AirtableQueryError: If a field is malformed or a record is outside the query scope.
    """
    for record in records:
        fields = record["fields"]
        _validate_appointment_fields(record["id"], fields)
        if fields["Date"] != appointment_date.isoformat() or fields["Location"] != location:
            raise AirtableQueryError(
                f"Airtable record {record['id']} does not match the requested date and location"
            )


def index_appointments_by_cat(records: Records, appointment_date: date) -> dict[str, Record]:
    """Return each linked cat's appointment and reject ambiguous identities.

    Repeated links within one appointment count once. A cat linked to two scoped
    appointments is rejected because its appointment-level output would be ambiguous.
    """
    appointments_by_cat: dict[str, Record] = {}
    for record in records:
        for cat_id in dict.fromkeys(record["fields"].get("Cats", [])):
            if cat_id in appointments_by_cat:
                raise AirtableQueryError(
                    f"Airtable Cat {cat_id} has multiple appointments for "
                    f"{appointment_date.isoformat()}"
                )
            appointments_by_cat[cat_id] = record
    return appointments_by_cat


def validate_cats(records: Records) -> None:
    """Validate all Cat-table fields consumed by the normalized output.

    Cat name is required. Optional text, choices, links, colors, and microchip
    values are checked only when present.
    """
    for record in records:
        fields = record["fields"]
        name = fields.get(CAT_FIELD_MAP["cat_name"])
        if not isinstance(name, str) or not name.strip():
            raise AirtableQueryError(f"Airtable Cat {record['id']} has an invalid Cat Name")
        _validate_optional_string(record["id"], fields, CAT_FIELD_MAP["gender"])
        _validate_string_list(record["id"], fields, CAT_FIELD_MAP["color"])
        _validate_record_links(record["id"], fields, CAT_FIELD_MAP["owner_or_trapper"])
        _validate_optional_string(record["id"], fields, CAT_FIELD_MAP["address"])
        _validate_optional_choice(record["id"], fields, CAT_FIELD_MAP["age"], CAT_AGES)
        _validate_optional_choice(record["id"], fields, CAT_FIELD_MAP["ear_tip"], EAR_TIPS)
        _validate_record_links(record["id"], fields, CAT_FIELD_MAP["vouchers"])
        normalize_microchip(record["id"], fields.get(CAT_FIELD_MAP["microchip_number"]))


def validate_display_records(
    records: Records, fields: tuple[str, ...]
) -> dict[str, Mapping[str, object]]:
    """Validate string-format fields and index them by source record ID.

    Airtable may omit empty fields from string-format responses. Any present
    requested value must be a non-empty string.
    """
    for record in records:
        for field in fields:
            _validate_optional_string(record["id"], record["fields"], field)
    return {record["id"]: record["fields"] for record in records}


def validate_linked_displays(
    appointments: Records,
    cats: Records,
    appointment_displays: DisplayFields,
    cat_displays: DisplayFields,
) -> None:
    """Require every typed linked value to agree with its display-format value.

    Owner IDs are used only for this completeness check. Voucher IDs are also
    retained later so downstream updates can address the exact voucher records.
    """
    for appointment in appointments:
        _validate_appointment_display(appointment, appointment_displays[appointment["id"]])
    for cat in cats:
        _validate_cat_display(cat, cat_displays[cat["id"]])


def normalize_microchip(record_id: object, value: object) -> str | None:
    """Validate and normalize an optional integral microchip to a digit string."""
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not float(value).is_integer()
    ):
        raise AirtableQueryError(f"Airtable record {record_id} has an invalid Microchip")
    return str(int(value))


def normalize_cost(record_id: object, value: object) -> str | None:
    """Validate and normalize an optional finite currency value to two decimals."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AirtableQueryError(f"Airtable record {record_id} has an invalid Cost")
    return f"{value:.2f}"


def _validate_appointment_fields(record_id: object, fields: Mapping[str, object]) -> None:
    """Validate fields consumed from one typed appointment record."""
    try:
        date.fromisoformat(fields["Date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AirtableQueryError(f"Airtable record {record_id} has an invalid Date") from exc
    if not isinstance(fields.get("Location"), str) or not fields["Location"].strip():
        raise AirtableQueryError(f"Airtable record {record_id} has an invalid Location")
    _validate_record_links(record_id, fields, "Cats")
    _validate_required_choice(record_id, fields, APPOINTMENT_FIELD_MAP["type"], APPOINTMENT_TYPES)
    _validate_record_links(record_id, fields, APPOINTMENT_FIELD_MAP["owner_or_trapper"])
    _validate_optional_string(record_id, fields, APPOINTMENT_FIELD_MAP["cat_address"])
    _validate_optional_string(record_id, fields, APPOINTMENT_FIELD_MAP["cat_city"])
    for field in APPOINTMENT_FIELD_MAP["services"]:
        _validate_string_list(record_id, fields, field)
    normalize_cost(record_id, fields.get(APPOINTMENT_FIELD_MAP["total_cost"]))


def _validate_appointment_display(record: Record, display: Mapping[str, object]) -> None:
    """Validate one appointment's linked owner display against its typed IDs."""
    field = APPOINTMENT_FIELD_MAP["owner_or_trapper"]
    _validate_link_presence(
        record["id"], record["fields"].get(field, []), display.get(field), field
    )


def _validate_cat_display(record: Record, display: Mapping[str, object]) -> None:
    """Validate one cat's owner and voucher displays against their typed IDs."""
    fields = record["fields"]
    owner_field = CAT_FIELD_MAP["owner_or_trapper"]
    voucher_field = CAT_FIELD_MAP["vouchers"]
    _validate_link_presence(
        record["id"], fields.get(owner_field, []), display.get(owner_field), owner_field
    )
    _validate_display_count(
        record["id"], fields.get(voucher_field, []), display.get(voucher_field), voucher_field
    )


def _validate_display_count(record_id: object, links: object, value: object, field: str) -> None:
    """Require non-empty display labels matching the typed link count."""
    _validate_link_presence(record_id, links, value, field)
    labels = [] if value is None else [item.strip() for item in value.split(",")]
    if len(labels) != len(links) or any(not label for label in labels):
        raise AirtableQueryError(f"Airtable record {record_id} has invalid {field} display values")


def _validate_link_presence(record_id: object, links: object, value: object, field: str) -> None:
    """Reject disagreement between typed link presence and a displayed value."""
    if bool(links) != (value is not None):
        raise AirtableQueryError(f"Airtable record {record_id} has inconsistent {field} links")


def _validate_string_list(record_id: object, fields: Mapping[str, object], field: str) -> None:
    """Validate an optional multiple-select field as non-empty strings."""
    value = fields.get(field, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise AirtableQueryError(f"Airtable record {record_id} has an invalid {field}")


def _validate_optional_string(record_id: object, fields: Mapping[str, object], field: str) -> None:
    """Validate an optional text or single-select value when present."""
    value = fields.get(field)
    if value is not None and (not isinstance(value, str) or not value):
        raise AirtableQueryError(f"Airtable record {record_id} has an invalid {field}")


def _validate_record_links(record_id: object, fields: Mapping[str, object], field: str) -> None:
    """Validate an optional linked-record field as Airtable record IDs."""
    value = fields.get(field, [])
    if not isinstance(value, list) or any(not is_record_id(item) for item in value):
        raise AirtableQueryError(f"Airtable record {record_id} has invalid {field} links")


def _validate_required_choice(
    record_id: object, fields: Mapping[str, object], field: str, choices: set[str]
) -> None:
    """Require a single-select value to be one of the configured choices."""
    if fields.get(field) not in choices:
        raise AirtableQueryError(f"Airtable record {record_id} has an invalid {field}")


def _validate_optional_choice(
    record_id: object, fields: Mapping[str, object], field: str, choices: set[str]
) -> None:
    """Validate a present single-select value against configured choices."""
    value = fields.get(field)
    if value is not None and value not in choices:
        raise AirtableQueryError(f"Airtable record {record_id} has an invalid {field}")
