"""Tests for treatment-sheet parsing, identity fields, and parser lookup."""

from treatment_sheet_parser.nlf.errors import TreatmentSheetError
from treatment_sheet_parser.nlf.models import Appointment, MedicalFindings
from treatment_sheet_parser.nlf.run_id import make_cat_id
from treatment_sheet_parser.nlf.treatment_sheet import (
    _appointment,
    _appointments,
    _cat_name,
    _color,
    _medical_findings,
    _optional_group,
    _owner_name,
    _service_date,
    _weight,
)


class FakePage:
    """Provide the minimal PDF-page interface used by parser unit tests."""

    width = 612

    def __init__(self, text: str) -> None:
        """Store the text returned by the fake page."""
        self.text = text

    def extract_text(self) -> str:
        """Return the page's configured extracted text."""
        return self.text

    def crop(self, _box: object) -> "FakePage":
        """Return this page because test text does not require real cropping."""
        return self


class FakeTable:
    """Return configured cells through the pdfplumber table interface."""

    def __init__(self, rows: list[list[str | None]]) -> None:
        """Store table rows for extraction."""
        self.rows = rows

    def extract(self) -> list[list[str | None]]:
        """Return the configured table rows."""
        return self.rows


class MedicalPage(FakePage):
    """Provide text and ruled tables for medical-field tests."""

    def __init__(self, text: str, tables: list[list[list[str | None]]]) -> None:
        """Store page text and table cells."""
        super().__init__(text)
        self.tables = [FakeTable(rows) for rows in tables]

    def find_tables(self) -> list[FakeTable]:
        """Return the configured ruled tables."""
        return self.tables


def test_full_cat_name_is_preserved() -> None:
    """Preserve annotations contained in a cat's full display name."""
    assert _cat_name([FakePage("Sterling 6 (?) Bonus cat")]) == "Sterling 6 (?) Bonus cat"


def test_blank_owner_is_na() -> None:
    """Represent a blank owner field with the expected placeholder."""
    assert _owner_name([FakePage("Owner:")]) == "N/A"


def test_owner_is_preserved() -> None:
    """Preserve a populated owner name exactly as printed."""
    assert _owner_name([FakePage("Owner: Alexa Camorlinga")]) == "Alexa Camorlinga"


def test_all_appointment_pages_are_collected() -> None:
    """Collect every page that begins a dated appointment."""
    header = """Female Cat
    Color: Black/ Type: Shelter
    Microchip #: Weight: 7.70 lbs"""
    medical_tables = _medical_tables()
    pages = [
        MedicalPage(f"Service Date: 8/6/2026\n{header}", medical_tables),
        MedicalPage("Page 2 of 2", [_bottom_table("follow-up text")]),
        MedicalPage(f"Service Date: 8/17/2026\n{header}", medical_tables),
    ]

    assert list(_appointments(pages)) == ["2026-08-06", "2026-08-17"]


def _medical_tables() -> list[list[list[str | None]]]:
    """Build representative NLF treatment-sheet table cells."""
    return [
        [["Services Received\nCat Spay\nAppt & Animal Notes: Already chipped", "Rabies"]],
        [
            [
                "Exam\nAssessment: Yes\nIf declined for surgery, reason: "
                "If high risk waiver, reason: HM 2/6\nOwner response: Accepted",
                None,
            ],
            ["Medical Flag\nFleas", "Veterinarian"],
            ["Drugs Administered\nConvenia", "Surgical Summary\nWound cleaned"],
            [
                "Internal Medical Notes (Surgical, Wellness and Recheck)\nMonitor wound",
                "Anesthesia Start Time:",
            ],
        ],
        _bottom_table("client instructions"),
    ]


def _bottom_table(communication: str) -> list[list[str | None]]:
    """Build the three-row bottom medical table."""
    return [["stseT", "CBC"], ["XR", "Clavamox"], ["noitacinummoC", communication]]


