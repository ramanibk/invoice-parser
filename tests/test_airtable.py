"""Tests for the read-only Airtable Needs_Invoice query."""

from __future__ import annotations

import io
import json
from datetime import date
from urllib.parse import parse_qs, urlparse

import pytest

from treatment_sheet_parser.needs_invoice import cli as needs_invoice_cli
from treatment_sheet_parser.needs_invoice.airtable import AirtableClient
from treatment_sheet_parser.needs_invoice.errors import AirtableQueryError
from treatment_sheet_parser.needs_invoice.output import save_needs_invoice
from treatment_sheet_parser.needs_invoice.query import (
    NeedsInvoiceResult,
    query_needs_invoice,
    resolve_location,
    result_document,
)

BASE_ID = "appTestBase"
APPOINTMENTS_TABLE_ID = "tblAppointments"
CATS_TABLE_ID = "tblCats"


class Response(io.BytesIO):
    """Context-managed byte response used by the fake transport."""

    def __enter__(self):
        """Return this response when entering the transport context."""
        return self

    def __exit__(self, *args):
        """Close the response without suppressing an active exception."""
        self.close()


def _response(document: object) -> Response:
    """Encode one fake Airtable JSON response."""
    return Response(json.dumps(document).encode())


def _query(transport, appointment_date: date, location: str) -> NeedsInvoiceResult:
    """Query fake Airtable tables with explicit non-production identifiers."""
    return query_needs_invoice(
        AirtableClient("secret", base_id=BASE_ID, transport=transport),
        appointment_date,
        location,
        appointments_table_id=APPOINTMENTS_TABLE_ID,
        cats_table_id=CATS_TABLE_ID,
    )


