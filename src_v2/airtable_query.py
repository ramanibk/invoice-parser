"""Read scoped Airtable records without exposing write operations."""

import json
from collections.abc import Callable, Mapping, Sequence
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
STRING_CELL_LOCALE = "en-us"
STRING_CELL_TIME_ZONE = "America/Los_Angeles"
STATUS_FILTER = "OR({Status}='Completed',{Status}='Scheduled',{Status}='Needs Scheduling')"
INCOMPLETE_FILTER = "OR({Status}!='Completed',{Cost}=BLANK(),{Filled}=BLANK(),{Invoice}=BLANK())"

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

Transport = Callable[[Request], AbstractContextManager[BinaryIO]]


def query_appointments(
    config: AirtableConfig, service_date: Date, *, transport: Transport = urlopen
) -> tuple[Mapping[str, object], ...]:
    """Return every appointment in the exact Needs Invoice scope."""
    if not isinstance(config, AirtableConfig):
        raise PipelineError("Airtable query configuration must be AirtableConfig")
    _require_date(service_date, "Airtable query service date")
    records = _query_records(
        config,
        config.appointments_table_id,
        APPOINTMENT_FIELDS,
        appointment_scope_formula(service_date),
        "json",
        transport,
    )
    for record in records:
        _validate_scope(record, service_date)
    return records


def query_records_by_ids(
    config: AirtableConfig,
    table_id: str,
    record_ids: Sequence[str],
    fields: Sequence[str],
    *,
    cell_format: str = "json",
    transport: Transport = urlopen,
) -> tuple[Mapping[str, object], ...]:
    """Return the exact requested records, rejecting missing or extra identities."""
    # One cat may be linked more than once in malformed source data. Stable
    # de-duplication prevents redundant requests without changing Airtable order.
    unique_ids = tuple(dict.fromkeys(record_ids))
    if any(not _is_airtable_record_id(record_id) for record_id in unique_ids):
        raise PipelineError("Airtable record identity is invalid")
    # This is batching, not one request per record. Capping each formula at 50
    # identities keeps URLs bounded while retaining exact RECORD_ID filtering.
    records = tuple(
        record
        for start in range(0, len(unique_ids), 50)
        for record in _query_records(
            config,
            table_id,
            fields,
            _identity_formula(unique_ids[start : start + 50]),
            cell_format,
            transport,
        )
    )
    # A successful HTTP response is insufficient: every requested linked record
    # must be present, and no unrelated record may enter the frozen run scope.
    returned_ids = {record["id"] for record in records}
    if len(records) != len(unique_ids) or returned_ids != set(unique_ids):
        raise PipelineError("Airtable response does not match requested record identities")
    return records


def appointment_scope_formula(service_date: Date) -> str:
    """Build the exact date, clinic, status, and incomplete-record filter."""
    _require_date(service_date, "Airtable query service date")
    return (
        "AND("
        f"IS_SAME({{Date}},DATETIME_PARSE('{service_date.isoformat()}'),'day'),"
        f"{{Location}}='{LOCATION_NAME}',{STATUS_FILTER},{INCOMPLETE_FILTER})"
    )


def _query_records(
    config: AirtableConfig,
    table_id: str,
    fields: Sequence[str],
    formula: str,
    cell_format: str,
    transport: Transport,
) -> tuple[Mapping[str, object], ...]:
    """Read every page for one validated Airtable table query."""
    _validate_query_inputs(table_id, fields, cell_format)
    records: list[Mapping[str, object]] = []
    offset = None
    seen_offsets: set[str] = set()
    while True:
        # Airtable offsets are opaque and must be replayed with the same query.
        page = _request_page(config, table_id, fields, formula, offset, cell_format, transport)
        records.extend(_page_records(page))
        offset = _page_offset(page)
        if offset is None:
            return tuple(records)
        if offset in seen_offsets:
            # Repeated offsets indicate a malformed pagination cycle.
            raise PipelineError("Airtable returned a repeated pagination offset")
        seen_offsets.add(offset)


