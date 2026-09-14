"""Load and validate Airtable services and recognized invoice descriptions."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from errors import PipelineError
from models_validation import _require_text, _require_tuple_of

FORMAT_VERSION = "1.0"
# Only these Airtable multi-select fields may receive mapped invoice services.
AIRTABLE_FIELD_NAMES = frozenset({"Services", "Additional Services"})
CATALOG_KEYS = frozenset({"format_version", "services"})
SERVICE_KEYS = frozenset(
    {"airtable_field_name", "airtable_service_option", "recognized_invoice_descriptions"}
)


@dataclass(frozen=True)
class ServiceCatalogEntry:
    """Describe one Airtable service option and its recognized invoice text."""

    airtable_field_name: str
    airtable_service_option: str
    recognized_invoice_descriptions: tuple[str, ...]

    def __post_init__(self) -> None:
        """Validate the Airtable identity and every recognized invoice description."""
        _require_text(self.airtable_field_name, "Airtable field name")
        if self.airtable_field_name not in AIRTABLE_FIELD_NAMES:
            raise PipelineError(f"invalid Airtable service field: {self.airtable_field_name!r}")
        _require_text(self.airtable_service_option, "Airtable service option")
        descriptions = _require_tuple_of(
            self.recognized_invoice_descriptions,
            str,
            "recognized invoice descriptions",
        )
        for invoice_description in descriptions:
            _require_text(invoice_description, "recognized invoice description")


@dataclass(frozen=True)
class ServiceCatalog:
    """Store every validated service available to the pipeline."""

    services: tuple[ServiceCatalogEntry, ...]

    def __post_init__(self) -> None:
        """Require a non-empty catalog with unique Airtable and invoice identities."""
        if not self.services:
            raise PipelineError("service catalog services must be a non-empty tuple")
        services = _require_tuple_of(self.services, ServiceCatalogEntry, "service catalog services")
        # Validate both output coverage and lookup uniqueness at load time so the
        # matching stage can perform direct, deterministic mappings.
        _validate_airtable_field_coverage(services)
        _validate_unique_airtable_services(services)
        _validate_unique_invoice_descriptions(services)


def load_service_catalog(catalog_path: Path) -> ServiceCatalog:
    """Load and completely validate the service catalog without modifying it.

    Args:
        catalog_path: Absolute path to the UTF-8 JSON service catalog.

    Returns:
        Airtable service options paired with their recognized invoice descriptions.

    Raises:
        PipelineError: If the file or any catalog entry is invalid.
    """
    catalog_json = _read_catalog_json(catalog_path)
    _validate_catalog_header(catalog_json)
    services_json = catalog_json["services"]
    if not isinstance(services_json, list) or not services_json:
        raise PipelineError("service catalog services must be a non-empty array")
    services = tuple(
        _parse_service_entry(service_json, position)
        for position, service_json in enumerate(services_json, start=1)
    )
    return ServiceCatalog(services)


def normalize_invoice_service_name(value: str) -> str:
    """Normalize insignificant case and whitespace in an invoice service name."""
    # Punctuation remains meaningful; only formatting differences known to arise
    # from PDF text extraction are ignored.
    return " ".join(value.casefold().split())


def _read_catalog_json(catalog_path: Path) -> dict[str, Any]:
    """Read the catalog and require its root JSON value to be an object."""
    if not isinstance(catalog_path, Path) or not catalog_path.is_absolute():
        raise PipelineError("service catalog path must be an absolute Path")
    try:
        loaded_json = json.loads(catalog_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"could not read service catalog {catalog_path}: {exc}") from exc
    if not isinstance(loaded_json, dict):
        raise PipelineError("service catalog must contain a JSON object")
    return loaded_json


def _validate_catalog_header(catalog_json: dict[str, Any]) -> None:
    """Require the exact catalog fields and supported format version."""
    if set(catalog_json) != CATALOG_KEYS:
        raise PipelineError("service catalog must contain exactly: format_version, services")
    if catalog_json["format_version"] != FORMAT_VERSION:
        raise PipelineError(f"service catalog format_version must be {FORMAT_VERSION!r}")


def _parse_service_entry(service_json: object, position: int) -> ServiceCatalogEntry:
    """Parse one self-contained service record at its one-based JSON position."""
    location = f"service catalog services[{position}]"
    if not isinstance(service_json, dict) or set(service_json) != SERVICE_KEYS:
        raise PipelineError(f"{location} must contain exactly the required service fields")
    descriptions_json = service_json["recognized_invoice_descriptions"]
    if not isinstance(descriptions_json, list):
        raise PipelineError(f"{location}.recognized_invoice_descriptions must be an array")
    return ServiceCatalogEntry(
        airtable_field_name=service_json["airtable_field_name"],
        airtable_service_option=service_json["airtable_service_option"],
        recognized_invoice_descriptions=tuple(descriptions_json),
    )


def _validate_unique_airtable_services(services: tuple[ServiceCatalogEntry, ...]) -> None:
    """Require every field and option pair to identify one catalog service."""
    # The Airtable field is part of the identity because equal option labels may
    # legitimately exist in different multi-select fields.
    identities = [
        (service.airtable_field_name.casefold(), service.airtable_service_option.casefold())
        for service in services
    ]
    if len(identities) != len(set(identities)):
        raise PipelineError("service catalog Airtable field and option pairs must be unique")


def _validate_airtable_field_coverage(services: tuple[ServiceCatalogEntry, ...]) -> None:
    """Require the catalog to represent every supported Airtable service field."""
    represented_fields = {service.airtable_field_name for service in services}
    if represented_fields != AIRTABLE_FIELD_NAMES:
        names = ", ".join(sorted(AIRTABLE_FIELD_NAMES))
        raise PipelineError(
            f"service catalog must represent exactly these Airtable fields: {names}"
        )


def _validate_unique_invoice_descriptions(services: tuple[ServiceCatalogEntry, ...]) -> None:
    """Require each normalized invoice description to identify one catalog service."""
    # Check normalized text rather than literal JSON strings because matching
    # applies the same normalization to extracted invoice descriptions.
    normalized_descriptions = [
        normalize_invoice_service_name(description)
        for service in services
        for description in service.recognized_invoice_descriptions
    ]
    if len(normalized_descriptions) != len(set(normalized_descriptions)):
        raise PipelineError("recognized invoice descriptions must be unique")