def test_medical_fields_are_extracted_with_continuation_text() -> None:
    """Preserve exact source values and append continuation-page communication."""
    pages = [
        MedicalPage("Service Date: 8/6/2026", _medical_tables()),
        MedicalPage("Page 2 of 2", [_bottom_table("follow-up text")]),
    ]

    findings = _medical_findings(pages)

    assert findings == MedicalFindings(
        services_received="Cat Spay",
        appointment_animal_notes="Already chipped",
        exam="Assessment: Yes",
        high_risk_waiver_reason="HM 2/6",
        owner_response="Accepted",
        medical_flag="Fleas",
        drugs_administered="Convenia",
        surgical_summary="Wound cleaned",
        internal_notes="Monitor wound",
        tests="CBC\n\nCBC",
        rx="Clavamox\n\nClavamox",
        client_communication="client instructions\n\nfollow-up text",
    )


def test_missing_medical_table_is_an_error() -> None:
    """Reject a sheet that cannot provide the required medical sections."""
    page = MedicalPage("Service Date: 8/6/2026", [])

    try:
        _medical_findings([page])
    except TreatmentSheetError as exc:
        assert str(exc) == "Services Received field was not found"
    else:
        raise AssertionError("expected TreatmentSheetError")


def test_blank_microchip_and_weight() -> None:
    """Represent blank microchip and weight fields as missing values."""
    text = """Female Cat
    Color: Black/ Type: Shelter
    Microchip #: Weight: lbs"""

    assert _appointment(text) == Appointment(
        gender="Female",
        microchip_number=None,
        color="Black",
        weight=None,
    )


def test_notes_do_not_fill_blank_microchip() -> None:
    """Do not mistake digits or text in notes for a blank microchip field."""
    text = """Female Cat
    Color: Brown Tabby/ Type: Shelter
    Microchip #: Weight: 8.66 lbs
    Appt & Animal Notes: Already chipped"""

    assert _appointment(text).microchip_number is None


def test_missing_microchip_is_none() -> None:
    """Return no value when a required microchip label has no value."""
    assert (
        _optional_group(
            "Microchip #: Weight: 4.80 lbs",
            r"Microchip\s*#:\s*(\d+)?\s*Weight:",
            "microchip",
        )
        is None
    )


def test_microchip_is_preserved_as_text() -> None:
    """Preserve a microchip number as text rather than a numeric value."""
    text = "Microchip #: 985113013939730 Weight: 6.80 lbs"
    assert (
        _optional_group(text, r"Microchip\s*#:\s*(\d+)?\s*Weight:", "microchip")
        == "985113013939730"
    )


def test_weight_keeps_printed_precision() -> None:
    """Keep the decimal precision printed in a weight field."""
    assert _weight("Weight: 6.80 lbs") == "6.80 lbs"


def test_suspicious_color_is_preserved() -> None:
    """Preserve unusual but valid color text for later review."""
    assert _color("Color: White/White Type: Community Cat (Feral)") == "White/White"


def test_blank_color_is_none() -> None:
    """Return no value for a blank color field."""
    assert _color("Color: / Type: Shelter") is None


def test_service_date_becomes_iso_key() -> None:
    """Normalize a printed service date for use as an ISO dictionary key."""
    assert _service_date("Service Date: 8/17/2026") == "2026-08-17"


def test_cat_id_uses_input_date_and_manifest_sequence() -> None:
    """Build an NLF cat ID from the date and manifest sequence number."""
    assert make_cat_id("2026-06-12", 4) == "26JUN12-NLF-4"


def test_cat_id_date_requires_iso_format() -> None:
    """Reject a cat ID date that is not in ISO format."""
    try:
        make_cat_id("06-12", 1)
    except ValueError as exc:
        assert str(exc) == "cat ID date must use YYYY-MM-DD format"
    else:
        raise AssertionError("expected ValueError")


def test_cat_id_date_requires_zero_padding() -> None:
    """Reject an ISO-like cat ID date without canonical zero padding."""
    try:
        make_cat_id("2026-6-12", 1)
    except ValueError as exc:
        assert str(exc) == "cat ID date must use YYYY-MM-DD format"
    else:
        raise AssertionError("expected ValueError")


def test_page_without_appointment_header_is_ignored() -> None:
    """Ignore a continuation page without a service-date header."""
    assert _service_date("Nine Lives - Page 2 of 2") is None


def test_missing_labeled_field_is_an_error() -> None:
    """Raise a domain error when an expected labeled field is absent."""
    try:
        _weight("no weight label")
    except TreatmentSheetError as exc:
        assert str(exc) == "weight field was not found"
    else:
        raise AssertionError("expected TreatmentSheetError")
