"""Tests for interactive unknown-service choices and option validation."""

import json

import pytest

from treatment_sheet_parser.nlf.cli import DEFAULT_SERVICE_OPTIONS
from treatment_sheet_parser.nlf.extraction import DEFAULT_SERVICE_MAP
from treatment_sheet_parser.nlf.models import Service
from treatment_sheet_parser.nlf.service_mapping import ServiceMappingError, load_service_reference
from treatment_sheet_parser.nlf.service_review import (
    InteractiveServiceDecider,
    load_airtable_service_options,
)


def _options(tmp_path):
    """Write a compact valid Airtable options reference."""
    path = tmp_path / "options.json"
    path.write_text(
        json.dumps(
            {
                "Cat.Services": ["Rabies", "FVRCP"],
                "Cat.Additional Services": ["Exam", "Nail Trim"],
            }
        ),
        encoding="utf-8",
    )
    return load_airtable_service_options(path)


def test_report_and_ignore_is_explicit_and_retains_no_map(tmp_path, monkeypatch, capsys) -> None:
    """Report an ignored line without returning a reference mapping."""
    answers = iter(["i"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    decision = InteractiveServiceDecider(_options(tmp_path))(
        Service("Paper record", "25.00"),
        None,
    )

    assert decision.action == "ignore"
    assert "cost remains in invoice total checks" in capsys.readouterr().out


@pytest.mark.parametrize("selection", ["3", "exam"])
def test_map_accepts_number_or_name_and_requires_confirmation(
    tmp_path, monkeypatch, selection
) -> None:
    """Resolve a schema option by number or name after explicit confirmation."""
    answers = iter(["m", selection, "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    decision = InteractiveServiceDecider(_options(tmp_path))(
        Service("Office visit", "25.00"),
        None,
    )

    assert decision.action == "map_existing"
    assert decision.airtable_value == "Exam"
    assert decision.airtable_field == "Additional Services"


def test_declined_confirmation_aborts(tmp_path, monkeypatch) -> None:
    """Return an abort decision when the proposed mapping is rejected."""
    answers = iter(["m", "Rabies", "n"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(answers))

    decision = InteractiveServiceDecider(_options(tmp_path))(
        Service("Rabies special", "10.00"),
        None,
    )

    assert decision.action == "abort"


def test_malformed_options_reference_is_rejected(tmp_path) -> None:
    """Reject a document missing one required Airtable service field."""
    path = tmp_path / "options.json"
    path.write_text('{"Cat.Services": ["Rabies"]}', encoding="utf-8")

    with pytest.raises(ServiceMappingError, match="must contain exactly"):
        load_airtable_service_options(path)


def test_default_map_uses_only_options_in_default_schema_reference() -> None:
    """Keep every shipped canonical mapping aligned with its schema field."""
    options = load_airtable_service_options(DEFAULT_SERVICE_OPTIONS)
    reference = load_service_reference(DEFAULT_SERVICE_MAP)
    option_fields = {choice.value: choice.field for choice in options.choices}

    mapped_fields = {value: entry["airtable_field"] for value, entry in reference.services.items()}

    assert mapped_fields.items() <= option_fields.items()
