"""Read Airtable appointment records for one exact pipeline scope."""

import json
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from datetime import date as Date
from typing import BinaryIO, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from errors import PipelineError
from global_constants import LOCATION_NAME
from models_validation import _is_airtable_record_id, _require_date
from pipeline_config import AirtableConfig

API_ROOT = "https://api.airtable.com/v0"

# Request only fields needed to validate the scope and build the later cat join.
# Airtable may omit empty fields from a record, so downstream normalization must
# continue to distinguish an omitted optional value from a malformed required one.
APPOINTMENT_FIELDS = (
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
)

# Keeping the HTTP executor injectable lets tests inspect requests without making
# network calls; production uses urllib's standard read-only request executor.
Transport = Callable[[Request], AbstractContextManager[BinaryIO]]


def query_appointments(
    config: AirtableConfig,
    service_date: Date,
    *,
    transport: Transport = urlopen,
) -> tuple[Mapping[str, object], ...]:
    """Return all appointment records matching the exact date and clinic.

    Each response page and record is validated before its records are retained.
    Malformed pagination, record IDs, or returned scope values fail the complete
    query instead of allowing a partial result.
    """
    if not isinstance(config, AirtableConfig):
        raise PipelineError("Airtable query configuration must be AirtableConfig")
    _require_date(service_date, "Airtable query service date")
    records: list[Mapping[str, object]] = []
    offset = None
    seen_offsets: set[str] = set()
    while True:
        # Airtable returns an opaque offset when another page exists. The same
        # date/location formula is sent on every page.
        page = _request_page(config, service_date, offset, transport)
        records.extend(_page_records(page, service_date))
        offset = _page_offset(page)
        if offset is None:
            return tuple(records)
        # A repeated offset would otherwise turn an invalid response into an
        # unbounded request loop.
        if offset in seen_offsets:
            raise PipelineError("Airtable returned a repeated pagination offset")
        seen_offsets.add(offset)


def appointment_scope_formula(service_date: Date) -> str:
    """Build the Airtable formula for the configured date and clinic.

    ``IS_SAME`` compares the calendar day rather than relying on Airtable's
    display formatting. The location comes from the pipeline's canonical
    constant, so callers cannot accidentally query a different clinic.
    """
    _require_date(service_date, "Airtable query service date")
    return (
        "AND("
        f"IS_SAME({{Date}},DATETIME_PARSE('{service_date.isoformat()}'),'day'),"
        f"{{Location}}='{LOCATION_NAME}'"
        ")"
    )


def _request_page(
    config: AirtableConfig,
    service_date: Date,
    offset: str | None,
    transport: Transport,
) -> Mapping[str, object]:
    """Request and decode one Airtable page using bearer authentication.

    Transport and JSON failures become ``PipelineError`` so the command has one
    stable failure type and never exposes the token in an error message.
    """
    # Keep the credential in the authorization header rather than placing it in
    # the URL, where it could appear in logs or captured query strings.
    request = Request(
        _request_url(config, service_date, offset),
        headers={"Authorization": f"Bearer {config.token}"},
        method="GET",
    )
    try:
        with transport(request) as response:
            document = json.load(response)
    except HTTPError as exc:
        raise PipelineError(f"Airtable request failed with HTTP {exc.code}") from exc
    except (OSError, URLError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"could not read Airtable response: {exc}") from exc
    # List-record responses must be objects because pagination and records are
    # named properties; accepting another JSON type would hide a schema failure.
    if not isinstance(document, dict):
        raise PipelineError("Airtable response must be a JSON object")
    return cast(Mapping[str, object], document)


def _request_url(config: AirtableConfig, service_date: Date, offset: str | None) -> str:
    """Encode the scoped formula, requested fields, page size, and optional offset."""
    # Repeated ``fields[]`` parameters preserve the explicit field allowlist.
    # ``urlencode`` also escapes the Airtable formula and opaque offset safely.
    parameters = [
        *(("fields[]", field) for field in APPOINTMENT_FIELDS),
        ("filterByFormula", appointment_scope_formula(service_date)),
        ("pageSize", "100"),
    ]
    if offset is not None:
        parameters.append(("offset", offset))
    encoded_base = quote(config.base_id, safe="")
    encoded_table = quote(config.appointments_table_id, safe="")
    path = f"{API_ROOT}/{encoded_base}/{encoded_table}"
    return f"{path}?{urlencode(parameters)}"


def _page_records(
    page: Mapping[str, object], service_date: Date
) -> tuple[Mapping[str, object], ...]:
    """Validate every record identity and its returned date/location scope.

    Validation happens before the page tuple is returned, ensuring a malformed
    record cannot produce a partially accepted page.
    """
    records = page.get("records")
    if not isinstance(records, list):
        raise PipelineError("Airtable response is missing a records array")
    for record in records:
        _validate_record(record, service_date)
    return tuple(cast(Mapping[str, object], record) for record in records)


def _validate_record(record: object, service_date: Date) -> None:
    """Reject malformed records and identities outside the requested scope."""
    if not isinstance(record, dict) or not _is_airtable_record_id(record.get("id")):
        raise PipelineError("Airtable returned an appointment with an invalid identity")
    fields = record.get("fields")
    if not isinstance(fields, dict):
        raise PipelineError(f"Airtable appointment {record['id']} is missing fields")
    # Recheck the server response instead of trusting filterByFormula alone. This
    # protects later matching if the Airtable schema or formula behavior changes.
    expected_scope = (service_date.isoformat(), LOCATION_NAME)
    returned_scope = (fields.get("Date"), fields.get("Location"))
    if returned_scope != expected_scope:
        raise PipelineError(f"Airtable appointment {record['id']} is outside the requested scope")


def _page_offset(page: Mapping[str, object]) -> str | None:
    """Return a valid next-page offset or identify the final page."""
    offset = page.get("offset")
    if offset is not None and (not isinstance(offset, str) or not offset):
        raise PipelineError("Airtable returned an invalid pagination offset")
    return cast(str | None, offset)
