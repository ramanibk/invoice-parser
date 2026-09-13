"""Provide a small read-only client for Airtable record queries."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from treatment_sheet_parser.needs_invoice.errors import AirtableQueryError

# Stable REST endpoint used for every read-only Airtable request.
API_ROOT = "https://api.airtable.com/v0"

# Airtable requires both settings when records use displayed string formatting.
STRING_CELL_LOCALE = "en-us"
STRING_CELL_TIME_ZONE = "America/Los_Angeles"

# Injectable request executor; tests supply an in-memory implementation.
Transport = Callable[[Request], Any]


class AirtableClient:
    """Read records from one Airtable base without exposing write operations."""

    def __init__(
        self,
        token: str,
        *,
        base_id: str,
        transport: Transport = urlopen,
    ) -> None:
        """Configure a read-only client for one Airtable base.

        Args:
            token: Personal access token sent as a bearer credential.
            base_id: Airtable base identifier, which must begin with ``app``.
            transport: Callable used to execute requests; injectable for tests.

        Raises:
            AirtableQueryError: If the token is blank or the base ID is invalid.
        """
        if not token.strip():
            raise AirtableQueryError("AIRTABLE_TOKEN must be set")
        if not base_id.startswith("app"):
            raise AirtableQueryError("Airtable base ID must start with app")
        self._token = token
        self.base_id = base_id
        self._transport = transport

    def list_records(
        self,
        table_id: str,
        *,
        fields: Sequence[str],
        formula: str | None = None,
        sort: Sequence[tuple[str, str]] = (),
        cell_format: str = "json",
    ) -> tuple[Mapping[str, object], ...]:
        """Return every record from a filtered table query.

        Pagination is followed until Airtable omits its offset. Repeated offsets
        are rejected to prevent an unbounded request loop.

        Args:
            table_id: Airtable table identifier.
            fields: Non-empty field names to include in each record.
            formula: Optional Airtable ``filterByFormula`` expression.
            sort: Field and ``asc``/``desc`` direction pairs in priority order.
            cell_format: ``json`` for typed values or ``string`` for displayed values.

        Raises:
            AirtableQueryError: If the query or any response page is malformed.
        """
        _validate_query(table_id, fields, sort, cell_format)
        records: list[Mapping[str, object]] = []
        offset = None
        seen_offsets: set[str] = set()
        while True:
            page = self._read_page(table_id, fields, formula, sort, offset, cell_format)
            records.extend(_page_records(page))
            offset = _next_offset(page)
            if offset is None:
                return tuple(records)
            if offset in seen_offsets:
                raise AirtableQueryError("Airtable returned a repeated pagination offset")
            seen_offsets.add(offset)

    def records_by_ids(
        self,
        table_id: str,
        record_ids: Sequence[str],
        *,
        fields: Sequence[str],
        cell_format: str = "json",
    ) -> tuple[Mapping[str, object], ...]:
        """Fetch a collection of records by exact Airtable identity.

        Duplicate input IDs are removed while preserving order, and requests are
        split into groups of 50 so generated formulas remain bounded.

        Args:
            table_id: Airtable table identifier.
            record_ids: Record IDs to fetch.
            fields: Fields to include in each returned record.
            cell_format: ``json`` for typed values or ``string`` for displayed values.

        Raises:
            AirtableQueryError: If an ID is malformed or Airtable returns a
                different set of identities than requested.
        """
        # Stable de-duplication also makes the response identity check unambiguous.
        unique_ids = tuple(dict.fromkeys(record_ids))
        if any(not is_record_id(record_id) for record_id in unique_ids):
            raise AirtableQueryError("Airtable record identity is invalid")
        # Airtable formulas and URLs are kept bounded at 50 identities per request.
        records = tuple(
            record
            for start in range(0, len(unique_ids), 50)
            for record in self.list_records(
                table_id,
                fields=fields,
                formula=_identity_formula(unique_ids[start : start + 50]),
                cell_format=cell_format,
            )
        )
        returned_ids = {record["id"] for record in records}
        if len(records) != len(unique_ids) or returned_ids != set(unique_ids):
            raise AirtableQueryError("Airtable response does not match requested record identities")
        return records

    def _read_page(
        self,
        table_id: str,
        fields: Sequence[str],
        formula: str | None,
        sort: Sequence[tuple[str, str]],
        offset: str | None,
        cell_format: str,
    ) -> Mapping[str, object]:
        """Issue and decode one page of an Airtable list-records request.

        Network, HTTP, text-decoding, and JSON errors are translated into
        ``AirtableQueryError`` so callers receive one stable failure type.
        """
        url = _request_url(self.base_id, table_id, fields, formula, sort, offset, cell_format)
        request = Request(
            url,
            headers={"Authorization": f"Bearer {self._token}"},
            method="GET",
        )
        try:
            with self._transport(request) as response:
                document = json.load(response)
        except HTTPError as exc:
            raise AirtableQueryError(f"Airtable request failed with HTTP {exc.code}") from exc
        except (OSError, URLError, UnicodeError, json.JSONDecodeError) as exc:
            raise AirtableQueryError(f"could not read Airtable response: {exc}") from exc
        if not isinstance(document, dict):
            raise AirtableQueryError("Airtable response must be a JSON object")
        return document


def is_record_id(value: object) -> bool:
    """Return whether ``value`` is a syntactically valid Airtable record ID.

    This validates only the ``rec`` prefix and alphanumeric shape; it does not
    contact Airtable or establish that the record exists.
    """
    return isinstance(value, str) and re.fullmatch(r"rec[A-Za-z0-9]+", value) is not None


def _request_url(
    base_id: str,
    table_id: str,
    fields: Sequence[str],
    formula: str | None,
    sort: Sequence[tuple[str, str]],
    offset: str | None,
    cell_format: str,
) -> str:
    """Build an encoded URL for one Airtable list-records page.

    Field and sort parameters retain caller order. Optional formula and offset
    parameters are omitted rather than sent with empty values.
    """
    parameters: list[tuple[str, str | int]] = [
        *(("fields[]", field) for field in fields),
        *((f"sort[{index}][field]", item[0]) for index, item in enumerate(sort)),
        *((f"sort[{index}][direction]", item[1]) for index, item in enumerate(sort)),
        ("pageSize", 100),
    ]
    if formula is not None:
        parameters.append(("filterByFormula", formula))
    if offset is not None:
        parameters.append(("offset", offset))
    if cell_format == "string":
        parameters.extend(
            (
                ("cellFormat", "string"),
                ("userLocale", STRING_CELL_LOCALE),
                ("timeZone", STRING_CELL_TIME_ZONE),
            )
        )
    path = f"{API_ROOT}/{quote(base_id, safe='')}/{quote(table_id, safe='')}"
    return f"{path}?{urlencode(parameters)}"


def _page_records(page: Mapping[str, object]) -> tuple[Mapping[str, object], ...]:
    """Validate and return the records in one Airtable response page.

    Every record must be an object with a valid ID and a fields object. The
    returned tuple prevents callers from mutating the page's record collection.
    """
    records = page.get("records")
    if not isinstance(records, list):
        raise AirtableQueryError("Airtable response is missing a records array")
    for record in records:
        if not isinstance(record, dict) or not is_record_id(record.get("id")):
            raise AirtableQueryError("Airtable returned a record with an invalid identity")
        if not isinstance(record.get("fields"), dict):
            raise AirtableQueryError(f"Airtable record {record['id']} is missing fields")
    return tuple(records)


def _next_offset(page: Mapping[str, object]) -> str | None:
    """Return a page's pagination offset after validating its shape.

    ``None`` indicates the final page; present offsets must be non-empty strings.
    """
    offset = page.get("offset")
    if offset is not None and (not isinstance(offset, str) or not offset):
        raise AirtableQueryError("Airtable returned an invalid pagination offset")
    return offset


def _validate_query(
    table_id: str,
    fields: Sequence[str],
    sort: Sequence[tuple[str, str]],
    cell_format: str,
) -> None:
    """Validate list-record query inputs before any network access.

    Table IDs must use Airtable's ``tbl`` prefix, fields must be non-empty
    strings, each sort direction must be either ``asc`` or ``desc``, and cell
    formatting must select typed JSON or displayed strings.

    Raises:
        AirtableQueryError: If any input cannot form a supported safe query.
    """
    if not table_id.startswith("tbl"):
        raise AirtableQueryError("Airtable table ID must start with tbl")
    if not fields or any(not isinstance(field, str) or not field for field in fields):
        raise AirtableQueryError("Airtable query fields must be non-empty strings")
    if any(not field or direction not in {"asc", "desc"} for field, direction in sort):
        raise AirtableQueryError("Airtable sort entries require a field and asc or desc")
    if cell_format not in {"json", "string"}:
        raise AirtableQueryError("Airtable cell format must be json or string")


def _identity_formula(record_ids: Sequence[str]) -> str:
    """Build an Airtable formula matching the supplied record IDs exactly.

    A single comparison is returned directly; multiple comparisons are wrapped
    in ``OR``. Callers validate IDs and avoid passing an empty collection.
    """
    comparisons = ",".join(f"RECORD_ID()='{record_id}'" for record_id in record_ids)
    return comparisons if len(record_ids) == 1 else f"OR({comparisons})"
