"""Represent validated invoice values with exact monetary amounts."""

import re
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
# Animal references are part of the invoice's printed identity and must retain
# the clinic's two-digit year prefix plus its variable-length sequence.
ANIMAL_REFERENCE_PATTERN = re.compile(r"\d{2}-\d+")


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
    animal_display_name: str
    animal_reference: str
    owner_name: str
    identity_text: str
    services: tuple[InvoiceServiceLine, ...]
    total_cost: Decimal

    def __post_init__(self) -> None:
        """Validate the visit identities, service collection, and printed total."""
        _require_date(self.service_date, "invoice appointment service date")
        _require_text(self.animal_display_name, "invoice animal display name")
        _require_text(self.animal_reference, "invoice animal reference")
        if ANIMAL_REFERENCE_PATTERN.fullmatch(self.animal_reference) is None:
            raise PipelineError("invoice animal reference must use NN-N format")
        _require_text(self.owner_name, "invoice owner name")
        _require_text(self.identity_text, "invoice appointment identity")
        # Preserve source order because later service aggregation uses first-seen
        # order when multiple invoice descriptions map to one Airtable option.
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
        # Models validate path identity only; parser and preflight layers own
        # existence and readability checks to keep model construction side-effect free.
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
