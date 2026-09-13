"""Join validated Needs Invoice records into flat cat-by-cat output payloads."""

from __future__ import annotations

from collections.abc import Mapping

from treatment_sheet_parser.needs_invoice.config import APPOINTMENT_FIELD_MAP, CAT_FIELD_MAP
from treatment_sheet_parser.needs_invoice.validation import normalize_cost, normalize_microchip

# Compact aliases describe the validated Airtable structures accepted here.
Record = Mapping[str, object]
Records = tuple[Record, ...]
DisplayFields = Mapping[str, Mapping[str, object]]


def build_cat_payloads(
    cat_ids: tuple[str, ...],
    cats: Records,
    appointments_by_cat: Mapping[str, Record],
    appointment_displays: DisplayFields,
    cat_displays: DisplayFields,
) -> tuple[Record, ...]:
    """Build one normalized record per cat in original appointment-link order.

    All source records and linked displays must be validated before this function
    is called. Cat and appointment IDs remain in each payload for downstream writes.
    """
    cats_by_id = {record["id"]: record for record in cats}
    return tuple(
        _cat_payload(
            cats_by_id[cat_id],
            appointments_by_cat[cat_id],
            appointment_displays,
            cat_displays[cat_id],
        )
        for cat_id in cat_ids
    )


def _cat_payload(
    cat: Record,
    appointment: Record,
    appointment_displays: DisplayFields,
    cat_display: Mapping[str, object],
) -> Record:
    """Combine one cat with its unique appointment and human-readable links."""
    fields = cat["fields"]
    payload = {
        "airtable_cat_id": cat["id"],
        "airtable_appointment_id": appointment["id"],
        "cat_name": fields[CAT_FIELD_MAP["cat_name"]],
    }
    payload.update(
        _comparison_fields(
            appointment,
            appointment_displays[appointment["id"]],
            cat["id"],
            fields,
            cat_display,
        )
    )
    return payload


def _comparison_fields(
    appointment: Record,
    appointment_display: Mapping[str, object],
    cat_id: object,
    cat_fields: Mapping[str, object],
    cat_display: Mapping[str, object],
) -> Record:
    """Translate validated Airtable fields into extraction-compatible names."""
    fields = appointment["fields"]
    colors = cat_fields.get(CAT_FIELD_MAP["color"], [])
    voucher_numbers, vouchers = _vouchers(cat_fields, cat_display)
    return {
        "appointment_type": fields[APPOINTMENT_FIELD_MAP["type"]],
        "appointment_owner_or_trapper": appointment_display.get(
            APPOINTMENT_FIELD_MAP["owner_or_trapper"]
        ),
        "appointment_cat_address": fields.get(APPOINTMENT_FIELD_MAP["cat_address"]),
        "appointment_cat_city": fields.get(APPOINTMENT_FIELD_MAP["cat_city"]),
        "cat_owner_or_trapper": cat_display.get(CAT_FIELD_MAP["owner_or_trapper"]),
        "cat_address": cat_fields.get(CAT_FIELD_MAP["address"]),
        "gender": cat_fields.get(CAT_FIELD_MAP["gender"]),
        "microchip_number": normalize_microchip(
            cat_id, cat_fields.get(CAT_FIELD_MAP["microchip_number"])
        ),
        "color": " / ".join(colors) if colors else None,
        # Airtable has no field compatible with treatment-sheet weight.
        "weight": None,
        "age": cat_fields.get(CAT_FIELD_MAP["age"]),
        "ear_tip": cat_fields.get(CAT_FIELD_MAP["ear_tip"]),
        "voucher_numbers": voucher_numbers,
        "vouchers": vouchers,
        "services": _services(fields),
        "total_cost": normalize_cost(
            appointment["id"], fields.get(APPOINTMENT_FIELD_MAP["total_cost"])
        ),
    }


def _vouchers(
    cat_fields: Mapping[str, object], cat_display: Mapping[str, object]
) -> tuple[list[str], list[Record]]:
    """Pair validated voucher labels with IDs while preserving Airtable link order."""
    field = CAT_FIELD_MAP["vouchers"]
    voucher_ids = cat_fields.get(field, [])
    displayed = cat_display.get(field)
    voucher_numbers = [] if displayed is None else [item.strip() for item in displayed.split(",")]
    vouchers = [
        {"airtable_voucher_id": voucher_id, "voucher_number": voucher_number}
        for voucher_id, voucher_number in zip(voucher_ids, voucher_numbers, strict=True)
    ]
    return voucher_numbers, vouchers


def _services(fields: Mapping[str, object]) -> list[str]:
    """Combine service fields into an ordered list whose first occurrence wins."""
    values = [
        value for field in APPOINTMENT_FIELD_MAP["services"] for value in fields.get(field, [])
    ]
    return list(dict.fromkeys(values))
