"""Tests for complete Airtable record joining and snapshot normalization."""

from datetime import date
from decimal import Decimal

import airtable_snapshot
import pytest
from airtable_snapshot import query_airtable_snapshot
from errors import PipelineError
from pipeline_config import AirtableConfig


def _config() -> AirtableConfig:
    """Return valid non-production Airtable identifiers."""
    return AirtableConfig("secret", "appBase1", "tblAppointments1", "tblCats1")


def _appointment(record_id: str = "recAppointment1") -> dict[str, object]:
    """Build one complete typed appointment record."""
    return {
        "id": record_id,
        "fields": {
            "Date": "2026-09-03",
            "Location": "Nine Lives Foundation",
            "Cats": ["recCat1"],
            "Type": "Pet",
            "Owner or Trapper": ["recOwner1"],
            "Services": ["Spay / Neuter"],
            "Additional Services": ["Rabies"],
            "Cost": 125.0,
        },
    }


def test_builds_complete_normalized_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """Join typed and displayed records while preserving exact Airtable identities."""
    monkeypatch.setattr(
        airtable_snapshot, "query_appointments", lambda *_args, **_kwargs: (_appointment(),)
    )

    def records(_config, table_id, _ids, fields, **kwargs):
        """Return the requested typed or displayed record projection."""
        if table_id == "tblAppointments1":
            return ({"id": "recAppointment1", "fields": {"Owner or Trapper": "Alex"}},)
        if kwargs.get("cell_format") == "string":
            return ({"id": "recCat1", "fields": {"Voucher": "V-1"}},)
        return (
            {
                "id": "recCat1",
                "fields": {
                    "Cat Name": "Luna",
                    "Gender": "Female",
                    "Microchip": 123456789,
                    "Color": ["Grey", "White"],
                    "Voucher": ["recVoucher1"],
                },
            },
        )

    monkeypatch.setattr(airtable_snapshot, "query_records_by_ids", records)

    snapshot = query_airtable_snapshot(_config(), date(2026, 9, 3))

    cat = snapshot.cat_records[0]
    assert cat.cat_name == "Luna"
    assert cat.appointment_owner_or_trapper == "Alex"
    assert cat.microchip_number == "123456789"
    assert cat.color == "Grey / White"
    assert cat.services == ("Spay / Neuter", "Rabies")
    assert cat.total_cost == Decimal("125.0")
    assert cat.vouchers[0].voucher_number == "V-1"


def test_rejects_cat_linked_to_multiple_appointments(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject an ambiguous Airtable cat before fetching its Cat-table fields."""
    appointments = (_appointment("recAppointment1"), _appointment("recAppointment2"))
    monkeypatch.setattr(
        airtable_snapshot, "query_appointments", lambda *_args, **_kwargs: appointments
    )

    with pytest.raises(PipelineError, match="multiple scoped appointments"):
        query_airtable_snapshot(_config(), date(2026, 9, 3))
