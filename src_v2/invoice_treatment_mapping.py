"""Match invoice visits to treatment sheets and map their service descriptions."""

from collections.abc import Mapping
from decimal import Decimal

from errors import PipelineError
from models_extraction import AirtableService, ExtractionAppointment, ExtractionCat
from models_invoice import Invoice, InvoiceAppointment
from models_treatment_sheet import TreatmentCat, TreatmentSheetAppointment
from service_catalog import ServiceCatalog, ServiceCatalogEntry, normalize_invoice_service_name
from text_normalization import contains_name, identity_tokens


def match_invoice_to_treatment_sheets(
    invoice: Invoice,
    treatment_cats: tuple[TreatmentCat, ...],
    service_catalog: ServiceCatalog,
) -> tuple[ExtractionCat, ...]:
    """Match each cat's latest treatment visit and preserve earlier history."""
    _validate_inputs(invoice, treatment_cats, service_catalog)
    service_by_invoice_name = _build_service_map(service_catalog)
    # Track positions rather than value equality so even identical-looking
    # invoice visits remain distinct source records that can be consumed once.
    used_invoice_indexes: set[int] = set()
    extraction_cats = tuple(
        _match_treatment_cat(
            treatment_cat,
            invoice,
            used_invoice_indexes,
            service_by_invoice_name,
        )
        for treatment_cat in treatment_cats
    )
    # Latest-visit matching proves each cat has an invoice visit; this reverse
    # count proves no invoice visit was left unmatched.
    if len(used_invoice_indexes) != len(invoice.appointments):
        raise PipelineError("invoice contains appointments not present in treatment sheets")
    return extraction_cats


def _validate_inputs(
    invoice: object,
    treatment_cats: object,
    service_catalog: object,
) -> None:
    """Require the typed, immutable inputs expected by the matching stage."""
    if not isinstance(invoice, Invoice):
        raise PipelineError("invoice mapping requires Invoice")
    if not isinstance(treatment_cats, tuple) or not treatment_cats:
        raise PipelineError("treatment cats must be a non-empty tuple")
    if not all(isinstance(treatment_cat, TreatmentCat) for treatment_cat in treatment_cats):
        raise PipelineError("treatment cats must contain only TreatmentCat values")
    if not isinstance(service_catalog, ServiceCatalog):
        raise PipelineError("invoice mapping requires ServiceCatalog")


def _match_treatment_cat(
    treatment_cat: TreatmentCat,
    invoice: Invoice,
    used_invoice_indexes: set[int],
    service_by_invoice_name: Mapping[str, ServiceCatalogEntry],
) -> ExtractionCat:
    """Match the latest visit and retain earlier visits without billing values."""
    latest_appointment = max(
        treatment_cat.appointments,
        key=lambda appointment: appointment.service_date,
    )
    extraction_appointments = tuple(
        _match_treatment_appointment(
            treatment_cat,
            treatment_appointment,
            invoice,
            used_invoice_indexes,
            service_by_invoice_name,
        )
        if treatment_appointment is latest_appointment
        else ExtractionAppointment(treatment_appointment, (), None)
        for treatment_appointment in treatment_cat.appointments
    )
    return ExtractionCat(treatment_cat, extraction_appointments)


def _match_treatment_appointment(
    treatment_cat: TreatmentCat,
    treatment_appointment: TreatmentSheetAppointment,
    invoice: Invoice,
    used_invoice_indexes: set[int],
    service_by_invoice_name: Mapping[str, ServiceCatalogEntry],
) -> ExtractionAppointment:
    """Require exactly one unused invoice visit for one treatment appointment."""
    invoice_matches = _find_invoice_matches(
        treatment_cat,
        treatment_appointment,
        invoice,
        used_invoice_indexes,
    )
    visit = f"{treatment_cat.cat_name!r} on {treatment_appointment.service_date.isoformat()}"
    if not invoice_matches:
        raise PipelineError(f"no invoice appointment matches {visit}")
    if len(invoice_matches) > 1:
        raise PipelineError(f"multiple invoice appointments match {visit}")
    invoice_index, invoice_appointment = invoice_matches[0]
    services = _map_services(invoice_appointment, service_by_invoice_name)
    # Mark the source visit only after service mapping succeeds, keeping the
    # consumed set consistent if validation raises.
    used_invoice_indexes.add(invoice_index)
    return ExtractionAppointment(
        treatment_appointment,
        services,
        invoice_appointment.total_cost,
    )


def _find_invoice_matches(
    treatment_cat: TreatmentCat,
    treatment_appointment: TreatmentSheetAppointment,
    invoice: Invoice,
    used_invoice_indexes: set[int],
) -> tuple[tuple[int, InvoiceAppointment], ...]:
    """Return every unused invoice visit matching date, cat, and owner identity."""
    return tuple(
        (invoice_index, invoice_appointment)
        for invoice_index, invoice_appointment in enumerate(invoice.appointments)
        if invoice_index not in used_invoice_indexes
        and _identities_match(treatment_cat, treatment_appointment, invoice_appointment)
    )


def _identities_match(
    treatment_cat: TreatmentCat,
    treatment_appointment: TreatmentSheetAppointment,
    invoice_appointment: InvoiceAppointment,
) -> bool:
    """Match a visit by date, contiguous cat-name tokens, and owner tokens."""
    return (
        treatment_appointment.service_date == invoice_appointment.service_date
        and contains_name(invoice_appointment.animal_display_name, treatment_cat.cat_name)
        and _owner_matches(invoice_appointment.owner_name, treatment_cat.owner_name)
    )


def _owner_matches(invoice_owner: str, treatment_owner: str) -> bool:
    """Match owner tokens in any order, allowing the manifest's N/A sentinel."""
    expected = set(identity_tokens(treatment_owner))
    # N/A is an explicit manifest sentinel for unavailable owner evidence, so it
    # removes the owner constraint without weakening date and cat-name matching.
    if expected == {"n", "a"}:
        return True
    return bool(expected) and expected <= set(identity_tokens(invoice_owner))


def _build_service_map(service_catalog: ServiceCatalog) -> dict[str, ServiceCatalogEntry]:
    """Index every recognized invoice description from a validated catalog."""
    # Catalog validation has already rejected normalized description collisions,
    # making each dictionary lookup unambiguous.
    return {
        normalize_invoice_service_name(description): service
        for service in service_catalog.services
        for description in service.recognized_invoice_descriptions
    }


def _map_services(
    invoice_appointment: InvoiceAppointment,
    service_by_invoice_name: Mapping[str, ServiceCatalogEntry],
) -> tuple[AirtableService, ...]:
    """Map and sum one invoice visit's services in their first-seen order."""
    # Dict insertion order preserves the first invoice appearance of each
    # Airtable option while repeated mappings accumulate exact Decimal costs.
    cost_by_airtable_service: dict[str, Decimal] = {}
    for invoice_service in invoice_appointment.services:
        catalog_service = service_by_invoice_name.get(
            normalize_invoice_service_name(invoice_service.name)
        )
        if catalog_service is None:
            raise PipelineError(f"invoice service is not mapped: {invoice_service.name!r}")
        airtable_name = catalog_service.airtable_service_option
        previous_cost = cost_by_airtable_service.get(airtable_name, Decimal())
        cost_by_airtable_service[airtable_name] = previous_cost + invoice_service.cost
    return tuple(
        AirtableService(airtable_name, cost)
        for airtable_name, cost in cost_by_airtable_service.items()
    )
