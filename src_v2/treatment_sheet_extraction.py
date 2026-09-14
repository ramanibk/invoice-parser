"""Parse and identity-check NLF treatment-sheet PDFs without writing output."""

import re
import unicodedata
from datetime import date as Date
from datetime import datetime as DateTime
from pathlib import Path
from typing import Any

import pdfplumber
from errors import PipelineError
from models_treatment_sheet import (
    CatRecord,
    ManifestEntry,
    MedicalFindings,
    TreatmentSheetAppointment,
)
from pdfminer.pdfparser import PDFSyntaxError
from preflight import RunManifest
from resolve_run_identity import make_cat_id
from text_normalization import normalize_wrapped_text

PATIENT_BOX_LEFT = 340
OWNER_BOX_LEFT = 185


def extract_treatment_sheets(manifest: RunManifest) -> tuple[CatRecord, ...]:
    """Parse every manifest sheet and return records only after all identities match."""
    if not isinstance(manifest, RunManifest):
        raise PipelineError("treatment-sheet extraction requires RunManifest")
    return tuple(
        _extract_manifest_entry(manifest, entry, path, sequence)
        for sequence, (entry, path) in enumerate(
            zip(manifest.entries, manifest.treatment_sheet_paths, strict=True), start=1
        )
    )


def parse_treatment_sheet(path: Path, run_date: Date, sequence: int) -> CatRecord:
    """Read one absolute PDF and return its typed identity and dated appointments."""
    _validate_source_path(path)
    try:
        with pdfplumber.open(path) as pdf:
            pages = list(pdf.pages)
            appointments = _appointments(pages)
            display_name = _cat_name(pages)
            owner_name = _owner_name(pages)
    except PipelineError:
        raise
    except (OSError, PDFSyntaxError, ValueError, IndexError) as exc:
        raise PipelineError(f"could not read treatment sheet {path}: {exc}") from exc
    if not appointments:
        raise PipelineError("no appointments were found")
    return CatRecord(
        cat_id=make_cat_id(run_date, sequence),
        display_name=display_name,
        cat_name=display_name,
        owner_name=owner_name,
        appointments=appointments,
    )


def _extract_manifest_entry(
    manifest: RunManifest,
    entry: ManifestEntry,
    path: Path,
    sequence: int,
) -> CatRecord:
    """Parse one sheet, verify its manifest identity, and apply authoritative names."""
    try:
        record = parse_treatment_sheet(path, manifest.run_date, sequence)
        _validate_identity(entry, record)
    except PipelineError as exc:
        raise PipelineError(f"{entry.filename}: {exc}") from exc
    return CatRecord(
        cat_id=record.cat_id,
        display_name=record.display_name,
        cat_name=entry.cat_name,
        owner_name=entry.owner_name,
        appointments=record.appointments,
    )


def _validate_source_path(value: object) -> None:
    """Require an existing absolute PDF before passing it to pdfplumber."""
    if not isinstance(value, Path) or not value.is_absolute():
        raise PipelineError("treatment-sheet extraction path must be an absolute Path")
    if value.suffix.casefold() != ".pdf" or not value.is_file():
        raise PipelineError(f"treatment-sheet source must be an existing PDF: {value}")


def _appointments(pages: list[Any]) -> tuple[TreatmentSheetAppointment, ...]:
    """Parse dated appointment groups and reject dates that would collide."""
    groups = _appointment_page_groups(pages)
    appointments = tuple(_appointment(service_date, group) for service_date, group in groups)
    dates = [appointment.service_date for appointment in appointments]
    if len(dates) != len(set(dates)):
        raise PipelineError("treatment sheet contains duplicate appointment dates")
    return appointments


def _appointment(service_date: Date, pages: list[Any]) -> TreatmentSheetAppointment:
    """Parse one appointment header and its complete preserved medical findings."""
    text = normalize_wrapped_text(pages[0].extract_text() or "")
    return TreatmentSheetAppointment(
        service_date=service_date,
        gender=_required_group(text, r"\b(Female|Male|Unknown)\s+Cat\b", "gender"),
        microchip_number=_optional_group(
            text, r"Microchip\s*#:\s*(\d{9,15})?\s*Weight:", "microchip"
        ),
        color=_color(text),
        weight=_weight(text),
        medical_findings=_medical_findings(pages),
    )


def _appointment_page_groups(pages: list[Any]) -> list[tuple[Date, list[Any]]]:
    """Attach continuation pages to the preceding service-date page."""
    groups: list[tuple[Date, list[Any]]] = []
    current_pages: list[Any] | None = None
    for page in pages:
        service_date = _service_date(page.extract_text() or "")
        if service_date is not None:
            current_pages = [page]
            groups.append((service_date, current_pages))
        elif current_pages is not None:
            current_pages.append(page)
    return groups


def _medical_findings(pages: list[Any]) -> MedicalFindings:
    """Extract exact medical field text from an appointment and continuations."""
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
    """Return text cells from every ruled table on one PDF page."""
    if not hasattr(page, "find_tables"):
        raise PipelineError("medical findings tables were not found")
    return [table.extract() for table in page.find_tables()]


def _cell_with_heading(tables: list[list[list[str | None]]], heading: str) -> str:
    """Find the table cell that begins with a required field heading."""
    for table in tables:
        for row in table:
            for cell in row:
                if cell and cell.startswith(heading):
                    return cell.strip()
    raise PipelineError(f"{heading} field was not found")


