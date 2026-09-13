"""Map clinic invoice descriptions to canonical Airtable service values."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Literal, Protocol, cast

from treatment_sheet_parser.nlf.models import Service

AIRTABLE_FIELDS = ("Services", "Additional Services")


class ServiceMappingError(ValueError):
    """Raised when the reference or a service-mapping decision is invalid."""


@dataclass(frozen=True)
class ServiceDecision:
    """Operator decision for an invoice description absent from the reference.

    Attributes:
        action: Whether to map to a schema option, report and ignore, or abort.
        airtable_value: Existing Airtable schema option selected for a map.
        airtable_field: Airtable field owning the selected option.
    """

    action: Literal["map_existing", "ignore", "abort"]
    airtable_value: str | None = None
    airtable_field: str | None = None


class UnknownServiceDecider(Protocol):
    """Choose how one previously unseen invoice description is handled."""

    def __call__(self, service: Service, reference: ServiceReference) -> ServiceDecision:
        """Return an explicit mapping, ignore, or abort decision for ``service``.

        ``reference`` exposes existing canonical values and may be used to limit
        the decision to valid schema options.
        """
        ...


class OddCostDecider(Protocol):
    """Choose whether a non-numeric service cost errors or is skipped."""

    def __call__(self, service: Service) -> Literal["error", "skip"]:
        """Return whether a non-numeric ``service`` cost should error or be skipped.

        Skipping excludes the service from mapped costs but does not alter the
        invoice parser's independent total validation.
        """
        ...


@dataclass
class ServiceReference:
    """Validated, editable service reference loaded from JSON.

    Attributes:
        path: Resolved path of the reference JSON file.
        document: Complete validated JSON object retained for persistence.
        aliases: Normalized invoice descriptions keyed to canonical values.
        changed: Whether an operator decision modified the reference.
    """

    path: Path
    document: dict[str, object]
    aliases: dict[str, str]
    changed: bool = False

    @property
    def services(self) -> dict[str, dict[str, object]]:
        """Return the validated canonical service entries from the JSON document.

        The returned mapping is live and intentionally mutable so approved alias
        additions can be persisted with the rest of the original document.
        """
        return cast(dict[str, dict[str, object]], self.document["services"])

    def add_alias(self, airtable_value: str, invoice_value: str) -> None:
        """Add an invoice spelling to an existing canonical service.

        Both the persisted document and normalized lookup are updated, and the
        reference is marked changed. Inputs are expected to be validated by the
        mapping decision path.
        """
        entry = self.services[airtable_value]
        values = entry["invoice_values"]
        assert isinstance(values, list)
        values.append(invoice_value)
        self.aliases[_normalized(invoice_value)] = airtable_value
        self.changed = True

    def add_service(self, airtable_value: str, airtable_field: str, invoice_value: str) -> None:
        """Add a schema option that is not yet represented in the reference.

        The new entry records its owning Airtable field, first invoice alias, and
        the fact that the option already exists in Airtable. The reference is
        marked changed for later persistence.
        """
        self.services[airtable_value] = {
            "airtable_field": airtable_field,
            "airtable_option_exists": True,
            "invoice_values": [invoice_value],
        }
        self.aliases[_normalized(invoice_value)] = airtable_value
        self.changed = True


def load_service_reference(path: str | Path) -> ServiceReference:
    """Load and fully validate a service-mapping reference.

    Args:
        path: JSON reference containing canonical Airtable values and aliases.

    Returns:
        A validated reference with a normalized alias lookup.

    Raises:
        ServiceMappingError: If the file is unreadable, malformed, or contains
            invalid or conflicting mappings.
    """
    reference_path = Path(path).expanduser().resolve()
    try:
        document = json.loads(reference_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        message = f"could not read service reference {reference_path}: {exc}"
        raise ServiceMappingError(message) from exc
    _validate_document(document)
    return ServiceReference(reference_path, document, _aliases(document["services"]))


def map_services(
    services: tuple[Service, ...],
    reference: ServiceReference,
    unknown_decider: UnknownServiceDecider | None = None,
    odd_cost_decider: OddCostDecider | None = None,
) -> dict[str, float]:
    """Map invoice services to canonical values and sum their numeric costs.

    Unknown descriptions and non-numeric costs require explicit callbacks;
    library use never prompts implicitly.

    Args:
        services: Raw services parsed from one invoice appointment.
        reference: Validated canonical values and invoice aliases.
        unknown_decider: Optional operator callback for an unknown description.
        odd_cost_decider: Optional operator callback for a non-numeric cost.

    Returns:
        Canonical Airtable values keyed to summed JSON-number costs.

    Raises:
        ServiceMappingError: If a required decision is absent or invalid.
    """
    mapped: dict[str, Decimal] = {}
    for service in services:
        target = _target_for(service, reference, unknown_decider)
        if target is None:
            continue
        cost = _numeric_cost(service, odd_cost_decider)
        if cost is not None:
            mapped[target] = mapped.get(target, Decimal()) + cost
    return {key: float(value) for key, value in mapped.items()}


def write_service_reference(reference: ServiceReference) -> None:
    """Atomically save operator-approved additions to the reference.

    The function does nothing when the reference is unchanged and never
    contacts or mutates Airtable.

    Raises:
        ServiceMappingError: If the updated reference cannot be persisted.
    """
    if not reference.changed:
        return
    temporary = reference.path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(reference.document, indent=2) + "\n", encoding="utf-8")
        temporary.replace(reference.path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        message = f"could not write service reference {reference.path}: {exc}"
        raise ServiceMappingError(message) from exc


def _target_for(
    service: Service,
    reference: ServiceReference,
    decider: UnknownServiceDecider | None,
) -> str | None:
    """Resolve one invoice description to a canonical Airtable value.

    Known aliases are returned without prompting. Unknown values require a
    decider; its result is validated and applied to the in-memory reference.

    Returns:
        Canonical value, or ``None`` when the operator explicitly ignores it.
    """
    target = reference.aliases.get(_normalized(service.name))
    if target is not None:
        return target
    if decider is None:
        raise ServiceMappingError(f"invoice service is not mapped: {service.name!r}")
    return _apply_decision(service, reference, decider(service, reference))


def _apply_decision(
    service: Service, reference: ServiceReference, decision: ServiceDecision
) -> str | None:
    """Validate and apply one decision for an unknown invoice service.

    Ignore leaves the reference unchanged, map delegates schema validation and
    alias creation, and abort raises ``ServiceMappingError``. Unknown action
    values are rejected defensively even though the dataclass is typed narrowly.
    """
    if decision.action == "ignore":
        return None
    if decision.action == "map_existing":
        return _map_existing(
            service,
            reference,
            decision.airtable_value,
            decision.airtable_field,
        )
    if decision.action == "abort":
        raise ServiceMappingError(f"service mapping aborted for {service.name!r}")
    raise ServiceMappingError(f"invalid service decision: {decision.action!r}")


def _map_existing(
    service: Service,
    reference: ServiceReference,
    airtable_value: str | None,
    airtable_field: str | None,
) -> str:
    """Validate a selected Airtable option and record its invoice alias.

    Existing reference entries must belong to the selected field. A schema
    option absent from the reference is added as a new canonical entry.

    Returns:
        The canonical Airtable value used as the mapped-cost key.
    """
    if not airtable_value:
        raise ServiceMappingError("a service map requires a non-empty Airtable value")
    if airtable_field not in AIRTABLE_FIELDS:
        raise ServiceMappingError(f"invalid Airtable service field: {airtable_field!r}")
    existing = reference.services.get(airtable_value)
    if existing is None:
        reference.add_service(airtable_value, airtable_field, service.name)
    else:
        if existing["airtable_field"] != airtable_field:
            raise ServiceMappingError(f"Airtable field mismatch for {airtable_value!r}")
        reference.add_alias(airtable_value, service.name)
    return airtable_value


def _numeric_cost(service: Service, decider: OddCostDecider | None) -> Decimal | None:
    """Parse one service cost or honor an explicit skip decision.

    Comma separators are accepted. A malformed cost returns ``None`` only when a
    supplied decider explicitly selects ``skip``; every other path raises.
    """
    try:
        return Decimal(service.cost.replace(",", ""))
    except InvalidOperation as exc:
        if decider is not None and decider(service) == "skip":
            return None
        raise ServiceMappingError(
            f"invoice service {service.name!r} has non-numeric cost {service.cost!r}"
        ) from exc


def _validate_document(document: object) -> None:
    """Validate a complete service-reference document and its alias uniqueness.

    Format version ``1.0`` and a non-empty services object are required before
    each entry is checked and the normalized alias index is constructed.
    """
    if not isinstance(document, dict) or document.get("format_version") != "1.0":
        raise ServiceMappingError("service reference format_version must be '1.0'")
    services = document.get("services")
    if not isinstance(services, dict) or not services:
        raise ServiceMappingError("service reference services must be a non-empty object")
    for value, entry in services.items():
        _validate_entry(value, entry)
    _aliases(services)


def _validate_entry(value: object, entry: object) -> None:
    """Validate one canonical service key and its mapping metadata.

    The owning field, option-existence flag, and invoice alias collection must
    all be present with their exact expected types.
    """
    if not isinstance(value, str) or not value.strip() or not isinstance(entry, dict):
        raise ServiceMappingError("each service must have a non-empty key and object value")
    if entry.get("airtable_field") not in AIRTABLE_FIELDS:
        raise ServiceMappingError(f"invalid Airtable field for service {value!r}")
    if not isinstance(entry.get("airtable_option_exists"), bool):
        raise ServiceMappingError(f"airtable_option_exists must be boolean for {value!r}")
    invoice_values = entry.get("invoice_values")
    _validate_invoice_values(value, invoice_values)


def _validate_invoice_values(value: str, invoice_values: object) -> None:
    """Validate the invoice aliases belonging to one canonical value.

    At least one alias is required and every item must be a non-blank string.
    Cross-entry uniqueness is checked separately by ``_aliases``.
    """
    if not isinstance(invoice_values, list) or not invoice_values:
        raise ServiceMappingError(f"invoice_values must be a non-empty array for {value!r}")
    if any(not isinstance(item, str) or not item.strip() for item in invoice_values):
        raise ServiceMappingError(f"invoice_values must contain non-empty strings for {value!r}")


def _aliases(services: object) -> dict[str, str]:
    """Build a normalized alias-to-canonical-value lookup.

    Canonical names also act as aliases. If normalization causes one spelling to
    target different canonical services, the reference is rejected as ambiguous.
    """
    assert isinstance(services, dict)
    aliases: dict[str, str] = {}
    for target, entry in services.items():
        assert isinstance(target, str) and isinstance(entry, dict)
        for invoice_value in [target, *entry["invoice_values"]]:
            normalized = _normalized(invoice_value)
            if normalized in aliases and aliases[normalized] != target:
                raise ServiceMappingError(f"duplicate invoice service mapping: {invoice_value!r}")
            aliases[normalized] = target
    return aliases


def _normalized(value: str) -> str:
    """Normalize case and whitespace for invoice-service comparisons.

    Internal whitespace runs collapse to one space after Unicode-aware
    case-folding; punctuation remains significant.
    """
    return " ".join(value.casefold().split())
