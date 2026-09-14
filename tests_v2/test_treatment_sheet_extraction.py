"""Tests for treatment-sheet parsing and manifest identity validation."""

from datetime import date
from pathlib import Path

import pytest
import treatment_sheet_extraction
from errors import PipelineError
from models_treatment_sheet import CatRecord, ManifestEntry, MedicalFindings
from preflight import RunManifest
from treatment_sheet_extraction import (
    _appointments,
    _box_text,
    _color,
    _service_date,
    _validate_identity,
    _weight,
    extract_treatment_sheets,
)


class FakeTable:
    """Provide extracted rows through the pdfplumber table interface."""

    def __init__(self, rows: list[list[str | None]]) -> None:
        """Store the rows returned by extraction."""
        self.rows = rows

    def extract(self) -> list[list[str | None]]:
        """Return the configured table cells."""
        return self.rows


class FakePage:
    """Provide the page text and tables used by parser unit tests."""

    width = 612

    def __init__(self, text: str, tables: list[list[list[str | None]]]) -> None:
        """Store extracted text and ruled-table values."""
        self.text = text
        self.tables = [FakeTable(rows) for rows in tables]

    def extract_text(self) -> str:
        """Return the configured page text."""
        return self.text

    def find_tables(self) -> list[FakeTable]:
        """Return configured ruled tables."""
        return self.tables

    def crop(self, _box: object) -> "FakePage":
        """Return this page for scalar crop-normalization tests."""
        return self


def _bottom_table(communication: str) -> list[list[str | None]]:
    """Build the optional Tests/RX/Communication table."""
    return [["stseT", "CBC"], ["XR", "Clavamox"], ["noitacinummoC", communication]]


def _medical_tables() -> list[list[list[str | None]]]:
    """Build representative required NLF medical tables."""
    return [
        [["Services Received\nCat Spay\nAppt & Animal Notes: Already chipped"]],
        [
            [
                "Exam\nHealthy\nIf declined for surgery, reason: "
                "If high risk waiver, reason: Murmur 2/6\nOwner response: Accepted"
            ],
            ["Medical Flag\nFleas"],
            ["Drugs Administered\nConvenia", "Surgical Summary\nSpay completed"],
            ["Internal Medical Notes (Surgical, Wellness and Recheck)\nMonitor"],
        ],
        _bottom_table("client instructions"),
    ]


def _header(service_date: str = "8/24/2026") -> str:
    """Build one valid treatment-sheet appointment header."""
    return (
        f"Service Date: {service_date}\nFemale Cat\n"
        "Color: Black/ Type: Shelter\nMicrochip #: Weight: 7.70 lbs"
    )


def _record(*, display_name: str = "(F) Sample Cat", owner_name: str = "Sample Owner") -> CatRecord:
    """Build a parsed record with selected identity overrides."""
    appointments = _appointments([FakePage(_header(), _medical_tables())])
    return CatRecord(
        "26SEP03-NLF-1",
        display_name,
        display_name,
        owner_name,
        appointments,
    )


def test_parses_appointments_and_continuation_medical_text() -> None:
    """Preserve header values and append medical fields from continuation pages."""
    pages = [
        FakePage(_header(), _medical_tables()),
        FakePage("Page 2 of 2", [_bottom_table("follow-up text")]),
    ]

    appointments = _appointments(pages)

    assert len(appointments) == 1
    appointment = appointments[0]
    assert appointment.service_date == date(2026, 8, 24)
    assert appointment.gender == "Female"
    assert appointment.microchip_number is None
    assert appointment.color == "Black"
    assert appointment.weight == "7.70 lbs"
    assert appointment.medical_findings == MedicalFindings(
        services_received="Cat Spay",
        appointment_animal_notes="Already chipped",
        exam="Healthy",
        high_risk_waiver_reason="Murmur 2/6",
        owner_response="Accepted",
        medical_flag="Fleas",
        drugs_administered="Convenia",
        surgical_summary="Spay completed",
        internal_notes="Monitor",
        tests="CBC\n\nCBC",
        rx="Clavamox\n\nClavamox",
        client_communication="client instructions\n\nfollow-up text",
    )