def _field_value(tables: list[list[list[str | None]]], heading: str) -> str:
    """Return one required table cell without its heading."""
    return _remove_heading(_cell_with_heading(tables, heading), heading)


def _remove_heading(text: str, heading: str) -> str:
    """Remove a validated field heading and surrounding whitespace."""
    if not text.startswith(heading):
        raise PipelineError(f"{heading} field was not found")
    return text.removeprefix(heading).strip()


def _split_field(text: str, next_heading: str) -> tuple[str, str]:
    """Split adjacent labeled fields while retaining their exact values."""
    before, separator, after = text.partition(next_heading)
    if not separator:
        raise PipelineError(f"{next_heading.rstrip(':')} field was not found")
    return before.strip(), after.strip()


def _bottom_fields(
    page_tables: list[list[list[list[str | None]]]],
) -> tuple[str, str, str]:
    """Combine Tests, RX, and Client Communication across continuation pages."""
    values: list[list[str]] = [[], [], []]
    for tables in page_tables:
        bottom = _bottom_table(tables)
        for index in range(3):
            value = _table_value(bottom, index)
            if value:
                values[index].append(value)
    combined = tuple("\n\n".join(parts) for parts in values)
    return combined[0], combined[1], combined[2]


def _bottom_table(tables: list[list[list[str | None]]]) -> list[list[str | None]]:
    """Return the optional three-row Tests/RX/Communication table."""
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


def _box_text(page: Any, box: tuple[int, int, float, int]) -> str:
    """Extract and normalize scalar text from one rectangular PDF region."""
    return normalize_wrapped_text(page.crop(box).extract_text() or "")


def _cat_name(pages: list[Any]) -> str:
    """Read the complete printed patient name from the first-page header."""
    first_page = pages[0]
    name = _box_text(first_page, (PATIENT_BOX_LEFT, 0, first_page.width, 30))
    if not name:
        raise PipelineError("cat name was not found")
    return name


def _owner_name(pages: list[Any]) -> str:
    """Read the printed owner name or normalize its labeled blank to N/A."""
    field = _box_text(pages[0], (OWNER_BOX_LEFT, 0, PATIENT_BOX_LEFT, 27))
    if not field.startswith("Owner:"):
        raise PipelineError("owner field was not found")
    return field.removeprefix("Owner:").strip() or "N/A"


def _required_group(text: str, pattern: str, field_name: str) -> str:
    """Return a required regex capture or raise a focused pipeline error."""
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        raise PipelineError(f"{field_name} was not found")
    return match.group(1).strip()


def _optional_group(text: str, pattern: str, field_name: str) -> str | None:
    """Return an optional capture while still requiring its surrounding label."""
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if match is None:
        raise PipelineError(f"{field_name} field was not found")
    return match.group(1) or None


def _service_date(text: str) -> Date | None:
    """Parse a present service-date label and ignore continuation pages."""
    text = normalize_wrapped_text(text)
    match = re.search(r"Service\s+Date:\s*(\d{1,2}/\d{1,2}/\d{4})", text)
    if match is None:
        return None
    try:
        return DateTime.strptime(match.group(1), "%m/%d/%Y").date()
    except ValueError as exc:
        raise PipelineError(f"treatment-sheet service date is invalid: {match.group(1)!r}") from exc


def _color(text: str) -> str | None:
    """Read optional color text bounded by the following Type label."""
    value = _optional_group(text, r"Color:\s*(.*?)\s*/?\s*Type:", "color")
    if value is None:
        return None
    return value.rstrip(" /") or None


def _weight(text: str) -> str | None:
    """Read optional weight text while preserving printed decimal precision."""
    value = _optional_group(text, r"Weight:\s*(\d+(?:\.\d+)?)?\s*lbs?\b", "weight")
    return f"{value} lbs" if value else None


def _validate_identity(entry: ManifestEntry, record: CatRecord) -> None:
    """Require the parsed owner and whole-token cat name to match the manifest."""
    if _normalize_name(entry.owner_name) != _normalize_name(record.owner_name):
        raise PipelineError(
            f"owner mismatch; manifest={entry.owner_name!r}, PDF={record.owner_name!r}"
        )
    if not _contains_name(record.display_name, entry.cat_name):
        raise PipelineError(
            f"cat mismatch; manifest={entry.cat_name!r}, PDF={record.display_name!r}"
        )


def _contains_name(display_name: str, expected_name: str) -> bool:
    """Return whether an expected name occurs as one contiguous token phrase."""
    display_tokens = _name_tokens(display_name)
    expected_tokens = _name_tokens(expected_name)
    width = len(expected_tokens)
    return bool(expected_tokens) and any(
        display_tokens[index : index + width] == expected_tokens
        for index in range(len(display_tokens))
    )


def _normalize_name(value: str) -> str:
    """Normalize identity text for punctuation- and case-insensitive comparison."""
    return " ".join(_name_tokens(value))


def _name_tokens(value: str) -> list[str]:
    """Return case-folded Unicode word tokens used by identity checks."""
    compatible = unicodedata.normalize("NFKC", value).casefold()
    return re.findall(r"[\w]+", compatible)
