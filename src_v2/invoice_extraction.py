"""Parse and validate invoice PDFs without creating pipeline output artifacts."""

import re
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime as DateTime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pdfplumber
from errors import PipelineError
from models_invoice import Invoice, InvoiceAppointment, InvoiceServiceLine
from pdfminer.pdfparser import PDFSyntaxError
from text_normalization import normalize_wrapped_text

MONEY_PATTERN = r"\$([\d,]+(?:\.\d{1,2})?)"
SERVICE_COST_PATTERN = r"\$([^\s]+)"
APPOINTMENT_START_PATTERN = re.compile(r"(?m)^(\d{1,2}/\d{1,2}/\d{4})\s+")
VISIT_DATE_PATTERN = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
ANIMAL_CELL_PATTERN = re.compile(r"(?P<name>.+?)\s+\((?P<reference>\d{2}-\d+)\)")
SUMMARY_PREFIXES = (
    "total of this appointment:",
    "total of this invoice:",
    "remainder for this invoice:",
    "total for all outstanding invoices:",
    "total of all non-subsidized animals:",
)


@dataclass(frozen=True)
class ExtractedInvoiceIdentity:
    """Store normalized identity columns extracted from one visual invoice row."""

    service_date: Date
    animal_display_name: str
    animal_reference: str
    owner_name: str

    @property
    def identity_text(self) -> str:
        """Return one comparison-friendly rendering of both source identity cells."""
        animal = f"{self.animal_display_name} ({self.animal_reference})"
        return f"{animal} {self.owner_name}"


def parse_invoice(invoice_path: Path) -> Invoice:
    """Read one absolute invoice PDF and return fully validated typed values."""
    _validate_invoice_path(invoice_path)
    text = _read_pdf_text(invoice_path)
    identities = _read_invoice_identities(invoice_path)
    appointments = _parse_appointments(text, identities)
    total_cost = _parse_invoice_total(text)
    _validate_invoice_total(appointments, total_cost)
    return Invoice(invoice_path, appointments, total_cost)


def _validate_invoice_path(value: object) -> None:
    """Require an existing absolute PDF before passing it to pdfplumber."""
    if not isinstance(value, Path) or not value.is_absolute():
        raise PipelineError("invoice extraction path must be an absolute Path")
    if value.suffix.casefold() != ".pdf" or not value.is_file():
        raise PipelineError(f"invoice extraction source must be an existing PDF: {value}")


