"""Parse a Nine Lives Foundation treatment-sheet PDF into a ``CatRecord``.

The Nine Lives layout places the patient and owner names in fixed areas at the
top of the first page. Each appointment begins on a page containing a service
date and labeled gender, microchip, color, and weight fields.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

import pdfplumber

from treatment_sheet_parser.nlf.errors import TreatmentSheetError
from treatment_sheet_parser.nlf.models import Appointment, CatRecord, MedicalFindings
from treatment_sheet_parser.nlf.run_id import make_cat_id

VET_CODE = "NLF"

# The owner and patient names occupy adjacent boxes at the top of the first
# page. These x-coordinates are points in the PDF's coordinate system.
PATIENT_BOX_LEFT = 340
OWNER_BOX_LEFT = 185


# Public parser interface


def parse_treatment_sheet(path: str | Path, *, date: str, sequence: int = 1) -> CatRecord:
    """Parse and validate one Nine Lives treatment-sheet PDF.

    ``sequence`` is the PDF's one-based position in its source manifest. This
    function only returns a ``CatRecord``; it never creates or writes output.

    Args:
        path: Nine Lives treatment-sheet PDF to read.
        date: Run date in strict ``YYYY-MM-DD`` format.
        sequence: One-based sheet position used in the generated cat ID.

    Returns:
        A ``CatRecord`` containing the generated ID, printed patient and owner
        names, and appointments keyed by service date.

    Raises:
        TreatmentSheetError: If the PDF cannot be read; the cat name, owner
            label, gender, microchip label, color label, or weight label is
            missing; no appointments exist; ``date`` is not ``YYYY-MM-DD``; or
            ``sequence`` is less than one.
    """
    pdf_path = Path(path)
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # Read all values while the underlying PDF file is still open.
            appointments = _appointments(pdf.pages)
            display_name = _cat_name(pdf.pages)
            owner_name = _owner_name(pdf.pages)
    except (IndexError, OSError) as exc:
        raise TreatmentSheetError(f"could not read {pdf_path}: {exc}") from exc

    if not appointments:
        raise TreatmentSheetError("no appointments were found")
    try:
        cat_id = make_cat_id(date, sequence)
    except ValueError as exc:
        raise TreatmentSheetError(str(exc)) from exc
    return CatRecord(
        cat_id=cat_id,
        display_name=display_name,
        cat_name=display_name,
        owner_name=owner_name,
        appointments=appointments,
    )


# Appointment parsing


def _appointments(pages: list[Any]) -> dict[str, Appointment]:
    """Collect appointments from pages containing service-date headers.

    Args:
        pages: PDF pages in source order.

    Returns:
        Parsed appointments keyed by ISO service date.

    Raises:
        TreatmentSheetError: If two appointment headers have the same date, or
            a header lacks gender or a microchip, color, or weight label.
    """
    appointments: dict[str, Appointment] = {}
    for service_date, appointment_pages in _appointment_pages(pages):
        if service_date in appointments:
            # Dates are dictionary keys, so accepting a duplicate would
            # silently replace an earlier appointment.
            raise TreatmentSheetError(f"duplicate appointment date: {service_date}")
        text = appointment_pages[0].extract_text() or ""
        appointments[service_date] = _appointment(
            text, medical_findings=_medical_findings(appointment_pages)
        )
    return appointments


def _appointment(text: str, *, medical_findings: MedicalFindings | None = None) -> Appointment:
    """Parse labeled values from one Nine Lives appointment header.

    Args:
        text: Text extracted from an appointment's first page.

    Returns:
        The gender, microchip number, color, and weight from the header.

    Raises:
        TreatmentSheetError: If gender is absent or the microchip, color, or
            weight label cannot be found.
    """
    return Appointment(
        gender=_required_group(text, r"\b(Female|Male|Unknown)\s+Cat\b", "gender"),
        # Stop at the Weight label so digits elsewhere in notes cannot be
        # mistaken for a microchip number when the printed field is blank.
        microchip_number=_optional_group(
            text, r"Microchip\s*#:\s*(\d{9,15})?\s*Weight:", "microchip"
        ),
        color=_color(text),
        weight=_weight(text),
        medical_findings=medical_findings or MedicalFindings(),
    )


def _appointment_pages(pages: list[Any]) -> list[tuple[str, list[Any]]]:
    """Group each dated appointment page with its continuation pages."""
    groups: list[tuple[str, list[Any]]] = []
    current_pages: list[Any] | None = None
    for page in pages:
        service_date = _service_date(page.extract_text() or "")
        if service_date:
            current_pages = [page]
            groups.append((service_date, current_pages))
        elif current_pages is not None:
            current_pages.append(page)
    return groups


def _medical_findings(pages: list[Any]) -> MedicalFindings:
    """Extract exact field text from one appointment and its continuations."""
    page_tables = [_extract_tables(page) for page in pages]
    services_cell = _cell_with_heading(page_tables[0], "Services Received")
    exam_cell = _cell_with_heading(page_tables[0], "Exam")
    services, appointment_notes = _split_field(services_cell, "Appt & Animal Notes:")
    exam, decline_reason = _split_field(exam_cell, "If declined for surgery, reason:")
    decline_reason, high_risk_reason = _split_field(decline_reason, "If high risk waiver, reason:")
    high_risk_reason, owner_response = _split_field(high_risk_reason, "Owner response:")
    tests, rx, communication = _bottom_fields(page_tables)
    return MedicalFindings(
        services_received=_remove_heading(services, "Services Received"),
        appointment_animal_notes=appointment_notes,
        exam=_remove_heading(exam, "Exam"),
        surgery_decline_reason=decline_reason,
        high_risk_waiver_reason=high_risk_reason,
        owner_response=owner_response,
        medical_flag=_field_value(page_tables[0], "Medical Flag"),
        drugs_administered=_field_value(page_tables[0], "Drugs Administered"),
        surgical_summary=_field_value(page_tables[0], "Surgical Summary"),
        internal_notes=_field_value(
            page_tables[0], "Internal Medical Notes (Surgical, Wellness and Recheck)"
        ),
        tests=tests,
        rx=rx,
        client_communication=communication,
    )


def _extract_tables(page: Any) -> list[list[list[str | None]]]:
    """Return text cells from all ruled tables on one PDF page."""
    if not hasattr(page, "find_tables"):
        raise TreatmentSheetError("medical findings tables were not found")
    return [table.extract() for table in page.find_tables()]


def _cell_with_heading(tables: list[list[list[str | None]]], heading: str) -> str:
    """Find the table cell that begins with a required field heading."""
    for table in tables:
        for row in table:
            for cell in row:
                if cell and cell.startswith(heading):
                    return cell.strip()
    raise TreatmentSheetError(f"{heading} field was not found")


def _field_value(tables: list[list[list[str | None]]], heading: str) -> str:
    """Return one required table cell without its heading."""
    return _remove_heading(_cell_with_heading(tables, heading), heading)


def _remove_heading(text: str, heading: str) -> str:
    """Remove a validated field heading and surrounding whitespace."""
    if not text.startswith(heading):
        raise TreatmentSheetError(f"{heading} field was not found")
    return text.removeprefix(heading).strip()


def _split_field(text: str, next_heading: str) -> tuple[str, str]:
    """Split adjacent labeled fields while retaining their exact values."""
    before, separator, after = text.partition(next_heading)
    if not separator:
        raise TreatmentSheetError(f"{next_heading.rstrip(':')} field was not found")
    return before.strip(), after.strip()


def _bottom_fields(
    page_tables: list[list[list[list[str | None]]]],
) -> tuple[str, str, str]:
    """Combine Tests, RX, and Client Communication across continuation pages."""
    values = [[], [], []]
    for tables in page_tables:
        bottom = _bottom_table(tables)
        for index in range(3):
            value = _table_value(bottom, index)
            if value:
                values[index].append(value)
    return tuple("\n\n".join(parts) for parts in values)


def _bottom_table(tables: list[list[list[str | None]]]) -> list[list[str | None]]:
    """Return the three-row Tests/RX/Client Communication table when present."""
    for table in tables:
        labels = [row[0] or "" for row in table if row]
        if len(table) == 3 and any("XR" in label for label in labels):
            return table
    return []


def _table_value(table: list[list[str | None]], row_index: int) -> str:
    """Read a value cell from an optional bottom-section table."""
    if not table or len(table[row_index]) < 2:
        return ""
    return (table[row_index][1] or "").strip()


# First-page patient identity


def _box_text(page: Any, box: tuple[int, int, float, int]) -> str:
    """Extract trimmed text from a rectangular PDF region.

    Args:
        page: A pdfplumber-compatible page object.
        box: Crop coordinates as ``(left, top, right, bottom)``.

    Returns:
        Extracted text without surrounding whitespace, or an empty string.
    """
    return (page.crop(box).extract_text() or "").strip()


def _cat_name(pages: list[Any]) -> str:
    """Read the full patient display name from the first-page header.

    Args:
        pages: PDF pages with the treatment-sheet header on the first page.

    Returns:
        The complete printed patient name.

    Raises:
        IndexError: If the PDF contains no pages.
        TreatmentSheetError: If the patient-name region is blank.
    """
    first_page = pages[0]
    # Cropping avoids nearby owner text and preserves annotations such as
    # ``(F)`` or ``(?)`` that are meaningful parts of the display name.
    patient_box = (PATIENT_BOX_LEFT, 0, first_page.width, 30)
    name = _box_text(first_page, patient_box)
    if name:
        return name
    raise TreatmentSheetError("cat name was not found")


def _owner_name(pages: list[Any]) -> str:
    """Read the owner field from the first-page header.

    Args:
        pages: PDF pages with the treatment-sheet header on the first page.

    Returns:
        The printed owner name, or ``N/A`` when the labeled field is blank.

    Raises:
        IndexError: If the PDF contains no pages.
        TreatmentSheetError: If the cropped text lacks the ``Owner:`` label.
    """
    first_page = pages[0]
    owner_box = (OWNER_BOX_LEFT, 0, PATIENT_BOX_LEFT, 27)
    field = _box_text(first_page, owner_box)
    if not field.startswith("Owner:"):
        raise TreatmentSheetError("owner field was not found")
    return field.removeprefix("Owner:").strip() or "N/A"


# Labeled appointment fields


def _required_group(text: str, pattern: str, field: str) -> str:
    """Extract a required value from the first regex capture group.

    Args:
        text: Text to search.
        pattern: Regular expression containing at least one capture group.
        field: Human-readable field name used in errors.

    Returns:
        The trimmed value from capture group one.

    Raises:
        TreatmentSheetError: If the pattern does not match.
    """
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        raise TreatmentSheetError(f"{field} was not found")
    return match.group(1).strip()


def _optional_group(text: str, pattern: str, field: str) -> str | None:
    """Extract a value that may be blank from a required labeled field.

    Args:
        text: Text to search.
        pattern: Regular expression whose first capture group may be empty.
        field: Human-readable field name used in errors.

    Returns:
        Capture group one, or ``None`` when the group did not participate.

    Raises:
        TreatmentSheetError: If the pattern, including its field label, is absent.
    """
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        raise TreatmentSheetError(f"{field} field was not found")
    return match.group(1) or None


def _service_date(text: str) -> str | None:
    """Extract and normalize an appointment's service date.

    Args:
        text: Text extracted from one PDF page.

    Returns:
        The date in ``YYYY-MM-DD`` format, or ``None`` when the page has no
        service-date header.

    Raises:
        ValueError: If a present service date is not a real calendar date.
    """
    match = re.search(r"Service\s+Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%m/%d/%Y").date().isoformat()


def _color(text: str) -> str | None:
    """Read color text bounded by the following ``Type`` label.

    Args:
        text: Appointment-header text.

    Returns:
        The printed color with internal slashes preserved, or ``None`` when blank.

    Raises:
        TreatmentSheetError: If the labeled color field is absent.
    """
    value = _optional_group(text, r"Color:\s*(.*?)\s*/?\s*Type:", "color")
    if value is None:
        return None
    return value.rstrip(" /") or None


def _weight(text: str) -> str | None:
    """Read weight as text so printed decimal precision is preserved.

    Args:
        text: Appointment-header text.

    Returns:
        The numeric text followed by ``lbs``, or ``None`` when blank.

    Raises:
        TreatmentSheetError: If the labeled weight field is absent or malformed.
    """
    value = _optional_group(text, r"Weight:\s*(\d+(?:\.\d+)?)?\s*lbs?\b", "weight")
    return f"{value} lbs" if value else None
