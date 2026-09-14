"""Render complete parsed values for operator verification against source PDFs."""

import json
from dataclasses import asdict
from datetime import date as Date
from decimal import Decimal
from pathlib import Path
from typing import Any

from models_invoice import Invoice
from models_treatment_sheet import CatRecord


def print_invoice_extraction(invoice: Invoice) -> None:
    """Print every extracted invoice value in a stable review-friendly form."""
    _print_extraction("Invoice extraction", invoice)


def print_treatment_sheet_extraction(record: CatRecord, source_path: Path) -> None:
    """Print every value extracted from one identified treatment sheet."""
    _print_extraction(f"Treatment-sheet extraction: {source_path.name}", record)


def _print_extraction(heading: str, value: object) -> None:
    """Print a heading and JSON representation before interactive review begins."""
    print(f"\n{heading}")
    print(json.dumps(asdict(value), indent=2, default=_json_value))
    # Ensure the extraction is visible before the PDF viewer takes focus.
    print(flush=True)


def _json_value(value: Any) -> str:
    """Convert supported typed model values into unambiguous JSON strings."""
    if isinstance(value, (Date, Path)):
        return value.isoformat() if isinstance(value, Date) else str(value)
    if isinstance(value, Decimal):
        return f"{value:.2f}"
    raise TypeError(f"unsupported extraction review value: {type(value).__name__}")