def _request_page(
    config: AirtableConfig,
    table_id: str,
    fields: Sequence[str],
    formula: str,
    offset: str | None,
    cell_format: str,
    transport: Transport,
) -> Mapping[str, object]:
    """Request and decode one Airtable page without exposing its bearer token."""
    # Credentials stay in the header so they cannot leak through logged URLs.
    request = Request(
        _request_url(config, table_id, fields, formula, offset, cell_format),
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
    if not isinstance(document, dict):
        raise PipelineError("Airtable response must be a JSON object")
    return cast(Mapping[str, object], document)


def _request_url(
    config: AirtableConfig,
    table_id: str,
    fields: Sequence[str],
    formula: str,
    offset: str | None,
    cell_format: str,
) -> str:
    """Encode one Airtable list-records request."""
    parameters = [
        *(("fields[]", field) for field in fields),
        ("filterByFormula", formula),
        ("pageSize", "100"),
    ]
    if offset is not None:
        parameters.append(("offset", offset))
    if cell_format == "string":
        # Airtable requires locale and time zone whenever linked values are
        # requested as their human-readable labels rather than record IDs.
        parameters.extend(_string_cell_parameters())
    path = f"{API_ROOT}/{quote(config.base_id, safe='')}/{quote(table_id, safe='')}"
    return f"{path}?{urlencode(parameters)}"


def _string_cell_parameters() -> tuple[tuple[str, str], ...]:
    """Return Airtable's required displayed-string query parameters."""
    return (
        ("cellFormat", "string"),
        ("userLocale", STRING_CELL_LOCALE),
        ("timeZone", STRING_CELL_TIME_ZONE),
    )


def _page_records(page: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Validate every record in one response page before retaining it."""
    records = page.get("records")
    if not isinstance(records, list):
        raise PipelineError("Airtable response is missing a records array")
    for record in records:
        _validate_record(record)
    return tuple(cast(Mapping[str, object], record) for record in records)


def _validate_record(record: object) -> None:
    """Require one record identity and fields object."""
    if not isinstance(record, dict) or not _is_airtable_record_id(record.get("id")):
        raise PipelineError("Airtable returned a record with an invalid identity")
    if not isinstance(record.get("fields"), dict):
        raise PipelineError("Airtable returned a record with missing fields")


def _validate_scope(record: Mapping[str, object], service_date: Date) -> None:
    """Reject an appointment whose returned date or clinic is outside the query."""
    fields = cast(Mapping[str, object], record["fields"])
    expected = (service_date.isoformat(), LOCATION_NAME)
    if (fields.get("Date"), fields.get("Location")) != expected:
        raise PipelineError("Airtable returned an appointment outside the requested scope")


def _page_offset(page: Mapping[str, object]) -> str | None:
    """Return a valid next-page offset or identify the final page."""
    offset = page.get("offset")
    if offset is not None and (not isinstance(offset, str) or not offset):
        raise PipelineError("Airtable returned an invalid pagination offset")
    return cast(str | None, offset)


def _identity_formula(record_ids: Sequence[str]) -> str:
    """Build an exact Airtable record-identity filter."""
    comparisons = ",".join(f"RECORD_ID()='{record_id}'" for record_id in record_ids)
    return comparisons if len(record_ids) == 1 else f"OR({comparisons})"


def _validate_query_inputs(table_id: object, fields: Sequence[str], cell_format: object) -> None:
    """Reject parameters that cannot form a supported read-only query."""
    if not isinstance(table_id, str) or not table_id.startswith("tbl"):
        raise PipelineError("Airtable table ID must start with tbl")
    if not fields or any(not isinstance(field, str) or not field for field in fields):
        raise PipelineError("Airtable query fields must be non-empty strings")
    if cell_format not in {"json", "string"}:
        raise PipelineError("Airtable cell format must be json or string")
