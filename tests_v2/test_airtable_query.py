"""Tests for date-and-location-scoped Airtable appointment reads."""

import io
import json
from datetime import date, datetime
from urllib.parse import parse_qs, urlparse

import pytest
from airtable_query import (
    APPOINTMENT_FIELDS,
    appointment_scope_formula,
    query_appointments,
    query_records_by_ids,
)
from errors import PipelineError
from pipeline_config import AirtableConfig


class Response(io.BytesIO):
    """Provide a context-managed byte response for the fake transport."""

    def __enter__(self) -> "Response":
        """Return the open response at context entry."""
        return self

    def __exit__(self, *args: object) -> None:
        """Close the response without suppressing an exception."""
        self.close()


def _config() -> AirtableConfig:
    """Return valid non-production Airtable settings."""
    return AirtableConfig("secret", "appBase1", "tblAppointments1", "tblCats1")


def _response(document: object) -> Response:
    """Encode a JSON-compatible document as a fake HTTP response."""
    return Response(json.dumps(document).encode("utf-8"))


def _record(record_id: str = "recAppointment1") -> dict[str, object]:
    """Build a minimally scoped Airtable appointment record."""
    return {
        "id": record_id,
        "fields": {"Date": "2026-09-03", "Location": "Nine Lives Foundation"},
    }


def test_query_sends_exact_date_location_fields_and_bearer_token() -> None:
    """Issue a read-only request containing the canonical appointment scope."""
    requests = []

    def transport(request):
        """Capture the request and return one matching record."""
        requests.append(request)
        return _response({"records": [_record()]})

    records = query_appointments(_config(), date(2026, 9, 3), transport=transport)

    assert records == (_record(),)
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].get_header("Authorization") == "Bearer secret"
    parsed = urlparse(requests[0].full_url)
    parameters = parse_qs(parsed.query)
    assert parsed.path == "/v0/appBase1/tblAppointments1"
    assert parameters["fields[]"] == list(APPOINTMENT_FIELDS)
    assert parameters["pageSize"] == ["100"]
    assert parameters["filterByFormula"] == [
        "AND(IS_SAME({Date},DATETIME_PARSE('2026-09-03'),'day'),"
        "{Location}='Nine Lives Foundation',"
        "OR({Status}='Completed',{Status}='Scheduled',{Status}='Needs Scheduling'),"
        "OR({Status}!='Completed',{Cost}=BLANK(),{Filled}=BLANK(),{Invoice}=BLANK()))"
    ]


def test_query_follows_every_unique_pagination_offset() -> None:
    """Collect all matching records while carrying Airtable's opaque offset."""
    requests = []
    pages = iter(
        [
            {"records": [_record("recAppointment1")], "offset": "next/page"},
            {"records": [_record("recAppointment2")]},
        ]
    )

    def transport(request):
        """Capture a paginated request and return the next prepared page."""
        requests.append(request)
        return _response(next(pages))

    records = query_appointments(_config(), date(2026, 9, 3), transport=transport)

    assert [record["id"] for record in records] == ["recAppointment1", "recAppointment2"]
    assert "offset" not in parse_qs(urlparse(requests[0].full_url).query)
    assert parse_qs(urlparse(requests[1].full_url).query)["offset"] == ["next/page"]


@pytest.mark.parametrize(
    ("service_date", "message"),
    [
        ("2026-09-03", "service date must be a date"),
        (datetime(2026, 9, 3), "service date must be a date"),
    ],
)
def test_formula_rejects_malformed_dates(service_date: object, message: str) -> None:
    """Reject non-date scope values before constructing a formula."""
    with pytest.raises(PipelineError, match=message):
        appointment_scope_formula(service_date)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("document", "message"),
    [
        ({}, "missing a records array"),
        ({"records": [{"id": "wrong", "fields": {}}]}, "invalid identity"),
        ({"records": [{"id": "recAppointment1"}]}, "missing fields"),
        ({"records": [], "offset": ""}, "invalid pagination offset"),
    ],
)
def test_query_rejects_malformed_responses(document: object, message: str) -> None:
    """Fail closed when Airtable returns an invalid page or record shape."""
    with pytest.raises(PipelineError, match=message):
        query_appointments(
            _config(),
            date(2026, 9, 3),
            transport=lambda _request: _response(document),
        )


@pytest.mark.parametrize(
    "fields",
    [
        {"Date": "2026-09-04", "Location": "Nine Lives Foundation"},
        {"Date": "2026-09-03", "Location": "Animal Medical Center"},
        {"Date": "2026-09-03"},
    ],
)
def test_query_rejects_records_outside_requested_identity(fields: dict[str, str]) -> None:
    """Reject records whose returned date or location does not match the query."""
    document = {"records": [{"id": "recAppointment1", "fields": fields}]}

    with pytest.raises(PipelineError, match="outside the requested scope"):
        query_appointments(
            _config(),
            date(2026, 9, 3),
            transport=lambda _request: _response(document),
        )


def test_query_rejects_repeated_pagination_offset() -> None:
    """Stop a malformed Airtable pagination cycle instead of looping forever."""
    page = {"records": [], "offset": "same"}

    with pytest.raises(PipelineError, match="repeated pagination offset"):
        query_appointments(
            _config(),
            date(2026, 9, 3),
            transport=lambda _request: _response(page),
        )


def test_query_records_by_ids_requests_displayed_values() -> None:
    """Fetch exact identities using Airtable's required displayed-string settings."""
    requests = []

    def transport(request):
        """Capture the exact-record request and return its requested record."""
        requests.append(request)
        return _response({"records": [{"id": "recCat1", "fields": {"Voucher": "V-1"}}]})

    records = query_records_by_ids(
        _config(),
        "tblCats1",
        ("recCat1",),
        ("Voucher",),
        cell_format="string",
        transport=transport,
    )

    parameters = parse_qs(urlparse(requests[0].full_url).query)
    assert records[0]["id"] == "recCat1"
    assert parameters["filterByFormula"] == ["RECORD_ID()='recCat1'"]
    assert parameters["cellFormat"] == ["string"]
    assert parameters["userLocale"] == ["en-us"]
    assert parameters["timeZone"] == ["America/Los_Angeles"]


def test_query_records_by_ids_rejects_missing_identity() -> None:
    """Reject a response that omits an explicitly requested linked record."""
    with pytest.raises(PipelineError, match="does not match requested record identities"):
        query_records_by_ids(
            _config(),
            "tblCats1",
            ("recCat1",),
            ("Cat Name",),
            transport=lambda _request: _response({"records": []}),
        )