def _read_pdf_text(invoice_path: Path) -> str:
    """Extract text from every page and translate expected PDF read failures."""
    try:
        with pdfplumber.open(invoice_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except (OSError, PDFSyntaxError, ValueError) as exc:
        raise PipelineError(f"could not read invoice {invoice_path}: {exc}") from exc
    if not text.strip():
        raise PipelineError("invoice contains no extractable text")
    return text


def _read_invoice_identities(invoice_path: Path) -> tuple[ExtractedInvoiceIdentity, ...]:
    """Extract normalized Animal and Owner cells from all invoice table pages."""
    try:
        with pdfplumber.open(invoice_path) as pdf:
            identities = tuple(
                identity for page in pdf.pages for identity in _page_identities(page)
            )
    except PipelineError:
        raise
    except (OSError, PDFSyntaxError, ValueError, IndexError) as exc:
        raise PipelineError(f"could not read invoice identities {invoice_path}: {exc}") from exc
    if not identities:
        raise PipelineError("invoice contains no structured appointment identities")
    return identities


def _page_identities(page: Any) -> tuple[ExtractedInvoiceIdentity, ...]:
    """Extract appointment identities from dynamically detected page columns."""
    words = page.extract_words()
    if not any(VISIT_DATE_PATTERN.fullmatch(str(word.get("text", ""))) for word in words):
        return ()
    header_top, animal_left, owner_left, species_left = _identity_column_boundaries(words)
    visits = _visit_rows(words, header_top, animal_left)
    return tuple(
        _cropped_identity(page, visits, index, animal_left, owner_left, species_left)
        for index in range(len(visits))
    )


def _identity_column_boundaries(
    words: list[dict[str, Any]],
) -> tuple[float, float, float, float]:
    """Find Animal, Owner, and Species column starts on one header row."""
    for animal in (word for word in words if word.get("text") == "Animal"):
        same_line = [word for word in words if abs(word["top"] - animal["top"]) < 1]
        owner = _word_named(same_line, "Owner")
        species = _word_named(same_line, "Species")
        if owner is not None and species is not None:
            return animal["top"], animal["x0"], owner["x0"], species["x0"]
    raise PipelineError("invoice identity column headers were not found")


def _word_named(words: list[dict[str, Any]], text: str) -> dict[str, Any] | None:
    """Return the first extracted word with an exact requested value."""
    return next((word for word in words if word.get("text") == text), None)


def _visit_rows(
    words: list[dict[str, Any]],
    header_top: float,
    animal_left: float,
) -> tuple[tuple[Date, float], ...]:
    """Return ordered visit dates and vertical starts below the table header."""
    visits = (
        (_parse_date(word["text"]), word["top"])
        for word in words
        if word["top"] > header_top
        and word["x0"] < animal_left
        and VISIT_DATE_PATTERN.fullmatch(word["text"])
    )
    return tuple(sorted(visits, key=lambda item: item[1]))


def _cropped_identity(
    page: Any,
    visits: tuple[tuple[Date, float], ...],
    index: int,
    animal_left: float,
    owner_left: float,
    species_left: float,
) -> ExtractedInvoiceIdentity:
    """Crop and parse one visual invoice row's Animal and Owner cells."""
    service_date, top = visits[index]
    bottom = visits[index + 1][1] if index + 1 < len(visits) else page.height
    animal_cell = _crop_text(page, animal_left, top - 1, owner_left - 1, bottom - 1)
    owner_name = _crop_text(page, owner_left, top - 1, species_left - 1, bottom - 1)
    animal_name, animal_reference = _parse_animal_cell(animal_cell)
    if not owner_name:
        raise PipelineError("invoice owner cell is blank")
    return ExtractedInvoiceIdentity(
        service_date,
        animal_name,
        animal_reference,
        owner_name,
    )


def _crop_text(page: Any, left: float, top: float, right: float, bottom: float) -> str:
    """Return normalized text from one coordinate-bounded invoice cell."""
    return normalize_wrapped_text(page.crop((left, top, right, bottom)).extract_text() or "")


def _parse_animal_cell(value: str) -> tuple[str, str]:
    """Separate a required trailing animal reference from its display name."""
    match = ANIMAL_CELL_PATTERN.fullmatch(value)
    if match is None:
        raise PipelineError(f"invoice animal cell is malformed: {value!r}")
    return match.group("name"), match.group("reference")


def _parse_appointments(
    text: str,
    identities: tuple[ExtractedInvoiceIdentity, ...],
) -> tuple[InvoiceAppointment, ...]:
    """Parse every line-anchored appointment block in invoice order."""
    starts = tuple(APPOINTMENT_START_PATTERN.finditer(text))
    if len(starts) != len(identities):
        raise PipelineError("invoice appointment rows and structured identities must align")
    appointments = tuple(
        _parse_appointment(_appointment_block(text, starts, index), identities[index])
        for index in range(len(starts))
    )
    if not appointments:
        raise PipelineError("invoice contains no appointments")
    return appointments


def _appointment_block(
    text: str,
    starts: tuple[re.Match[str], ...],
    index: int,
) -> str:
    """Return one appointment block ending at the next anchored visit date."""
    end = starts[index + 1].start() if index + 1 < len(starts) else len(text)
    return text[starts[index].start() : end]


def _parse_appointment(block: str, identity: ExtractedInvoiceIdentity) -> InvoiceAppointment:
    """Parse one visit and require its service costs to match its total."""
    header = re.search(
        rf"^(?P<date>\d{{1,2}}/\d{{1,2}}/\d{{4}})\s+"
        rf"(?P<identity>.*?)\s+Cat\s+\d+(?:\.\d+)?\s+"
        rf"(?:Female|Male|Unknown)\s+(?P<service>.+?)\s+{SERVICE_COST_PATTERN}",
        block,
        flags=re.DOTALL,
    )
    if header is None:
        raise PipelineError("invoice appointment header is malformed")
    services = [_service_line(header.group("service"), header.group(4))]
    services.extend(_remaining_service_lines(block[header.end() :]))
    total_cost = _parse_appointment_total(block)
    service_date = _parse_date(header.group("date"))
    if service_date != identity.service_date:
        raise PipelineError("invoice text date does not match its structured identity row")
    _validate_appointment_total(services, total_cost, identity.identity_text)
    return InvoiceAppointment(
        service_date=service_date,
        animal_display_name=identity.animal_display_name,
        animal_reference=identity.animal_reference,
        owner_name=identity.owner_name,
        identity_text=identity.identity_text,
        services=tuple(services),
        total_cost=total_cost,
    )


def _remaining_service_lines(text: str) -> list[InvoiceServiceLine]:
    """Parse later service rows and join wrapped description continuations."""
    results: list[InvoiceServiceLine] = []
    current: InvoiceServiceLine | None = None
    for line in text.splitlines():
        match = re.fullmatch(rf"([^$]+?)\s+{SERVICE_COST_PATTERN}\s*", line.strip())
        if match is not None and not _is_summary_line(match.group(1)):
            current = _service_line(match.group(1), match.group(2))
            results.append(current)
        elif current is not None and _is_description_continuation(line):
            current = InvoiceServiceLine(
                f"{current.name} {_single_line(line)}",
                current.cost,
            )
            results[-1] = current
        else:
            current = None
    return results


def _service_line(name: str, cost: str) -> InvoiceServiceLine:
    """Normalize one service description and parse its exact monetary cost."""
    return InvoiceServiceLine(_single_line(name), _parse_money(cost, "invoice service cost"))


def _is_description_continuation(line: str) -> bool:
    """Return whether plain text can continue the preceding service name."""
    value = _single_line(line)
    return (
        bool(value)
        and "$" not in value
        and not value.startswith("Visit Date ")
        and re.fullmatch(r"\(?\d+(?:-\d*)?\)?", value) is None
    )


def _is_summary_line(value: str) -> bool:
    """Return whether a priced row is a known invoice summary rather than a service."""
    return _single_line(value).casefold().startswith(SUMMARY_PREFIXES)


def _parse_appointment_total(block: str) -> Decimal:
    """Extract the required printed total for one appointment block."""
    match = re.search(rf"Total of this appointment:\s*{MONEY_PATTERN}", block)
    if match is None:
        raise PipelineError("invoice appointment total is missing")
    return _parse_money(match.group(1), "invoice appointment total")


def _parse_invoice_total(text: str) -> Decimal:
    """Collapse equal repeated page totals into one exact invoice total."""
    values = {
        _parse_money(value, "invoice total")
        for value in re.findall(rf"Total of this invoice:\s*{MONEY_PATTERN}", text)
    }
    if len(values) != 1:
        raise PipelineError("invoice total is missing or inconsistent")
    return values.pop()


def _validate_appointment_total(
    services: list[InvoiceServiceLine],
    total_cost: Decimal,
    identity: str,
) -> None:
    """Require one appointment's service sum to equal its printed total."""
    service_total = sum((service.cost for service in services), Decimal())
    if service_total != total_cost:
        raise PipelineError(f"service costs do not match appointment total for {identity!r}")


def _validate_invoice_total(
    appointments: tuple[InvoiceAppointment, ...],
    total_cost: Decimal,
) -> None:
    """Require all appointment totals to equal the printed invoice total."""
    appointment_total = sum((item.total_cost for item in appointments), Decimal())
    if appointment_total != total_cost:
        raise PipelineError("appointment totals do not match invoice total")


def _parse_money(value: str, field_name: str) -> Decimal:
    """Parse comma-separated money and reject invalid or over-precise tokens."""
    try:
        amount = Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise PipelineError(f"{field_name} is invalid: {value!r}") from exc
    if not amount.is_finite() or amount < 0 or amount.as_tuple().exponent < -2:
        raise PipelineError(f"{field_name} is invalid: {value!r}")
    return amount


def _parse_date(value: str) -> Date:
    """Convert a printed MM/DD/YYYY visit date into a calendar date."""
    try:
        return DateTime.strptime(value, "%m/%d/%Y").date()
    except ValueError as exc:
        raise PipelineError(f"invoice appointment date is invalid: {value!r}") from exc


def _single_line(value: str) -> str:
    """Collapse PDF line breaks and whitespace runs into one trimmed line."""
    return normalize_wrapped_text(value)