def test_query_paginates_and_counts_unique_linked_cats() -> None:
    """Return every matching record and count unique linked cats."""
    requests = []
    pages = iter(
        [
            {
                "records": [
                    {
                        "id": "recAppointment1",
                        "fields": {
                            "Date": "2026-09-03",
                            "Location": "Nine Lives Foundation",
                            "Cats": ["recCat1", "recCat2"],
                            "Type": "Pet",
                            "Owner or Trapper": ["recOwner1"],
                            "Cat Address": "123 Main St",
                            "Cat City": "San Jose",
                            "Services": ["Spay / Neuter"],
                            "Additional Services": ["Exam"],
                            "Cost": 250,
                        },
                    }
                ],
                "offset": "next/page",
            },
            {
                "records": [
                    {
                        "id": "recAppointment2",
                        "fields": {
                            "Date": "2026-09-03",
                            "Location": "Nine Lives Foundation",
                            "Cats": ["recCat3"],
                            "Type": "TNR",
                            "Services": ["Rabies"],
                            "Cost": 20,
                        },
                    },
                    {
                        "id": "recAppointment3",
                        "fields": {
                            "Date": "2026-09-03",
                            "Location": "Nine Lives Foundation",
                            "Type": "Pet",
                        },
                    },
                ]
            },
            {
                "records": [
                    {
                        "id": "recAppointment1",
                        "fields": {"Owner or Trapper": "Kate Castillo"},
                    },
                    {"id": "recAppointment2", "fields": {}},
                    {"id": "recAppointment3", "fields": {}},
                ]
            },
            {
                "records": [
                    {
                        "id": "recCat1",
                        "fields": {
                            "Cat Name": "Miso",
                            "Microchip": 981020000000001,
                            "Color": ["Black"],
                            "Gender": "Female",
                            "Trapper or Owner": ["recOwner1"],
                            "Address": "123 Main St",
                            "Age": "Adult",
                            "Ear Tip": "None",
                            "Voucher": ["recVoucher1"],
                        },
                    },
                    {
                        "id": "recCat2",
                        "fields": {
                            "Cat Name": "Mochi",
                            "Color": ["White", "Black"],
                            "Gender": "Male",
                            "Age": "Juvenile",
                            "Ear Tip": "Yes - Left",
                        },
                    },
                    {
                        "id": "recCat3",
                        "fields": {"Cat Name": "Taro"},
                    },
                ]
            },
            {
                "records": [
                    {
                        "id": "recCat1",
                        "fields": {
                            "Trapper or Owner": "Kate Castillo",
                            "Voucher": "V-1001",
                        },
                    },
                    {"id": "recCat2", "fields": {}},
                    {"id": "recCat3", "fields": {}},
                ]
            },
        ]
    )

    def transport(request):
        """Capture one request and return the next prepared Airtable page."""
        requests.append(request)
        return _response(next(pages))

    result = _query(transport, date(2026, 9, 3), "nlf")

    assert result_document(result) == {
        "date": "2026-09-03",
        "location_code": "NLF",
        "location": "Nine Lives Foundation",
        "total_records": 3,
        "linked_cats": 3,
        "cats": [
            {
                "airtable_cat_id": "recCat1",
                "airtable_appointment_id": "recAppointment1",
                "cat_name": "Miso",
                "appointment_type": "Pet",
                "appointment_owner_or_trapper": "Kate Castillo",
                "appointment_cat_address": "123 Main St",
                "appointment_cat_city": "San Jose",
                "cat_owner_or_trapper": "Kate Castillo",
                "cat_address": "123 Main St",
                "gender": "Female",
                "microchip_number": "981020000000001",
                "color": "Black",
                "weight": None,
                "age": "Adult",
                "ear_tip": "None",
                "voucher_numbers": ["V-1001"],
                "vouchers": [
                    {
                        "airtable_voucher_id": "recVoucher1",
                        "voucher_number": "V-1001",
                    }
                ],
                "services": ["Spay / Neuter", "Exam"],
                "total_cost": "250.00",
            },
            {
                "airtable_cat_id": "recCat2",
                "airtable_appointment_id": "recAppointment1",
                "cat_name": "Mochi",
                "appointment_type": "Pet",
                "appointment_owner_or_trapper": "Kate Castillo",
                "appointment_cat_address": "123 Main St",
                "appointment_cat_city": "San Jose",
                "cat_owner_or_trapper": None,
                "cat_address": None,
                "gender": "Male",
                "microchip_number": None,
                "color": "White / Black",
                "weight": None,
                "age": "Juvenile",
                "ear_tip": "Yes - Left",
                "voucher_numbers": [],
                "vouchers": [],
                "services": ["Spay / Neuter", "Exam"],
                "total_cost": "250.00",
            },
            {
                "airtable_cat_id": "recCat3",
                "airtable_appointment_id": "recAppointment2",
                "cat_name": "Taro",
                "appointment_type": "TNR",
                "appointment_owner_or_trapper": None,
                "appointment_cat_address": None,
                "appointment_cat_city": None,
                "cat_owner_or_trapper": None,
                "cat_address": None,
                "gender": None,
                "microchip_number": None,
                "color": None,
                "weight": None,
                "age": None,
                "ear_tip": None,
                "voucher_numbers": [],
                "vouchers": [],
                "services": ["Rabies"],
                "total_cost": "20.00",
            },
        ],
    }
    assert all(request.method == "GET" for request in requests)
    assert all(request.get_header("Authorization") == "Bearer secret" for request in requests)
    assert parse_qs(urlparse(requests[1].full_url).query)["offset"] == ["next/page"]
    display_parameters = parse_qs(urlparse(requests[2].full_url).query)
    assert display_parameters["cellFormat"] == ["string"]
    assert display_parameters["userLocale"] == ["en-us"]
    assert display_parameters["timeZone"] == ["America/Los_Angeles"]
    cat_request = urlparse(requests[3].full_url)
    assert f"/{CATS_TABLE_ID}?" in requests[3].full_url
    assert "RECORD_ID()='recCat1'" in parse_qs(cat_request.query)["filterByFormula"][0]


def test_query_sends_exact_date_and_resolved_location() -> None:
    """Filter Airtable by the requested date and full location name."""
    captured = []

    def transport(request):
        """Capture the scoped Airtable request and return an empty result page."""
        captured.append(request)
        return _response({"records": []})

    _query(transport, date(2026, 9, 3), "NLF")
    parameters = parse_qs(urlparse(captured[0].full_url).query)

    formula = parameters["filterByFormula"][0]
    assert "IS_SAME({Date},DATETIME_PARSE('2026-09-03'),'day')" in formula
    assert "{Location}='Nine Lives Foundation'" in formula
    assert "{Cost}=BLANK()" in formula
    assert parameters["fields[]"] == [
        "Date",
        "Location",
        "Cats",
        "Type",
        "Owner or Trapper",
        "Cat Address",
        "Cat City",
        "Services",
        "Additional Services",
        "Cost",
    ]


