"""Tests for loading and validating the flat service catalog."""

import json
from pathlib import Path

import pytest
from errors import PipelineError
from pipeline_config import DEFAULT_SERVICE_CATALOG_PATH
from service_catalog import ServiceCatalog, ServiceCatalogEntry, load_service_catalog


def _service(**changes: object) -> dict[str, object]:
    """Build one valid service catalog JSON record with selected overrides."""
    service = {
        "airtable_field_name": "Services",
        "airtable_service_option": "Spay / Neuter",
        "recognized_invoice_descriptions": ["Cat Spay", "Cat Neuter"],
    }
    service.update(changes)
    return service


def _valid_catalog() -> dict[str, object]:
    """Return a minimal catalog containing both supported Airtable fields."""
    return {
        "format_version": "1.0",
        "services": [
            _service(),
            _service(
                airtable_field_name="Additional Services",
                airtable_service_option="Pregnant",
                recognized_invoice_descriptions=["Pregnant Surcharge w/LRS Administered"],
            ),
        ],
    }


def _load(tmp_path: Path, catalog_json: object) -> ServiceCatalog:
    """Write and load one selected service catalog JSON value."""
    catalog_path = tmp_path / "service_catalog.json"
    catalog_path.write_text(json.dumps(catalog_json), encoding="utf-8")
    return load_service_catalog(catalog_path)


def test_loads_bundled_service_catalog() -> None:
    """Validate every service shipped with the rewritten pipeline."""
    service_catalog = load_service_catalog(DEFAULT_SERVICE_CATALOG_PATH)

    assert len(service_catalog.services) == 67
    assert service_catalog.services[0] == ServiceCatalogEntry(
        airtable_field_name="Services",
        airtable_service_option="Spay / Neuter",
        recognized_invoice_descriptions=("Cat Neuter", "Cat Spay"),
    )


def test_loads_empty_descriptions_for_unused_airtable_option(tmp_path: Path) -> None:
    """Allow an Airtable option that has not appeared on an invoice yet."""
    catalog_json = _valid_catalog()
    services_json = catalog_json["services"]
    assert isinstance(services_json, list)
    services_json.append(
        _service(
            airtable_service_option="DAPP",
            recognized_invoice_descriptions=[],
        )
    )

    service_catalog = _load(tmp_path, catalog_json)

    assert service_catalog.services[-1].recognized_invoice_descriptions == ()


@pytest.mark.parametrize(
    ("catalog_json", "message"),
    [
        ([], "service catalog must contain a JSON object"),
        ({"format_version": "1.0"}, "service catalog must contain exactly"),
        (
            {"format_version": "2.0", "services": []},
            "service catalog format_version must be '1.0'",
        ),
        (
            {"format_version": "1.0", "services": []},
            "service catalog services must be a non-empty array",
        ),
    ],
)
def test_rejects_malformed_catalog_root(
    tmp_path: Path,
    catalog_json: object,
    message: str,
) -> None:
    """Reject non-object roots, missing fields, versions, and empty service arrays."""
    with pytest.raises(PipelineError, match=message):
        _load(tmp_path, catalog_json)


@pytest.mark.parametrize(
    ("service_json", "message"),
    [
        ({}, "must contain exactly the required service fields"),
        (_service(airtable_field_name="Unknown"), "invalid Airtable service field"),
        (_service(airtable_service_option=""), "Airtable service option must be non-empty"),
        (
            _service(recognized_invoice_descriptions="Cat Spay"),
            "recognized_invoice_descriptions must be an array",
        ),
        (
            _service(recognized_invoice_descriptions=[""]),
            "recognized invoice description must be non-empty",
        ),
    ],
)
def test_rejects_malformed_service_record(
    tmp_path: Path,
    service_json: object,
    message: str,
) -> None:
    """Reject incomplete records and invalid Airtable or invoice values."""
    catalog_json = {"format_version": "1.0", "services": [service_json]}

    with pytest.raises(PipelineError, match=message):
        _load(tmp_path, catalog_json)


def test_rejects_duplicate_airtable_service_identity(tmp_path: Path) -> None:
    """Reject repeated Airtable field and option pairs under case folding."""
    catalog_json = _valid_catalog()
    services_json = catalog_json["services"]
    assert isinstance(services_json, list)
    services_json.append(
        _service(
            airtable_service_option="SPAY / NEUTER",
            recognized_invoice_descriptions=[],
        )
    )

    with pytest.raises(PipelineError, match="field and option pairs must be unique"):
        _load(tmp_path, catalog_json)


def test_rejects_missing_airtable_field(tmp_path: Path) -> None:
    """Require the catalog to represent both supported Airtable service fields."""
    catalog_json = {"format_version": "1.0", "services": [_service()]}

    with pytest.raises(PipelineError, match="must represent exactly these Airtable fields"):
        _load(tmp_path, catalog_json)


def test_rejects_duplicate_recognized_invoice_description(tmp_path: Path) -> None:
    """Reject normalized invoice descriptions repeated under different services."""
    catalog_json = _valid_catalog()
    services_json = catalog_json["services"]
    assert isinstance(services_json, list)
    services_json.append(
        _service(
            airtable_service_option="Microchip",
            recognized_invoice_descriptions=["  CAT   SPAY "],
        )
    )

    with pytest.raises(PipelineError, match="recognized invoice descriptions must be unique"):
        _load(tmp_path, catalog_json)
