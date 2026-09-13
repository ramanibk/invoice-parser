"""Load NLF manifests and validate their treatment-sheet identities."""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePath
from typing import Any, Protocol

from treatment_sheet_parser.nlf.errors import ExtractionError, TreatmentSheetError
from treatment_sheet_parser.nlf.models import CatRecord


class TreatmentSheetParser(Protocol):
    """Callable used to parse one NLF treatment sheet during a manifest run."""

    def __call__(self, path: str | Path, *, date: str, sequence: int = 1) -> CatRecord:
        """Return one parsed record without writing output."""
        ...


@dataclass(frozen=True)
class ManifestSheet:
    """Identity and filename declared for one treatment sheet."""

    owner_name: str
    cat_name: str
    filename: str


@dataclass(frozen=True)
class Manifest:
    """Date and treatment sheets from a validated source manifest."""

    date: str
    sheets: list[ManifestSheet]


def load_manifest(path: Path) -> Manifest:
    """Read and fully validate a source manifest JSON file.

    Returns a typed manifest only after its date and every treatment-sheet entry
    have passed structural validation. File and JSON failures are translated to
    ``ExtractionError`` with the source path.
    """
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExtractionError(f"could not read manifest {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExtractionError("manifest must contain a JSON object")
    return Manifest(
        date=_required_string(raw, "date", "manifest"),
        sheets=_manifest_sheets(raw.get("treatmentSheets")),
    )


def validate_date(date: str, manifest_date: str) -> None:
    """Require the requested and manifest dates to be identical strict ISO dates.

    Both values are parsed independently so malformed input is rejected even if
    the two malformed strings happen to be equal.
    """
    parsed_date = _parse_date(date)
    parsed_manifest_date = _parse_date(manifest_date)
    if parsed_date != parsed_manifest_date:
        raise ExtractionError(f"input date {date} does not match manifest date {manifest_date}")


def extract_sheets(
    directory: Path,
    sheets: list[ManifestSheet],
    date: str,
    parser: TreatmentSheetParser,
) -> list[tuple[ManifestSheet, CatRecord]]:
    """Parse and identity-check all declared PDFs in manifest order.

    Sequence numbers start at one and are passed to the clinic parser for stable
    cat IDs. List construction completes before callers create output, ensuring
    any later sheet failure discards the entire in-memory extraction.
    """
    return [
        (sheet, _extract_sheet(directory / sheet.filename, sheet, date, parser, index))
        for index, sheet in enumerate(sheets, start=1)
    ]


def contains_name(display_name: str, cat_name: str) -> bool:
    """Return whether a cat name occurs as a contiguous whole-token phrase.

    Token matching avoids substring false positives such as ``Ann`` matching
    ``Anna`` while permitting punctuation and case differences.
    """
    display = _name_tokens(display_name)
    expected = _name_tokens(cat_name)
    width = len(expected)
    return bool(expected) and any(
        display[index : index + width] == expected for index in range(len(display))
    )


def contains_owner(identity_text: str, owner_name: str) -> bool:
    """Return whether all owner tokens occur in invoice identity text.

    Token order is not significant because invoice layouts may reorder names.
    The normalized ``N/A`` sentinel matches any identity because no owner was
    available to verify.
    """
    if normalize_name(owner_name) == "n a":
        return True
    return set(_name_tokens(owner_name)) <= set(_name_tokens(identity_text))


def normalize_name(value: str) -> str:
    """Normalize a name for exact, case- and punctuation-insensitive comparison.

    Unicode compatibility normalization is applied before word tokens are joined
    with single spaces.
    """
    return " ".join(_name_tokens(value))


def _manifest_sheets(value: Any) -> list[ManifestSheet]:
    """Validate and convert a non-empty treatment-sheet declaration array.

    Entry positions are one-based in validation errors to match human-readable
    manifest numbering.
    """
    if not isinstance(value, list) or not value:
        raise ExtractionError("manifest treatmentSheets must be a non-empty array")
    return [_manifest_sheet(item, index) for index, item in enumerate(value, start=1)]


def _manifest_sheet(value: Any, index: int) -> ManifestSheet:
    """Validate and convert one indexed treatment-sheet declaration.

    Owner, cat name, and filename must be non-empty strings, and the filename is
    additionally restricted to a bare PDF name.
    """
    location = f"treatmentSheets[{index}]"
    if not isinstance(value, dict):
        raise ExtractionError(f"{location} must be an object")
    filename = _required_string(value, "fileName", location)
    _validate_filename(filename, location)
    return ManifestSheet(
        owner_name=_required_string(value, "owner", location),
        cat_name=_required_string(value, "catName", location),
        filename=filename,
    )


def _required_string(value: dict[str, Any], key: str, location: str) -> str:
    """Return a required manifest string after trimming surrounding whitespace.

    ``location`` and ``key`` are included in ``ExtractionError`` when the field
    is absent, non-string, or blank.
    """
    field = value.get(key)
    if not isinstance(field, str) or not field.strip():
        raise ExtractionError(f"{location}.{key} must be a non-empty string")
    return field.strip()


def _validate_filename(filename: str, location: str) -> None:
    """Require a bare PDF filename so input reads stay beside the manifest.

    Absolute paths, directory components, and non-PDF suffixes are rejected;
    source manifests cannot redirect extraction elsewhere on the filesystem.
    """
    path = PurePath(filename)
    if path.is_absolute() or len(path.parts) != 1 or path.suffix.casefold() != ".pdf":
        raise ExtractionError(f"{location}.fileName must be a bare PDF filename")


def _parse_date(value: str) -> datetime:
    """Parse a calendar date only when it uses canonical ``YYYY-MM-DD`` form.

    A format round trip rejects values that ``strptime`` accepts without the
    required zero padding.
    """
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ExtractionError("date and manifest date must use YYYY-MM-DD format") from exc
    if parsed.strftime("%Y-%m-%d") != value:
        raise ExtractionError("date and manifest date must use YYYY-MM-DD format")
    return parsed


def _extract_sheet(
    path: Path,
    sheet: ManifestSheet,
    date: str,
    parser: TreatmentSheetParser,
    sequence: int,
) -> CatRecord:
    """Parse one declared PDF and validate it against manifest identity.

    Missing files and parser failures are reported as ``ExtractionError`` with
    the manifest filename. The parsed record is returned only after owner and cat
    checks succeed.
    """
    if not path.is_file():
        raise ExtractionError(f"treatment sheet does not exist: {path}")
    try:
        record = parser(path, date=date, sequence=sequence)
    except TreatmentSheetError as exc:
        raise ExtractionError(f"{sheet.filename}: {exc}") from exc
    _validate_identity(sheet, record)
    return record


def _validate_identity(sheet: ManifestSheet, record: CatRecord) -> None:
    """Require parsed owner and cat identities to match one manifest entry.

    Owners use normalized exact equality. Cat names use contiguous token matching
    because the PDF display name may include annotations beyond the manifest name.
    """
    if normalize_name(sheet.owner_name) != normalize_name(record.owner_name):
        raise ExtractionError(
            f"{sheet.filename}: owner mismatch; manifest={sheet.owner_name!r}, "
            f"PDF={record.owner_name!r}"
        )
    if not contains_name(record.display_name, sheet.cat_name):
        raise ExtractionError(
            f"{sheet.filename}: cat mismatch; manifest={sheet.cat_name!r}, "
            f"PDF display name={record.display_name!r}"
        )


def _name_tokens(value: str) -> list[str]:
    """Produce case-folded Unicode word tokens for identity matching.

    NFKC normalization makes compatibility characters comparable, while token
    extraction intentionally discards punctuation and whitespace differences.
    """
    compatible = unicodedata.normalize("NFKC", value).casefold()
    return re.findall(r"[\w]+", compatible)
