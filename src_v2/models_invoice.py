"""Represent validated invoice values with exact monetary amounts."""

from dataclasses import dataclass
from datetime import date as Date
from decimal import Decimal
from pathlib import Path

from errors import PipelineError
from models_validation import (
    _require_date,
    _require_money,
    _require_non_empty_tuple_of,
    _require_text,
)

SUPPORTED_CURRENCY = "USD"


@dataclass(frozen=True)
class InvoiceServiceLine:
    """Store one invoiced service description and its exact cost."""

    name: str
    cost: Decimal

    def __post_init__(self) -> None:
        """Require a named service with a valid nonnegative monetary cost."""
        _require_text(self.name, "invoice service name")
        _require_money(self.cost, "invoice service cost")


@dataclass(frozen=True)
class InvoiceAppointment:
    """Store one dated invoice visit and its ordered service lines."""

    service_date: Date
    identity_text: str
    services: tuple[InvoiceServiceLine, ...]
    total_cost: Decimal

    def __post_init__(self) -> None:
        """Validate the visit identity, service collection, and printed total."""
        _require_date(self.service_date, "invoice appointment service date")
        _require_text(self.identity_text, "invoice appointment identity")
        _require_non_empty_tuple_of(
            self.services, InvoiceServiceLine, "invoice appointment services"
        )
        _require_money(self.total_cost, "invoice appointment total")


@dataclass(frozen=True)
class Invoice:
    """Store a complete parsed invoice without performing filesystem I/O."""

    source_file: Path
    appointments: tuple[InvoiceAppointment, ...]
    total_cost: Decimal
    currency: str = SUPPORTED_CURRENCY

    def __post_init__(self) -> None:
        """Validate the source identity, appointments, total, and currency."""
        _validate_source_file(self.source_file)
        _require_non_empty_tuple_of(self.appointments, InvoiceAppointment, "invoice appointments")
        _require_money(self.total_cost, "invoice total")
        if self.currency != SUPPORTED_CURRENCY:
            raise PipelineError(f"invoice currency must be {SUPPORTED_CURRENCY}")


def _validate_source_file(value: object) -> None:
    """Require an absolute PDF path without reading or changing the source file."""
    if not isinstance(value, Path):
        raise PipelineError("invoice source file must be a Path")
    if not value.is_absolute() or value.suffix.casefold() != ".pdf":
        raise PipelineError("invoice source file must be an absolute PDF path")
