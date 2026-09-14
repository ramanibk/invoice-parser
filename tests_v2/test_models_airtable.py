"""Tests for normalized read-only Airtable models."""

from datetime import date, datetime
from decimal import Decimal

import pytest
from errors import PipelineError
from models_airtable import AirtableCatRecord, AirtableSnapshot, AirtableVoucher


def _cat(**changes: object) -> AirtableCatRecord:
    """Build a valid normalized Airtable cat with selected overrides."""
    values = {
        "airtable_cat_id": "recCat1",
        "airtable_appointment_id": "recAppointment1",
        "cat_name": "Miso",
        "appointment_type": "Pet",
    }
    values.update(changes)
    return AirtableCatRecord(**values)  # type: ignore[arg-type]


def _snapshot(**changes: object) -> AirtableSnapshot:
    """Build a valid Airtable snapshot with selected overrides."""
    cat_records = (_cat(),)
    values = {
        "service_date": date(2026, 9, 3),
        "location_code": "NLF",
        "location_name": "Nine Lives Foundation",
        "appointment_record_count": 1,
        "cat_records": cat_records,
    }
    values.update(changes)
    return AirtableSnapshot(**values)  # type: ignore[arg-type]


def test_voucher_preserves_linked_identity_and_display_number() -> None:
    """Retain both voucher values required by later processing."""
    voucher = AirtableVoucher("recVoucher1", "V-1001")

    assert voucher.airtable_voucher_id == "recVoucher1"
    assert voucher.voucher_number == "V-1001"


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (("voucher1", "V-1001"), "voucher ID must be an Airtable record ID"),
        (("recVoucher1", ""), "voucher number must be non-empty"),
    ],
)
def test_rejects_malformed_voucher(values: tuple[str, str], message: str) -> None:
    """Reject malformed voucher identities and missing display values."""
    with pytest.raises(PipelineError, match=message):
        AirtableVoucher(*values)


def test_cat_record_preserves_normalized_comparison_values() -> None:
    """Retain ordered services, linked vouchers, and an exact optional total."""
    voucher = AirtableVoucher("recVoucher1", "V-1001")
    record = _cat(
        appointment_owner_or_trapper="Kate Castillo",
        gender="Female",
        microchip_number="981020000000001",
        color="Black / White",
        vouchers=(voucher,),
        services=("Spay / Neuter", "Exam"),
        total_cost=Decimal("250.00"),
    )

    assert record.services == ("Spay / Neuter", "Exam")
    assert record.vouchers == (voucher,)
    assert record.total_cost == Decimal("250.00")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"airtable_cat_id": "cat1"}, "cat ID must be an Airtable record ID"),
        ({"airtable_appointment_id": "appointment1"}, "appointment ID must be"),
        ({"cat_name": ""}, "cat name must be non-empty"),
        ({"appointment_type": None}, "appointment type must be a string"),
        ({"gender": ""}, "gender must be non-empty"),
        ({"microchip_number": "Already chipped"}, "microchip number must contain"),
        ({"vouchers": []}, "vouchers must be a tuple"),
        ({"vouchers": ("V-1001",)}, "must contain only AirtableVoucher values"),
        ({"services": []}, "services must be a tuple"),
        ({"services": ("",)}, "service name must be non-empty"),
        ({"services": ("Exam", "Exam")}, "service names must be unique"),
        ({"total_cost": 250.0}, "appointment total must be a Decimal"),
    ],
)
def test_rejects_malformed_cat_record(changes: dict[str, object], message: str) -> None:
    """Reject invalid identities, comparison values, links, and monetary totals."""
    with pytest.raises(PipelineError, match=message):
        _cat(**changes)


def test_rejects_duplicate_voucher_identity() -> None:
    """Reject two voucher values referring to the same linked Airtable record."""
    vouchers = (
        AirtableVoucher("recVoucher1", "V-1001"),
        AirtableVoucher("recVoucher1", "V-1002"),
    )

    with pytest.raises(PipelineError, match="voucher IDs must be unique"):
        _cat(vouchers=vouchers)


def test_snapshot_derives_linked_cat_count_from_records() -> None:
    """Derive the linked cat count instead of storing redundant state."""
    records = (
        _cat(),
        _cat(airtable_cat_id="recCat2"),
    )
    snapshot = _snapshot(appointment_record_count=1, cat_records=records)

    assert snapshot.appointment_record_count == 1
    assert snapshot.cat_records == records
    assert snapshot.linked_cat_count == 2


def test_snapshot_accepts_appointments_without_linked_cats() -> None:
    """Allow source appointments that do not have linked cat records."""
    snapshot = _snapshot(appointment_record_count=2, cat_records=())

    assert snapshot.appointment_record_count == 2
    assert snapshot.linked_cat_count == 0


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"service_date": datetime(2026, 9, 3)}, "service date must be a date"),
        ({"location_code": "AMC"}, "location must be NLF"),
        ({"location_name": "NLF"}, "location must be NLF"),
        (
            {"appointment_record_count": True},
            "appointment record count must be a nonnegative integer",
        ),
        (
            {"appointment_record_count": -1},
            "appointment record count must be a nonnegative integer",
        ),
        ({"cat_records": []}, "snapshot cat records must be a tuple"),
        ({"cat_records": ("Miso",)}, "must contain only AirtableCatRecord values"),
    ],
)
def test_rejects_malformed_snapshot(changes: dict[str, object], message: str) -> None:
    """Reject invalid scope identity, counts, and normalized cat collections."""
    with pytest.raises(PipelineError, match=message):
        _snapshot(**changes)


def test_rejects_more_represented_appointments_than_source_records() -> None:
    """Reject source counts smaller than distinct represented appointments."""
    cat_records = (
        _cat(),
        _cat(airtable_cat_id="recCat2", airtable_appointment_id="recAppointment2"),
    )

    with pytest.raises(PipelineError, match="smaller than represented appointments"):
        _snapshot(appointment_record_count=1, cat_records=cat_records)


def test_rejects_duplicate_cat_identity() -> None:
    """Reject a frozen query scope containing the same linked cat twice."""
    cat_records = (_cat(), _cat())

    with pytest.raises(PipelineError, match="snapshot cat IDs must be unique"):
        _snapshot(appointment_record_count=1, cat_records=cat_records)
