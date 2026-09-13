"""Parse and validate a Nine Lives Foundation invoice without writing output."""

from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pdfplumber

from treatment_sheet_parser.nlf.errors import InvoiceError
from treatment_sheet_parser.nlf.models import Invoice, InvoiceAppointment, Service

MONEY = r"\$([\d,]+(?:\.\d{1,2})?)"
SERVICE_PRICE = r"\$([^\s]+)"


def parse_invoice(path: str | Path) -> Invoice:
    """Read an invoice PDF and validate its appointments and totals.

    Args:
        path: Nine Lives Foundation invoice PDF.

    Returns:
        Parsed visits in invoice order and the validated invoice total.

    Raises:
        InvoiceError: If the PDF cannot be read, has no visits, contains
            malformed values, or has inconsistent service or invoice totals.
    """
    invoice_path = Path(path).expanduser().resolve()
    try:
        with pdfplumber.open(invoice_path) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    except (OSError, IndexError) as exc:
        raise InvoiceError(f"could not read invoice {invoice_path}: {exc}") from exc
    appointments = tuple(_appointments(text))
    total_cost = _invoice_total(text)
    if not appointments:
        raise InvoiceError("invoice contains no appointments")
    _validate_invoice_total(appointments, total_cost)
    return Invoice(str(invoice_path), appointments, total_cost)


def _appointments(text: str) -> list[InvoiceAppointment]:
    """Parse every date-led visit row in an invoice.

    Appointment dates are independent; one invoice may contain visits from
    several dates or multiple animals on the same date. Only dates at the start
    of a line open a block, preventing footer or description dates from becoming
    false appointments.
    """
    # A visit begins only when a date is the first value on a line. This avoids
    # treating dates embedded in service descriptions or footer text as visits.
    starts = list(re.finditer(r"(?m)^(\d{1,2}/\d{1,2}/\d{4})\s+", text))
    blocks = [
        text[match.start() : _block_end(starts, index, text)] for index, match in enumerate(starts)
    ]
    return [_appointment(block) for block in blocks]


def _block_end(starts: list[re.Match[str]], index: int, text: str) -> int:
    """Return the exclusive end offset for one appointment block.

    The next date-led match starts the following block; the final appointment
    extends to the end of the extracted invoice text.
    """
    return starts[index + 1].start() if index + 1 < len(starts) else len(text)


def _appointment(block: str) -> InvoiceAppointment:
    """Parse and validate one visit block into a shared appointment model.

    Identity text is preserved for later manifest matching, wrapped service lines
    are collected, and service costs must agree with the printed appointment total.
    """
    # NLF prints a variable patient/owner description before stable columns for
    # species, weight, and gender. Preserve that description for later matching.
    header = re.search(
        rf"^(?P<date>\d{{1,2}}/\d{{1,2}}/\d{{4}})\s+(?P<identity>.*?)"
        rf"\s+Cat\s+\d+(?:\.\d+)?\s+(?:Female|Male|Unknown)\s+"
        rf"(?P<service>.+?)\s+{SERVICE_PRICE}",
        block,
        flags=re.DOTALL,
    )
    if not header:
        raise InvoiceError("invoice appointment header is malformed")
    services = [Service(_single_line(header.group("service")), _service_cost(header.group(4)))]
    services.extend(_remaining_services(block[header.end() :]))
    total_cost = _appointment_total(block)
    _validate_appointment_total(services, total_cost, header.group("identity"))
    return InvoiceAppointment(
        _date(header.group("date")),
        _single_line(header.group("identity")),
        tuple(services),
        total_cost,
    )


def _remaining_services(text: str) -> list[Service]:
    """Parse service rows following an appointment's first service.

    Monetary summary rows are excluded. Plain text immediately following a
    service row is treated as a wrapped continuation of that service name.
    """
    results: list[Service] = []
    current: Service | None = None
    for line in text.splitlines():
        match = re.fullmatch(rf"([^$]+?)\s+{SERVICE_PRICE}\s*", line.strip())
        if match and not _summary_line(_single_line(match.group(1))):
            current = Service(_single_line(match.group(1)), _service_cost(match.group(2)))
            results.append(current)
        elif current is not None and _is_name_continuation(line):
            # PDF extraction can place the end of a long service name on the
            # following line even though its price appeared on the first line.
            current = Service(f"{current.name} {_single_line(line)}", current.cost)
            results[-1] = current
        else:
            current = None
    return results