def test_rejects_malformed_treatment_sheet_values() -> None:
    """Reject an invalid present date and missing required medical fields."""
    with pytest.raises(PipelineError, match="service date is invalid"):
        _service_date(_header("2/30/2026"))
    with pytest.raises(PipelineError, match="Services Received field was not found"):
        _appointments([FakePage(_header(), [])])


def test_preserves_optional_header_values() -> None:
    """Preserve unusual color and weight precision while accepting blanks."""
    assert _color("Color: White/White Type: Community Cat") == "White/White"
    assert _color("Color: / Type: Shelter") is None
    assert _weight("Weight: 6.80 lbs") == "6.80 lbs"
    assert _weight("Weight: lbs") is None


def test_normalizes_wrapped_treatment_sheet_header_field() -> None:
    """Collapse visual wrapping in cropped identity fields without altering medical text."""
    page = FakePage("(F) Jelly Bean (26-\n7465)", [])

    assert _box_text(page, (0, 0, 100, 100)) == "(F) Jelly Bean (26-7465)"


@pytest.mark.parametrize(
    ("entry", "record", "message"),
    [
        (ManifestEntry("Different Owner", "Sample Cat", "cat.pdf"), _record(), "owner mismatch"),
        (
            ManifestEntry("Sample Owner", "Sample", "cat.pdf"),
            _record(display_name="Sampleton"),
            "cat mismatch",
        ),
    ],
)
def test_rejects_manifest_identity_mismatch(
    entry: ManifestEntry, record: CatRecord, message: str
) -> None:
    """Reject owner differences and cat-name substring false positives."""
    with pytest.raises(PipelineError, match=message):
        _validate_identity(entry, record)


def test_extracts_complete_manifest_in_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parse all sheets before returning authoritative manifest identities."""
    entries = (
        ManifestEntry("Sample Owner", "Sample Cat", "one.pdf"),
        ManifestEntry("Other Owner", "Other Cat", "two.pdf"),
    )
    paths = (tmp_path / "one.pdf", tmp_path / "two.pdf")
    for path in paths:
        path.write_bytes(b"%PDF-placeholder")
    manifest = RunManifest(date(2026, 9, 3), entries, paths)

    def parsed(path: Path, _run_date: date, sequence: int) -> CatRecord:
        """Return a valid parsed record aligned to the selected source path."""
        identity = entries[sequence - 1]
        record = _record(display_name=f"(F) {identity.cat_name}", owner_name=identity.owner_name)
        return CatRecord(
            f"26SEP03-NLF-{sequence}",
            record.display_name,
            record.cat_name,
            record.owner_name,
            record.appointments,
        )

    monkeypatch.setattr(treatment_sheet_extraction, "parse_treatment_sheet", parsed)

    records = extract_treatment_sheets(manifest)

    assert [record.cat_id for record in records] == ["26SEP03-NLF-1", "26SEP03-NLF-2"]
    assert [record.cat_name for record in records] == ["Sample Cat", "Other Cat"]
    assert [record.owner_name for record in records] == ["Sample Owner", "Other Owner"]


def test_manifest_failure_returns_no_partial_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail the complete extraction when a later sheet has the wrong identity."""
    entries = (
        ManifestEntry("Sample Owner", "Sample Cat", "one.pdf"),
        ManifestEntry("Other Owner", "Other Cat", "two.pdf"),
    )
    paths = (tmp_path / "one.pdf", tmp_path / "two.pdf")
    for path in paths:
        path.write_bytes(b"%PDF-placeholder")
    manifest = RunManifest(date(2026, 9, 3), entries, paths)

    def parsed(_path: Path, _run_date: date, sequence: int) -> CatRecord:
        """Return one match followed by one deliberate owner mismatch."""
        owner = "Sample Owner" if sequence == 1 else "Wrong Owner"
        name = "Sample Cat" if sequence == 1 else "Other Cat"
        return _record(display_name=name, owner_name=owner)

    monkeypatch.setattr(treatment_sheet_extraction, "parse_treatment_sheet", parsed)

    with pytest.raises(PipelineError, match="two.pdf: owner mismatch"):
        extract_treatment_sheets(manifest)
