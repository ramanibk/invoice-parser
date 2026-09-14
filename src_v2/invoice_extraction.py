"""Parse and validate invoice PDFs without creating pipeline output artifacts."""

import re
from datetime import date as Date
from datetime import datetime as DateTime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber
from errors import PipelineError
from models_invoice import Invoice, InvoiceAppointment, InvoiceServiceLine
from pdfminer.pdfparser import PDFSyntaxError

MONEY_PATTERN = r"\$([\d,]+(?:\.\d{1,2})?)"
SERVICE_COST_PATTERN = r"\$([^\s]+)"
APPOINTMENT_START_PATTERN = re.compile(r"(?m)^(\d{1,2}/\d{1,2}/\d{4})\s+")
SUMMARY_PREFIXES = (
    "total of this appointment:",
    "total of this invoice:",
    "remainder for this invoice:",
    "total for all outstanding invoices:",
    "total of all non-subsidized animals:",
)


def parse_invoice(invoice_path: Path) -> Invoice:
    """Read one absolute invoice PDF and return fully validated typed values."""
    _validate_invoice_path(invoice_path)
    text = _read_pdf_text(invoice_path)
    appointments = _parse_appointments(text)
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


def _parse_appointments(text: str) -> tuple[InvoiceAppointment, ...]:
    """Parse every line-anchored appointment block in invoice order."""
    starts = tuple(APPOINTMENT_START_PATTERN.finditer(text))
    appointments = tuple(
        _parse_appointment(_appointment_block(text, starts, index)) for index in range(len(starts))
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


def _parse_appointment(block: str) -> InvoiceAppointment:
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
    identity = _single_line(header.group("identity"))
    _validate_appointment_total(services, total_cost, identity)
    return InvoiceAppointment(
        service_date=_parse_date(header.group("date")),
        identity_text=identity,
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
    return " ".join(value.split())