def test_resolve_location_rejects_unknown_abbreviation() -> None:
    """Do not guess a full Airtable value for an unknown clinic code."""
    with pytest.raises(AirtableQueryError, match="unknown location code.*AMC, NLF"):
        resolve_location("XYZ")


@pytest.mark.parametrize(
    "record,error",
    [
        ({"id": "recBad", "fields": {"Location": "NLF"}}, "invalid Date"),
        (
            {"id": "wrongIdentity", "fields": {"Date": "2026-09-03", "Location": "NLF"}},
            "invalid identity",
        ),
        (
            {
                "id": "recBadCats",
                "fields": {"Date": "2026-09-03", "Location": "NLF", "Cats": ["bad"]},
            },
            "invalid Cats links",
        ),
        (
            {
                "id": "recBadType",
                "fields": {"Date": "2026-09-03", "Location": "NLF", "Type": "Other"},
            },
            "invalid Type",
        ),
    ],
)
def test_query_rejects_malformed_records(record, error) -> None:
    """Reject missing fields and mismatched Airtable record identities."""
    with pytest.raises(AirtableQueryError, match=error):
        _query(lambda request: _response({"records": [record]}), date(2026, 9, 3), "NLF")


def test_query_rejects_record_outside_requested_identity() -> None:
    """Reject a record whose date or location differs from the requested scope."""
    record = {
        "id": "recWrongLocation",
        "fields": {
            "Date": "2026-09-03",
            "Location": "Animal Medical Center",
            "Cats": [],
            "Type": "Pet",
        },
    }
    with pytest.raises(AirtableQueryError, match="does not match the requested"):
        _query(lambda request: _response({"records": [record]}), date(2026, 9, 3), "NLF")


@pytest.mark.parametrize(
    "cat_record,error",
    [
        ({"id": "recDifferent", "fields": {"Cat Name": "Miso"}}, "does not match requested"),
        ({"id": "recCat1", "fields": {}}, "invalid Cat Name"),
        (
            {"id": "recCat1", "fields": {"Cat Name": "Miso", "Age": "Senior"}},
            "invalid Age",
        ),
    ],
)
def test_query_rejects_malformed_or_mismatched_cat(cat_record, error) -> None:
    """Reject malformed Cat fields and identities outside the appointment links."""
    appointment = {
        "id": "recAppointment1",
        "fields": {
            "Date": "2026-09-03",
            "Location": "Nine Lives Foundation",
            "Cats": ["recCat1"],
            "Type": "Pet",
        },
    }
    pages = iter(
        [
            {"records": [appointment]},
            {"records": [{"id": "recAppointment1", "fields": {}}]},
            {"records": [cat_record]},
        ]
    )

    with pytest.raises(AirtableQueryError, match=error):
        _query(lambda request: _response(next(pages)), date(2026, 9, 3), "NLF")


def test_query_rejects_link_without_display_value() -> None:
    """Reject linked owner evidence that cannot be represented for matching."""
    appointment = {
        "id": "recAppointment1",
        "fields": {
            "Date": "2026-09-03",
            "Location": "Nine Lives Foundation",
            "Cats": ["recCat1"],
            "Type": "Pet",
            "Owner or Trapper": ["recOwner1"],
        },
    }
    pages = iter(
        [
            {"records": [appointment]},
            {"records": [{"id": "recAppointment1", "fields": {}}]},
            {"records": [{"id": "recCat1", "fields": {"Cat Name": "Miso"}}]},
            {"records": [{"id": "recCat1", "fields": {}}]},
        ]
    )

    with pytest.raises(AirtableQueryError, match="inconsistent Owner or Trapper links"):
        _query(lambda request: _response(next(pages)), date(2026, 9, 3), "NLF")


def test_query_rejects_missing_token_before_transport() -> None:
    """Do not issue a request without explicit Airtable credentials."""
    with pytest.raises(AirtableQueryError, match="AIRTABLE_TOKEN"):
        AirtableClient(
            "", base_id=BASE_ID, transport=lambda request: pytest.fail("transport called")
        )


@pytest.mark.parametrize("missing_name", needs_invoice_cli.AIRTABLE_ID_ENVIRONMENT.values())
def test_cli_rejects_missing_airtable_identifier(missing_name, monkeypatch) -> None:
    """Require every Airtable identifier unless its CLI override is provided."""
    for environment_name in needs_invoice_cli.AIRTABLE_ID_ENVIRONMENT.values():
        monkeypatch.setenv(environment_name, "configured")
    monkeypatch.delenv(missing_name)
    arguments = needs_invoice_cli._argument_parser().parse_args(["2026-09-03", "NLF"])

    with pytest.raises(AirtableQueryError, match=missing_name):
        needs_invoice_cli._airtable_identifiers(arguments)


