"""Represent validated treatment-sheet inputs and extracted values."""

import re
from dataclasses import dataclass, field, fields
from datetime import date as Date
from pathlib import Path, PurePath

from errors import PipelineError
from global_constants import LOCATION_CODE, RUN_YEAR
from models_validation import (
    _require_absolute_path,
    _require_date,
    _require_microchip_number,
    _require_non_empty_tuple_of,
    _require_string,
    _require_text,
)
from resolve_run_identity import MONTH_ABBREVIATIONS

GENDERS = frozenset({"Female", "Male", "Unknown"})
# Cat IDs encode the configured run date and one-based manifest position; the
# separate calendar check below rejects regex-shaped but impossible dates.
CAT_ID_PATTERN = re.compile(
    rf"{RUN_YEAR % 100:02d}({'|'.join(MONTH_ABBREVIATIONS)})(\d{{2}})-"
    rf"{LOCATION_CODE}-([1-9]\d*)"
)


@dataclass(frozen=True)
class ManifestEntry:
    """Identify one expected cat and its treatment-sheet PDF."""

    owner_name: str
    cat_name: str
    filename: str

    def __post_init__(self) -> None:
        """Reject missing identities and filenames outside the input directory."""
        _require_text(self.owner_name, "manifest owner name")
        _require_text(self.cat_name, "manifest cat name")
        _validate_pdf_filename(self.filename)


@dataclass(frozen=True)
class RunManifest:
    """Store a validated manifest and its aligned treatment-sheet source paths."""

    run_date: Date
    entries: tuple[ManifestEntry, ...]
    treatment_sheet_paths: tuple[Path, ...]

    def __post_init__(self) -> None:
        """Require typed entries paired in order with absolute PDF paths."""
        _require_date(self.run_date, "manifest date")
        entries = _require_non_empty_tuple_of(self.entries, ManifestEntry, "manifest entries")
        paths = _require_non_empty_tuple_of(
            self.treatment_sheet_paths,
            Path,
            "treatment sheet paths",
        )
        if len(entries) != len(paths):
            raise PipelineError(
                "manifest entries and treatment sheet paths must have equal lengths"
            )
        # Positional alignment is significant: the manifest order determines cat
        # IDs and each entry must continue to point at its own source PDF.
        for entry, path in zip(entries, paths, strict=True):
            _validate_treatment_sheet_path(entry, path)


@dataclass(frozen=True)
class MedicalFindings:
    """Preserve exact text from treatment-sheet medical and procedural fields."""

    services_received: str = ""
    appointment_animal_notes: str = ""
    exam: str = ""
    surgery_decline_reason: str = ""
    high_risk_waiver_reason: str = ""
    owner_response: str = ""
    medical_flag: str = ""
    drugs_administered: str = ""
    surgical_summary: str = ""
    internal_notes: str = ""
    notes: str = ""
    tests: str = ""
    rx: str = ""
    client_communication: str = ""

    def __post_init__(self) -> None:
        """Require every preserved medical field to remain textual."""
        # Dataclass reflection keeps this invariant complete when a new preserved
        # medical field is added to the model.
        for item in fields(self):
            _require_string(getattr(self, item.name), item.name)


@dataclass(frozen=True)
class TreatmentSheetAppointment:
    """Store the validated treatment-sheet values for one dated visit."""

    service_date: Date
    gender: str
    microchip_number: str | None
    color: str | None
    weight: str | None
    medical_findings: MedicalFindings = field(default_factory=MedicalFindings)

    def __post_init__(self) -> None:
        """Validate the appointment date, characteristics, and medical findings."""
        _require_date(self.service_date, "appointment service date")
        _validate_gender(self.gender)
        _require_microchip_number(self.microchip_number, "appointment microchip")
        _validate_optional_text(self.color, "appointment color")
        _validate_optional_text(self.weight, "appointment weight")
        if not isinstance(self.medical_findings, MedicalFindings):
            raise PipelineError("appointment medical findings must be MedicalFindings")


@dataclass(frozen=True)
class TreatmentCat:
    """Store one manifest cat and its ordered treatment-sheet appointments."""

    cat_id: str
    display_name: str
    cat_name: str
    owner_name: str
    appointments: tuple[TreatmentSheetAppointment, ...]

    def __post_init__(self) -> None:
        """Validate the stable identity and require unique dated appointments."""
        _validate_cat_id(self.cat_id)
        _require_text(self.display_name, "cat display name")
        _require_text(self.cat_name, "cat name")
        _require_text(self.owner_name, "cat owner name")
        _validate_appointments(self.appointments)


def _validate_pdf_filename(value: object) -> None:
    """Require a bare PDF filename that cannot redirect input reads."""
    _require_text(value, "manifest filename")
    path = PurePath(value)
    # Reject both absolute paths and relative traversal; all source files must be
    # resolved beneath the already validated run input directory.
    if path.is_absolute() or len(path.parts) != 1 or path.suffix.casefold() != ".pdf":
        raise PipelineError("manifest filename must be a bare PDF filename")


def _validate_treatment_sheet_path(entry: ManifestEntry, path: Path) -> None:
    """Require an absolute PDF path aligned with its manifest filename."""
    _require_absolute_path(path, "treatment sheet path")
    if path.name != entry.filename:
        raise PipelineError("treatment sheet path must match its manifest filename")


def _validate_gender(value: object) -> None:
    """Require one gender value emitted by the treatment-sheet parser."""
    _require_text(value, "appointment gender")
    if value not in GENDERS:
        choices = ", ".join(sorted(GENDERS))
        raise PipelineError(f"appointment gender must be one of: {choices}")


def _validate_optional_text(value: object, field_name: str) -> None:
    """Require either null or a non-empty textual field value."""
    if value is not None:
        _require_text(value, field_name)


def _validate_cat_id(value: object) -> None:
    """Require a canonical cat ID containing a real configured-year date."""
    _require_text(value, "cat ID")
    match = CAT_ID_PATTERN.fullmatch(value)
    if match is None:
        raise PipelineError("cat ID must use canonical YYMMMDD-NLF-N format")
    month_name, day, _sequence = match.groups()
    try:
        # Regex validation establishes shape; constructing a date establishes
        # calendar validity, including month length and leap years.
        Date(RUN_YEAR, MONTH_ABBREVIATIONS.index(month_name) + 1, int(day))
    except ValueError as exc:
        raise PipelineError("cat ID must contain a valid run date") from exc


def _validate_appointments(value: object) -> None:
    """Require a non-empty tuple of appointments with unique service dates."""
    appointments = _require_non_empty_tuple_of(value, TreatmentSheetAppointment, "cat appointments")
    dates = [item.service_date for item in appointments]
    if len(dates) != len(set(dates)):
        raise PipelineError("cat appointments must have unique service dates")
