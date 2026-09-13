"""Orchestrate the read-only Airtable Needs Invoice query."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

from treatment_sheet_parser.needs_invoice.airtable import AirtableClient
from treatment_sheet_parser.needs_invoice.config import (
    APPOINTMENT_DISPLAY_FIELDS,
    APPOINTMENT_FIELDS,
    CAT_DISPLAY_FIELDS,
    CAT_FIELDS,
    CUTOFF_DATE,
    INCOMPLETE_FILTER,
    STATUS_FILTER,
)
from treatment_sheet_parser.needs_invoice.errors import AirtableQueryError
from treatment_sheet_parser.needs_invoice.records import build_cat_payloads
from treatment_sheet_parser.needs_invoice.validation import (
    index_appointments_by_cat,
    validate_appointments,
    validate_cats,
    validate_display_records,
    validate_linked_displays,
)
from treatment_sheet_parser.shared.locations import LOCATION_NAMES


@dataclass(frozen=True)
class NeedsInvoiceResult:
    """Validated Needs Invoice snapshot for one date and clinic.

    Counts describe the source appointments and unique linked cats. ``cats``
    contains the normalized comparison records and preserves Airtable link order.
    """

    date: str
    location_code: str
    location: str
    total_records: int
    linked_cats: int
    cats: tuple[Mapping[str, object], ...]


def query_needs_invoice(
    client: AirtableClient,
    appointment_date: date,
    location_code: str,
    *,
    appointments_table_id: str,
    cats_table_id: str,
) -> NeedsInvoiceResult:
    """Fetch and assemble one comparison-ready Needs Invoice snapshot.

    Typed records establish identities and field values. Separate display-format
    reads supply human-readable linked values. Every response is validated before
    a result is returned.

    Args:
        client: Configured read-only Airtable client.
        appointment_date: Requested service date within the supported window.
        location_code: Supported clinic abbreviation, matched case-insensitively.
        appointments_table_id: Airtable table containing appointments.
        cats_table_id: Airtable table containing linked cat records.

    Returns:
        A complete validated snapshot for JSON serialization.

    Raises:
        AirtableQueryError: If the scope, response, or record relationships are invalid.
    """
    _validate_target_date(appointment_date)
    code, location = resolve_location(location_code)

    appointments = _fetch_appointments(client, appointments_table_id, appointment_date, location)
    validate_appointments(appointments, appointment_date, location)
    appointments_by_cat = index_appointments_by_cat(appointments, appointment_date)
    cat_ids = tuple(appointments_by_cat)

    # Keep related typed and display reads adjacent so the pipeline order is visible.
    appointment_displays = _fetch_display_fields(
        client,
        appointments_table_id,
        tuple(record["id"] for record in appointments),
        APPOINTMENT_DISPLAY_FIELDS,
    )
    cats = _fetch_cats(client, cats_table_id, cat_ids)
    validate_cats(cats)
    cat_displays = _fetch_display_fields(client, cats_table_id, cat_ids, CAT_DISPLAY_FIELDS)
    validate_linked_displays(appointments, cats, appointment_displays, cat_displays)

    # Build nothing until the full source set and all cross-record links are valid.
    payloads = build_cat_payloads(
        cat_ids,
        cats,
        appointments_by_cat,
        appointment_displays,
        cat_displays,
    )
    return NeedsInvoiceResult(
        appointment_date.isoformat(), code, location, len(appointments), len(cat_ids), payloads
    )


def result_document(result: NeedsInvoiceResult) -> dict[str, object]:
    """Convert a result to its stable JSON-compatible document structure.

    The cat tuple becomes a list because JSON has no tuple representation.
    """
    return {
        "date": result.date,
        "location_code": result.location_code,
        "location": result.location,
        "total_records": result.total_records,
        "linked_cats": result.linked_cats,
        "cats": list(result.cats),
    }


def resolve_location(location_code: str) -> tuple[str, str]:
    """Resolve a clinic abbreviation to its canonical code and Airtable value.

    Whitespace and case are ignored. Unknown codes fail with the supported list
    instead of being guessed.
    """
    code = location_code.strip().upper()
    try:
        return code, LOCATION_NAMES[code]
    except KeyError as exc:
        supported = ", ".join(sorted(LOCATION_NAMES))
        raise AirtableQueryError(
            f"unknown location code {location_code!r}; supported: {supported}"
        ) from exc


def needs_invoice_formula(appointment_date: date, location: str) -> str:
    """Build the Airtable formula for relevant incomplete appointments.

    The caller supplies an already validated date and a canonical location name.
    """
    target_date = appointment_date.isoformat()
    return (
        "AND("
        f"IS_SAME({{Date}},DATETIME_PARSE('{target_date}'),'day'),"
        f"{{Location}}='{location}',"
        f"{STATUS_FILTER},{INCOMPLETE_FILTER}"
        ")"
    )


def _fetch_appointments(
    client: AirtableClient,
    table_id: str,
    appointment_date: date,
    location: str,
) -> tuple[Mapping[str, object], ...]:
    """Fetch typed appointments selected by the Version 1 Needs Invoice formula."""
    return client.list_records(
        table_id,
        fields=APPOINTMENT_FIELDS,
        formula=needs_invoice_formula(appointment_date, location),
    )


def _fetch_cats(
    client: AirtableClient, table_id: str, cat_ids: tuple[str, ...]
) -> tuple[Mapping[str, object], ...]:
    """Fetch typed Cat-table records for exactly the requested linked identities."""
    return client.records_by_ids(table_id, cat_ids, fields=CAT_FIELDS)


def _fetch_display_fields(
    client: AirtableClient,
    table_id: str,
    record_ids: tuple[str, ...],
    fields: tuple[str, ...],
) -> dict[str, Mapping[str, object]]:
    """Fetch linked-record labels and index them by their source record IDs.

    Airtable's string cell format supplies display labels rather than typed link
    IDs. Identity completeness remains enforced by ``records_by_ids``.
    """
    records = client.records_by_ids(table_id, record_ids, fields=fields, cell_format="string")
    return validate_display_records(records, fields)


def _validate_target_date(appointment_date: date) -> None:
    """Require a date in the interval where Airtable reporting is supported.

    Historical dates through ``CUTOFF_DATE`` and future dates are rejected.
    """
    if appointment_date <= CUTOFF_DATE or appointment_date > date.today():
        raise AirtableQueryError(
            f"appointment date must be after {CUTOFF_DATE.isoformat()} and no later than today"
        )
