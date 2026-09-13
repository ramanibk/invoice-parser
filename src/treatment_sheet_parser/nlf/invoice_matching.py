"""Match validated NLF invoice appointments to manifest cats."""

from __future__ import annotations

from dataclasses import replace

from treatment_sheet_parser.nlf.errors import ExtractionError
from treatment_sheet_parser.nlf.manifest import ManifestSheet, contains_name, contains_owner
from treatment_sheet_parser.nlf.models import Appointment, CatRecord, Invoice, InvoiceAppointment
from treatment_sheet_parser.nlf.service_mapping import (
    OddCostDecider,
    ServiceReference,
    UnknownServiceDecider,
    map_services,
)


def merge_invoice(
    records: list[tuple[ManifestSheet, CatRecord]],
    invoice: Invoice,
    reference: ServiceReference,
    unknown_decider: UnknownServiceDecider | None,
    odd_cost_decider: OddCostDecider | None,
) -> list[tuple[ManifestSheet, CatRecord]]:
    """Merge all invoice appointments into uniquely matching treatment visits.

    Invoice rows are consumed at most once. The function returns new immutable
    records and rejects an invoice containing any unmatched visits.

    Raises:
        ExtractionError: If a treatment visit has no match or invoice rows remain.
        ServiceMappingError: If service mapping requires or receives an invalid
            operator decision.
    """
    used: set[int] = set()
    merged = [
        (
            sheet,
            _merge_cat(
                sheet,
                record,
                invoice,
                used,
                reference,
                unknown_decider,
                odd_cost_decider,
            ),
        )
        for sheet, record in records
    ]
    if len(used) != len(invoice.appointments):
        raise ExtractionError("invoice contains appointments not present in the manifest")
    return merged


def _merge_cat(
    sheet: ManifestSheet,
    record: CatRecord,
    invoice: Invoice,
    used: set[int],
    reference: ServiceReference,
    unknown_decider: UnknownServiceDecider | None,
    odd_cost_decider: OddCostDecider | None,
) -> CatRecord:
    """Return one cat record with invoice values merged into every appointment.

    The original frozen record is preserved; only its appointment mapping is
    replaced with newly created appointment values.
    """
    appointments = {
        date: _merge_appointment(
            sheet,
            date,
            appointment,
            invoice,
            used,
            reference,
            unknown_decider,
            odd_cost_decider,
        )
        for date, appointment in record.appointments.items()
    }
    return replace(record, appointments=appointments)


def _merge_appointment(
    sheet: ManifestSheet,
    date: str,
    appointment: Appointment,
    invoice: Invoice,
    used: set[int],
    reference: ServiceReference,
    unknown_decider: UnknownServiceDecider | None,
    odd_cost_decider: OddCostDecider | None,
) -> Appointment:
    """Merge the first unused invoice row matching a treatment appointment.

    Matching requires service date, cat name, and owner identity. The selected
    invoice index is marked used before its services and total replace the parsed
    appointment's empty billing fields.
    """
    matches = [
        (index, item)
        for index, item in enumerate(invoice.appointments)
        if index not in used and _invoice_matches(sheet, date, item)
    ]
    if not matches:
        raise ExtractionError(f"no invoice appointment matches {sheet.cat_name!r} on {date}")
    index, item = matches[0]
    used.add(index)
    services = map_services(item.services, reference, unknown_decider, odd_cost_decider)
    return replace(appointment, services=services, total_cost=item.total_cost)


def _invoice_matches(sheet: ManifestSheet, date: str, item: InvoiceAppointment) -> bool:
    """Return whether an invoice visit matches a manifest identity and date.

    Cat names must appear as contiguous tokens, while owner matching tolerates
    token order and permits the manifest's explicit ``N/A`` owner sentinel.
    """
    return (
        date == item.service_date
        and contains_name(item.identity_text, sheet.cat_name)
        and contains_owner(item.identity_text, sheet.owner_name)
    )
