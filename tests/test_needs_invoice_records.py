"""Tests for Needs Invoice record validation, joining, and normalization."""

from datetime import date

import pytest

from treatment_sheet_parser.needs_invoice.errors import AirtableQueryError
from treatment_sheet_parser.needs_invoice.records import build_cat_payloads
from treatment_sheet_parser.needs_invoice.validation import (
    index_appointments_by_cat,
    validate_cats,
    validate_linked_displays,
)


def _appointment(record_id: str, cat_id: str) -> dict[str, object]:
    """Build one minimal valid appointment record for adapter tests."""
    return {
        "id": record_id,
        "fields": {
            "Date": "2026-09-03",
            "Location": "Nine Lives Foundation",
            "Cats": [cat_id],
            "Type": "Pet",
            "Services": ["Spay / Neuter"],
            "Cost": 125,
        },
    }


def test_build_cat_payloads_joins_and_normalizes_records() -> None:
    """Join a cat to its appointment and produce comparison-ready values."""
    appointment = _appointment("recAppointment1", "recCat1")
    cat = {
        "id": "recCat1",
        "fields": {"Cat Name": "Miso", "Color": ["Black", "White"]},
    }

    payloads = build_cat_payloads(
        ("recCat1",),
        (cat,),
        {"recCat1": appointment},
        {"recAppointment1": {}},
        {"recCat1": {}},
    )

    assert payloads[0]["airtable_appointment_id"] == "recAppointment1"
    assert payloads[0]["cat_name"] == "Miso"
    assert payloads[0]["color"] == "Black / White"
    assert payloads[0]["services"] == ["Spay / Neuter"]
    assert payloads[0]["total_cost"] == "125.00"


def test_validate_cats_rejects_malformed_fields() -> None:
    """Reject malformed cat data before any payload is built."""
    with pytest.raises(AirtableQueryError, match="invalid Cat Name"):
        validate_cats(({"id": "recCat1", "fields": {"Cat Name": ""}},))


def test_index_appointments_rejects_ambiguous_cat_identity() -> None:
    """Reject a cat linked to more than one appointment in the query scope."""
    appointments = (
        _appointment("recAppointment1", "recCat1"),
        _appointment("recAppointment2", "recCat1"),
    )
    with pytest.raises(AirtableQueryError, match="multiple appointments"):
        index_appointments_by_cat(appointments, date(2026, 9, 3))


def test_index_appointments_ignores_repeated_link_in_one_appointment() -> None:
    """Treat a repeated link within one appointment as one cat relationship."""
    appointment = _appointment("recAppointment1", "recCat1")
    appointment["fields"]["Cats"] = ["recCat1", "recCat1"]

    indexed = index_appointments_by_cat((appointment,), date(2026, 9, 3))

    assert indexed == {"recCat1": appointment}


def test_build_cat_payloads_retains_voucher_ids_and_numbers() -> None:
    """Keep comparison labels together with identities needed for later updates."""
    appointment = _appointment("recAppointment1", "recCat1")
    cat = {
        "id": "recCat1",
        "fields": {
            "Cat Name": "Miso",
            "Voucher": ["recVoucher1", "recVoucher2"],
        },
    }

    payload = build_cat_payloads(
        ("recCat1",),
        (cat,),
        {"recCat1": appointment},
        {"recAppointment1": {}},
        {"recCat1": {"Voucher": "V-1001, V-1002"}},
    )[0]

    assert payload["voucher_numbers"] == ["V-1001", "V-1002"]
    assert payload["vouchers"] == [
        {"airtable_voucher_id": "recVoucher1", "voucher_number": "V-1001"},
        {"airtable_voucher_id": "recVoucher2", "voucher_number": "V-1002"},
    ]


def test_validate_linked_displays_rejects_missing_voucher_label() -> None:
    """Reject display results that cannot represent every linked voucher identity."""
    cat = {
        "id": "recCat1",
        "fields": {
            "Cat Name": "Miso",
            "Voucher": ["recVoucher1", "recVoucher2"],
        },
    }

    with pytest.raises(AirtableQueryError, match="invalid Voucher display values"):
        validate_linked_displays(
            (),
            (cat,),
            {},
            {"recCat1": {"Voucher": "V-1001"}},
        )
