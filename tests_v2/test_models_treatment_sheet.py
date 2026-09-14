"""Tests for validated treatment-sheet data models."""

from datetime import date, datetime

import pytest
from errors import PipelineError
from models_treatment_sheet import (
    ManifestEntry,
    MedicalFindings,
    TreatmentCat,
    TreatmentSheetAppointment,
)


def _appointment(**changes: object) -> TreatmentSheetAppointment:
    """Build a valid appointment with selected test overrides."""
    values = {
        "service_date": date(2026, 8, 24),
        "gender": "Female",
        "microchip_number": None,
        "color": "Black",
        "weight": "7.70 lbs",
        "medical_findings": MedicalFindings(exam="Healthy"),
    }
    values.update(changes)
    return TreatmentSheetAppointment(**values)  # type: ignore[arg-type]


def test_constructs_manifest_entry_without_changing_identity_text() -> None:
    """Preserve validated manifest identities and the declared PDF name."""
    entry = ManifestEntry("Alexa Camorlinga", "Nebula", "Nebula.pdf")

    assert entry.owner_name == "Alexa Camorlinga"
    assert entry.cat_name == "Nebula"
    assert entry.filename == "Nebula.pdf"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (("", "Nebula", "Nebula.pdf"), "owner name must be non-empty"),
        (("Alexa", " ", "Nebula.pdf"), "cat name must be non-empty"),
        (("Alexa", "Nebula", "sheets/Nebula.pdf"), "bare PDF filename"),
        (("Alexa", "Nebula", "Nebula.txt"), "bare PDF filename"),
    ],
)
def test_rejects_malformed_manifest_entry(values: tuple[str, str, str], message: str) -> None:
    """Reject missing manifest identities and unsafe or non-PDF filenames."""
    with pytest.raises(PipelineError, match=message):
        ManifestEntry(*values)


def test_preserves_exact_medical_findings_text() -> None:
    """Keep line breaks and surrounding whitespace in extracted medical text."""
    findings = MedicalFindings(exam="  Finding one\nFinding two  ")

    assert findings.exam == "  Finding one\nFinding two  "


def test_rejects_non_text_medical_finding() -> None:
    """Reject a medical field that cannot be serialized as source text."""
    with pytest.raises(PipelineError, match="exam must be a string"):
        MedicalFindings(exam=None)  # type: ignore[arg-type]


def test_constructs_treatment_sheet_appointment() -> None:
    """Retain validated appointment characteristics and findings."""
    appointment = _appointment(microchip_number="123456789012345")

    assert appointment.service_date == date(2026, 8, 24)
    assert appointment.microchip_number == "123456789012345"
    assert appointment.medical_findings.exam == "Healthy"


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"service_date": "2026-08-24"}, "service date must be a date"),
        ({"service_date": datetime(2026, 8, 24)}, "service date must be a date"),
        ({"gender": "Other"}, "gender must be one of"),
        ({"gender": []}, "gender must be a string"),
        ({"microchip_number": "already chipped"}, "microchip must contain"),
        ({"color": 12}, "color must be a string"),
        ({"weight": ""}, "weight must be non-empty"),
        ({"medical_findings": {}}, "medical findings must be MedicalFindings"),
    ],
)
def test_rejects_malformed_appointment(changes: dict[str, object], message: str) -> None:
    """Reject malformed dates, characteristics, and nested findings."""
    with pytest.raises(PipelineError, match=message):
        _appointment(**changes)


def test_constructs_cat_record_with_ordered_appointments() -> None:
    """Retain manifest identity and source appointment order."""
    appointments = (
        _appointment(service_date=date(2026, 8, 6)),
        _appointment(service_date=date(2026, 8, 17)),
    )

    record = TreatmentCat(
        cat_id="26AUG24-NLF-1",
        display_name="(F) Nebula Delgado",
        cat_name="Nebula",
        owner_name="Alexa Camorlinga",
        appointments=appointments,
    )

    assert record.appointments == appointments


@pytest.mark.parametrize(
    ("cat_id", "message"),
    [
        ("25AUG24-NLF-1", "canonical"),
        ("26AUG24-AMC-1", "canonical"),
        ("26FEB30-NLF-1", "valid run date"),
        ("26AUG24-NLF-0", "canonical"),
    ],
)
def test_rejects_cat_identity_mismatch(cat_id: str, message: str) -> None:
    """Reject cat IDs that conflict with configured run identity rules."""
    with pytest.raises(PipelineError, match=message):
        TreatmentCat(cat_id, "Nebula", "Nebula", "Alexa", (_appointment(),))


def test_rejects_duplicate_appointment_identity() -> None:
    """Reject two appointments whose identical dates would collide at publication."""
    appointments = (_appointment(), _appointment())

    with pytest.raises(PipelineError, match="unique service dates"):
        TreatmentCat("26AUG24-NLF-1", "Nebula", "Nebula", "Alexa", appointments)


@pytest.mark.parametrize("appointments", [(), [], ("not an appointment",)])
def test_rejects_invalid_appointment_collection(appointments: object) -> None:
    """Require a non-empty immutable collection of typed appointments."""
    with pytest.raises(PipelineError, match="cat appointments"):
        TreatmentCat(  # type: ignore[arg-type]
            "26AUG24-NLF-1", "Nebula", "Nebula", "Alexa", appointments
        )