def _is_name_continuation(line: str) -> bool:
    """Return whether a line can continue the preceding service name.

    Empty lines, priced rows, visit headings, and standalone numeric identifiers
    terminate continuation handling.
    """
    value = _single_line(line)
    return (
        bool(value)
        and "$" not in value
        and not value.startswith("Visit Date ")
        and not re.fullmatch(r"\(?\d+(?:-\d*)?\)?", value)
    )


def _summary_line(name: str) -> bool:
    """Return whether a parsed name belongs to a known invoice summary row.

    Comparison is case-insensitive and prefix-based so the amount or trailing
    formatting does not affect classification.
    """
    lowered = name.casefold()
    return lowered.startswith(
        (
            "total of this appointment:",
            "total of this invoice:",
            "remainder for this invoice:",
            "total for all outstanding invoices:",
            "total of all non-subsidized animals:",
        )
    )


def _appointment_total(block: str) -> str:
    """Extract and normalize the required total from one appointment block.

    Raises ``InvoiceError`` when the labeled monetary value is absent.
    """
    match = re.search(rf"Total of this appointment:\s*{MONEY}", block)
    if not match:
        raise InvoiceError("invoice appointment total is missing")
    return _money(match.group(1))


def _invoice_total(text: str) -> str:
    """Extract one consistent invoice total from all repeated page footers.

    Repeated equal values collapse to one total. Missing footers or conflicting
    values raise ``InvoiceError``.
    """
    values = {_money(value) for value in re.findall(rf"Total of this invoice:\s*{MONEY}", text)}
    if len(values) != 1:
        raise InvoiceError("invoice total is missing or inconsistent")
    return values.pop()


def _validate_appointment_total(services: list[Service], total: str, identity: str) -> None:
    """Require numeric service costs to equal the printed appointment total.

    Validation is deferred when any service has a non-numeric cost because that
    value requires an explicit operator decision in the mapping layer.
    """
    costs = [_optional_decimal(item.cost) for item in services]
    # A non-numeric service value is resolved explicitly by the mapping layer.
    if None in costs:
        return
    if sum((cost for cost in costs if cost is not None), Decimal()) != _decimal(total):
        raise InvoiceError(
            f"service costs do not match appointment total for {_single_line(identity)!r}"
        )


def _validate_invoice_total(appointments: tuple[InvoiceAppointment, ...], total: str) -> None:
    """Require the sum of appointment totals to equal the invoice total exactly.

    Decimal arithmetic avoids binary floating-point rounding during comparison.
    """
    if sum((_decimal(item.total_cost) for item in appointments), Decimal()) != _decimal(total):
        raise InvoiceError("appointment totals do not match invoice total")


def _money(value: str) -> str:
    """Normalize comma-separated dollar text to a two-decimal string.

    Invalid numeric text is reported through ``InvoiceError`` by ``_decimal``.
    """
    return f"{_decimal(value):.2f}"


def _service_cost(value: str) -> str:
    """Normalize a numeric service price or preserve an odd token for review.

    Non-numeric values remain byte-for-byte unchanged so a later operator
    decision has the exact invoice content available.
    """
    parsed = _optional_decimal(value)
    return value if parsed is None else f"{parsed:.2f}"


def _optional_decimal(value: str) -> Decimal | None:
    """Parse comma-separated decimal text, returning ``None`` when invalid.

    This helper deliberately makes no error-or-skip policy decision for unusual
    service costs.
    """
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation:
        return None


def _decimal(value: str) -> Decimal:
    """Parse comma-separated decimal text into an exact ``Decimal`` value.

    ``InvalidOperation`` is translated to ``InvoiceError`` containing the source
    token.
    """
    try:
        return Decimal(value.replace(",", ""))
    except InvalidOperation as exc:
        raise InvoiceError(f"invalid invoice cost: {value!r}") from exc


def _date(value: str) -> str:
    """Convert a printed ``MM/DD/YYYY`` visit date to ISO format.

    Invalid calendar dates raise ``InvoiceError`` containing the printed value.
    """
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError as exc:
        raise InvoiceError(f"invalid invoice date: {value!r}") from exc


def _single_line(value: str) -> str:
    """Collapse PDF line breaks and whitespace runs into a single-line value.

    Leading and trailing whitespace is removed as part of splitting and joining.
    """
    return " ".join(value.split())
