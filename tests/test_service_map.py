"""Tests for service-reference validation, mapping decisions, and persistence."""

import json
from pathlib import Path

import pytest

from treatment_sheet_parser.nlf.models import Service
from treatment_sheet_parser.nlf.service_mapping import (
    ServiceDecision,
    ServiceMappingError,
    load_service_reference,
    map_services,
    write_service_reference,
)


def _reference(tmp_path: Path) -> Path:
    """Write a valid service reference for a mapping test."""
    path = tmp_path / "services.json"
    path.write_text(
        json.dumps(
            {
                "format_version": "1.0",
                "services": {
                    "Rabies": {
                        "airtable_field": "Services",
                        "airtable_option_exists": True,
                        "invoice_values": ["Rabies 1 year"],
                    },
                    "Exam": {
                        "airtable_field": "Additional Services",
                        "airtable_option_exists": True,
                        "invoice_values": ["Office visit"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_known_aliases_become_canonical_numeric_costs(tmp_path) -> None:
    """Map known aliases to canonical services and sum their numeric costs."""
    reference = load_service_reference(_reference(tmp_path))

    result = map_services(
        (
            Service("Rabies 1 year", "10.00"),
            Service("rabies 1 YEAR", "5.00"),
            Service("Exam", "0.00"),
        ),
        reference,
    )

    assert result == {"Rabies": 15.0, "Exam": 0.0}


def test_unknown_can_map_to_existing_and_updates_reference(tmp_path) -> None:
    """Persist an approved unknown spelling as an alias of an existing service."""
    path = _reference(tmp_path)
    reference = load_service_reference(path)

    result = map_services(
        (Service("Rabies vaccine special", "12.00"),),
        reference,
        lambda _service, _reference: ServiceDecision("map_existing", "Rabies", "Services"),
    )
    write_service_reference(reference)

    assert result == {"Rabies": 12.0}
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["services"]["Rabies"]["invoice_values"][-1] == "Rabies vaccine special"


def test_ignored_unknown_is_absent_but_does_not_change_reference(tmp_path) -> None:
    """Omit an ignored service without changing its reference file."""
    path = _reference(tmp_path)
    original = path.read_text(encoding="utf-8")
    reference = load_service_reference(path)

    result = map_services(
        (Service("Paper record", "25.00"),),
        reference,
        lambda _service, _reference: ServiceDecision("ignore"),
    )
    write_service_reference(reference)

    assert result == {}
    assert path.read_text(encoding="utf-8") == original


def test_schema_option_absent_from_map_can_be_added(tmp_path) -> None:
    """Add a confirmed Airtable option that is absent from the mapping reference."""
    path = _reference(tmp_path)
    reference = load_service_reference(path)

    result = map_services(
        (Service("Laser therapy", "30.00"),),
        reference,
        lambda _service, _reference: ServiceDecision(
            "map_existing", "Laser Therapy", "Additional Services"
        ),
    )
    write_service_reference(reference)

    assert result == {"Laser Therapy": 30.0}
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["services"]["Laser Therapy"]["airtable_option_exists"] is True


def test_aborted_map_does_not_change_reference(tmp_path) -> None:
    """Abort extraction without persisting a mapping when confirmation is declined."""
    path = _reference(tmp_path)
    original = path.read_text(encoding="utf-8")
    reference = load_service_reference(path)

    with pytest.raises(ServiceMappingError, match="mapping aborted"):
        map_services(
            (Service("Laser therapy", "30.00"),),
            reference,
            lambda _service, _reference: ServiceDecision("abort"),
        )
    write_service_reference(reference)

    assert path.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(("decision", "expected"), [("skip", {}), ("error", None)])
def test_non_numeric_cost_can_skip_or_error(tmp_path, decision, expected) -> None:
    """Apply the selected skip-or-error policy to a non-numeric cost."""
    reference = load_service_reference(_reference(tmp_path))
    service = Service("Rabies", "N/C")

    if decision == "error":
        with pytest.raises(ServiceMappingError, match="non-numeric cost"):
            map_services((service,), reference, odd_cost_decider=lambda _service: "error")
    else:
        assert (
            map_services((service,), reference, odd_cost_decider=lambda _service: "skip")
            == expected
        )


def test_malformed_reference_is_rejected(tmp_path) -> None:
    """Reject a reference that lacks a non-empty services mapping."""
    path = _reference(tmp_path)
    path.write_text('{"format_version": "1.0", "services": {}}', encoding="utf-8")

    with pytest.raises(ServiceMappingError, match="non-empty object"):
        load_service_reference(path)