def test_save_needs_invoice_creates_sequenced_snapshot(tmp_path) -> None:
    """Save complete snapshots without overwriting an earlier run."""
    result = NeedsInvoiceResult(
        date="2026-09-03",
        location_code="NLF",
        location="Nine Lives Foundation",
        total_records=0,
        linked_cats=0,
        cats=(),
    )

    first = save_needs_invoice(result, tmp_path / "outputs")
    second = save_needs_invoice(result, tmp_path / "outputs")

    assert first == tmp_path / "outputs" / "26SEP03-NLF" / "needs_invoice.json"
    assert second.parent.name == "26SEP03-NLF.1"
    assert json.loads(first.read_text()) == result_document(result)


def test_save_needs_invoice_shares_existing_extraction_directory(tmp_path) -> None:
    """Place the Airtable snapshot beside extraction for the same run."""
    run_directory = tmp_path / "outputs" / "26SEP03-NLF"
    run_directory.mkdir(parents=True)
    (run_directory / "extraction.json").write_text("{}\n")
    result = NeedsInvoiceResult("2026-09-03", "NLF", "Nine Lives Foundation", 0, 0, ())

    output = save_needs_invoice(result, tmp_path / "outputs")

    assert output == run_directory / "needs_invoice.json"
    assert (run_directory / "extraction.json").read_text() == "{}\n"


def test_save_needs_invoice_reports_output_directory_failure(tmp_path) -> None:
    """Reject an unwritable output target without leaving a run artifact."""
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory")
    result = NeedsInvoiceResult("2026-09-03", "NLF", "Nine Lives Foundation", 0, 0, ())

    with pytest.raises(AirtableQueryError, match="could not create output directory"):
        save_needs_invoice(result, blocked)

    assert blocked.read_text() == "not a directory"


def test_needs_invoice_cli_saves_json_and_prints_path(tmp_path, monkeypatch, capsys) -> None:
    """Wire a successful CLI query to automatic output persistence."""
    result = NeedsInvoiceResult("2026-09-03", "NLF", "Nine Lives Foundation", 0, 0, ())
    monkeypatch.setenv("AIRTABLE_TOKEN", "secret")
    monkeypatch.setenv("AIRTABLE_BASE_ID", BASE_ID)
    monkeypatch.setenv("AIRTABLE_APPOINTMENTS_TABLE_ID", APPOINTMENTS_TABLE_ID)
    monkeypatch.setenv("AIRTABLE_CATS_TABLE_ID", CATS_TABLE_ID)
    monkeypatch.setattr(needs_invoice_cli, "query_needs_invoice", lambda *args, **kwargs: result)

    status = needs_invoice_cli.main(
        ["2026-09-03", "NLF", "--outputs-dir", str(tmp_path / "outputs")]
    )

    output_path = tmp_path / "outputs" / "26SEP03-NLF" / "needs_invoice.json"
    assert status == 0
    assert capsys.readouterr().out.strip() == str(output_path)
    assert json.loads(output_path.read_text()) == result_document(result)


def test_needs_invoice_cli_writes_nothing_after_query_validation_failure(
    tmp_path, monkeypatch
) -> None:
    """Do not create an output directory when source validation rejects the query."""
    outputs = tmp_path / "outputs"
    monkeypatch.setenv("AIRTABLE_TOKEN", "secret")
    monkeypatch.setenv("AIRTABLE_BASE_ID", BASE_ID)
    monkeypatch.setenv("AIRTABLE_APPOINTMENTS_TABLE_ID", APPOINTMENTS_TABLE_ID)
    monkeypatch.setenv("AIRTABLE_CATS_TABLE_ID", CATS_TABLE_ID)

    def reject_query(*args, **kwargs):
        """Represent a validation failure before a complete result exists."""
        raise AirtableQueryError("invalid Cat Name")

    monkeypatch.setattr(needs_invoice_cli, "query_needs_invoice", reject_query)

    with pytest.raises(SystemExit):
        needs_invoice_cli.main(["2026-09-03", "NLF", "--outputs-dir", str(outputs)])

    assert not outputs.exists()
