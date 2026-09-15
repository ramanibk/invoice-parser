"""Invoke Codex for cat matching and validate its complete structured result."""

import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from errors import PipelineError
from global_constants import AIRTABLE_ENV_VARS

PROJECT_DIR = Path(__file__).resolve().parent.parent
INVARIANTS_PATH = PROJECT_DIR / "src" / "prompts" / "invariants.md"
INSTRUCTIONS_PATH = PROJECT_DIR / "src" / "prompts" / "cat_matching_instructions.md"
OUTPUT_SCHEMA_PATH = Path(__file__).with_name("cat_matching_output.schema.json")
MATCHES_FILENAME = "cat_matches.json"
REVIEW_FILENAME = "cat_match_review.json"

MATCH_FIELDS = {
    "paperwork_cat_id",
    "paperwork_display_name",
    "airtable_cat_id",
    "airtable_display_name",
    "match_reason",
}
REVIEW_FIELDS = MATCH_FIELDS | {"review_kind", "resolution"}
REVIEW_KINDS = {"unresolved_paperwork", "unassigned_airtable", "bounded_cohort"}

Executor = Callable[..., subprocess.CompletedProcess[str]]


def run_codex_cat_matching(
    run_directory: Path, *, executor: Executor = subprocess.run
) -> tuple[Path, Path, int]:
    """Run read-only Codex matching, validate it, and publish both result files."""
    paperwork_path = run_directory / "extraction.json"
    needs_invoice_path = run_directory / "needs_invoice.json"
    paperwork = _load_object(paperwork_path, "paperwork snapshot")
    needs_invoice = _load_object(needs_invoice_path, "Airtable snapshot")
    # Validate the frozen state before paying for or exposing data to a model.
    _validate_input_identity(run_directory, paperwork, needs_invoice)
    _require_instruction_files()
    command = (
        "codex",
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--cd",
        str(PROJECT_DIR),
        "--output-schema",
        str(OUTPUT_SCHEMA_PATH),
        "-",
    )
    try:
        # Pass an argument tuple rather than a shell command so artifact paths
        # cannot be interpreted as shell syntax.
        result = executor(
            command,
            input=_matching_prompt(paperwork_path, needs_invoice_path),
            text=True,
            capture_output=True,
            check=False,
            env=_codex_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PipelineError(f"could not run Codex cat matching: {exc}") from exc
    if result.returncode != 0:
        raise PipelineError(_codex_failure_message(result.stderr))
    output = _decode_codex_output(result.stdout)
    matches, review_items = _validate_output(output, paperwork, needs_invoice)
    matches_path, review_path = _publish_outputs(run_directory, matches, review_items)
    return matches_path, review_path, len(review_items)


def _matching_prompt(paperwork_path: Path, needs_invoice_path: Path) -> str:
    """Build a path-specific prompt that preserves the shared AI invariants."""
    return f"""Match the paperwork cats to the Airtable cats for this run.

Read and follow {INVARIANTS_PATH} before beginning.
Read and follow {INSTRUCTIONS_PATH} for matching rules and evidence priorities.
Read the authoritative paperwork snapshot at {paperwork_path}.
Read the authoritative Airtable snapshot at {needs_invoice_path}.

Do not modify any file or Airtable record. Return one JSON object with exactly two keys:
`matches` containing every accepted match and `review_items` containing every item that requires
operator review. Use paperwork terminology for identities originating in extraction.json.
Validate the complete response before returning it.
Costs and services are supporting evidence only and never override a conflicting strong identity.
"""


def _codex_environment() -> dict[str, str]:
    """Return the process environment without Airtable credentials or schema IDs."""
    environment = dict(os.environ)
    # Codex needs its own authentication, but first-pass matching needs only the
    # frozen snapshots and must not receive live Airtable access.
    for name in AIRTABLE_ENV_VARS.values():
        environment.pop(name, None)
    return environment


def _decode_codex_output(value: str) -> dict[str, Any]:
    """Decode the schema-constrained final Codex response as one JSON object."""
    try:
        document = json.loads(value)
    except (TypeError, json.JSONDecodeError) as exc:
        raise PipelineError("Codex cat matching did not return valid JSON") from exc
    if not isinstance(document, dict):
        raise PipelineError("Codex cat matching output must be a JSON object")
    return document


def _codex_failure_message(stderr: str | None) -> str:
    """Return a safe diagnostic without relaying model progress or source identities."""
    safe_failures = (
        ("invalid_json_schema", "Codex cat matching failed: invalid output schema"),
        ("authentication", "Codex cat matching failed: authentication error"),
        ("rate_limit", "Codex cat matching failed: rate limit exceeded"),
    )
    normalized = (stderr or "").lower()
    for marker, message in safe_failures:
        if marker in normalized:
            return message
    return "Codex cat matching failed"


def _validate_input_identity(
    run_directory: Path, paperwork: dict[str, Any], needs_invoice: dict[str, Any]
) -> None:
    """Require both authoritative snapshots to describe the selected run."""
    parameters = paperwork.get("input_parameters")
    if paperwork.get("run_id") != run_directory.name or not isinstance(parameters, dict):
        raise PipelineError("paperwork snapshot identity does not match its run directory")
    expected = (parameters.get("date"), parameters.get("vet"))
    actual = (needs_invoice.get("date"), needs_invoice.get("location_code"))
    if actual != expected:
        raise PipelineError("extraction and Airtable snapshot identities do not match")


def _require_instruction_files() -> None:
    """Require every local instruction and schema input before starting Codex."""
    for path in (INVARIANTS_PATH, INSTRUCTIONS_PATH, OUTPUT_SCHEMA_PATH):
        if not path.is_file():
            raise PipelineError("Codex cat matching instruction file is unavailable")


def _validate_output(
    output: dict[str, Any], paperwork: dict[str, Any], needs_invoice: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate structure, identities, names, uniqueness, and complete coverage."""
    if set(output) != {"matches", "review_items"}:
        raise PipelineError("Codex output must contain only matches and review_items")
    matches = _require_array(output["matches"], "matches")
    review_items = _require_array(output["review_items"], "review_items")
    paperwork_cats = _index_cats(paperwork, "cat_id", "paperwork snapshot")
    airtable_cats = _index_cats(needs_invoice, "airtable_cat_id", "Airtable snapshot")
    indexed_matches = _validate_matches(matches, paperwork_cats, airtable_cats)
    _validate_review_items(review_items, indexed_matches, paperwork_cats, airtable_cats)
    _validate_coverage(indexed_matches, review_items, paperwork_cats, airtable_cats)
    return matches, review_items


def _validate_matches(
    values: list[object],
    paperwork: dict[str, dict[str, Any]],
    airtable: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Validate accepted matches and index them by paperwork identity."""
    indexed: dict[str, dict[str, Any]] = {}
    assigned: set[str] = set()
    for value in values:
        entry = _match_entry(value)
        paperwork_id = cast(str, entry["paperwork_cat_id"])
        airtable_id = cast(str, entry["airtable_cat_id"])
        if paperwork_id not in paperwork or paperwork_id in indexed:
            raise PipelineError("matches contains an unknown or duplicate paperwork cat")
        if airtable_id not in airtable or airtable_id in assigned:
            raise PipelineError("matches contains an unknown or duplicate Airtable cat")
        _require_source_names(entry, paperwork[paperwork_id], airtable[airtable_id])
        indexed[paperwork_id] = entry
        assigned.add(airtable_id)
    return indexed


def _validate_review_items(
    values: list[object],
    matches: dict[str, dict[str, Any]],
    paperwork: dict[str, dict[str, Any]],
    airtable: dict[str, dict[str, Any]],
) -> None:
    """Validate every typed review item and reject duplicates."""
    seen: set[tuple[object, object, object]] = set()
    for value in values:
        entry = _review_entry(value)
        _validate_review_item(entry, matches, paperwork, airtable)
        identity = (
            entry["review_kind"],
            entry["paperwork_cat_id"],
            entry["airtable_cat_id"],
        )
        if identity in seen:
            raise PipelineError("review_items contains a duplicate item")
        seen.add(identity)


def _validate_review_item(
    entry: dict[str, Any],
    matches: dict[str, dict[str, Any]],
    paperwork: dict[str, dict[str, Any]],
    airtable: dict[str, dict[str, Any]],
) -> None:
    """Dispatch one review item to its kind-specific identity checks."""
    kind = entry["review_kind"]
    if kind == "unresolved_paperwork":
        _validate_unresolved_paperwork(entry, matches, paperwork)
    elif kind == "unassigned_airtable":
        _validate_unassigned_airtable(entry, matches, airtable)
    else:
        _validate_bounded_cohort(entry, matches, paperwork, airtable)


def _validate_unresolved_paperwork(
    entry: dict[str, Any],
    matches: dict[str, dict[str, Any]],
    paperwork: dict[str, dict[str, Any]],
) -> None:
    """Require an unresolved item to identify only a known paperwork cat."""
    paperwork_id = entry["paperwork_cat_id"]
    if not isinstance(paperwork_id, str) or paperwork_id not in paperwork:
        raise PipelineError("unresolved review contains an unknown paperwork cat")
    if paperwork_id in matches:
        raise PipelineError("accepted paperwork match cannot be unresolved")
    if entry["airtable_cat_id"] is not None or entry["airtable_display_name"] is not None:
        raise PipelineError("unresolved paperwork review must have null Airtable fields")
    if entry["paperwork_display_name"] != paperwork[paperwork_id].get("display_name"):
        raise PipelineError("review item has an incorrect paperwork display name")


def _validate_unassigned_airtable(
    entry: dict[str, Any],
    matches: dict[str, dict[str, Any]],
    airtable: dict[str, dict[str, Any]],
) -> None:
    """Require an unassigned item to identify only a known Airtable cat."""
    airtable_id = entry["airtable_cat_id"]
    if not isinstance(airtable_id, str) or airtable_id not in airtable:
        raise PipelineError("unassigned review contains an unknown Airtable cat")
    assigned = {match["airtable_cat_id"] for match in matches.values()}
    if airtable_id in assigned:
        raise PipelineError("accepted Airtable match cannot be unassigned")
    if entry["paperwork_cat_id"] is not None or entry["paperwork_display_name"] is not None:
        raise PipelineError("unassigned Airtable review must have null paperwork fields")
    if entry["airtable_display_name"] != airtable[airtable_id].get("cat_name"):
        raise PipelineError("review item has an incorrect Airtable display name")


def _validate_bounded_cohort(
    entry: dict[str, Any],
    matches: dict[str, dict[str, Any]],
    paperwork: dict[str, dict[str, Any]],
    airtable: dict[str, dict[str, Any]],
) -> None:
    """Require a bounded-cohort review item to copy one accepted match."""
    paperwork_id = entry["paperwork_cat_id"]
    airtable_id = entry["airtable_cat_id"]
    if not isinstance(paperwork_id, str) or not isinstance(airtable_id, str):
        raise PipelineError("bounded-cohort review must contain both cat identities")
    if paperwork_id not in paperwork or airtable_id not in airtable:
        raise PipelineError("bounded-cohort review contains an unknown identity")
    expected = matches.get(paperwork_id)
    copied_match = {name: entry[name] for name in MATCH_FIELDS}
    if expected != copied_match:
        raise PipelineError("bounded-cohort review must copy its accepted match")


def _validate_coverage(
    matches: dict[str, dict[str, Any]],
    review_items: list[dict[str, Any]],
    paperwork: dict[str, dict[str, Any]],
    airtable: dict[str, dict[str, Any]],
) -> None:
    """Require every source cat to be assigned or represented for review."""
    unresolved = {
        item["paperwork_cat_id"]
        for item in review_items
        if item["review_kind"] == "unresolved_paperwork"
    }
    unassigned = {
        item["airtable_cat_id"]
        for item in review_items
        if item["review_kind"] == "unassigned_airtable"
    }
    if set(paperwork) != matches.keys() | unresolved:
        raise PipelineError("Codex output does not cover every paperwork cat")
    assigned = {entry["airtable_cat_id"] for entry in matches.values()}
    if set(airtable) != assigned | unassigned:
        raise PipelineError("Codex output does not cover every Airtable cat")


def _match_entry(value: object) -> dict[str, Any]:
    """Validate one accepted match's exact field contract."""
    entry = _require_object(value, "cat match entry")
    if set(entry) != MATCH_FIELDS:
        raise PipelineError("cat match entry has invalid fields")
    if any(not isinstance(entry[name], str) or not entry[name] for name in MATCH_FIELDS):
        raise PipelineError("cat match fields must be non-empty strings")
    return entry


def _review_entry(value: object) -> dict[str, Any]:
    """Validate one operator-review item's exact field contract."""
    entry = _require_object(value, "cat review item")
    if set(entry) != REVIEW_FIELDS or entry.get("review_kind") not in REVIEW_KINDS:
        raise PipelineError("cat review item has invalid fields")
    if entry["resolution"] != "" or not _is_nonempty_string(entry["match_reason"]):
        raise PipelineError("cat review item has invalid reason or resolution")
    identity_fields = MATCH_FIELDS - {"match_reason"}
    if any(not _is_optional_nonempty_string(entry[name]) for name in identity_fields):
        raise PipelineError("cat review identity fields must be non-empty strings or null")
    return entry


def _is_nonempty_string(value: object) -> bool:
    """Return whether a value is a non-empty string."""
    return isinstance(value, str) and bool(value)


def _is_optional_nonempty_string(value: object) -> bool:
    """Return whether a value is null or a non-empty string."""
    return value is None or _is_nonempty_string(value)


def _require_source_names(
    entry: dict[str, Any], paperwork: dict[str, Any], airtable: dict[str, Any]
) -> None:
    """Require output display names to be copied exactly from source snapshots."""
    if entry["paperwork_display_name"] != paperwork.get("display_name"):
        raise PipelineError("match has an incorrect paperwork display name")
    if entry["airtable_display_name"] != airtable.get("cat_name"):
        raise PipelineError("match has an incorrect Airtable display name")


def _index_cats(document: dict[str, Any], key: str, description: str) -> dict[str, dict[str, Any]]:
    """Index one artifact's cats array by a required unique string identity."""
    cats = document.get("cats")
    if not isinstance(cats, list) or any(not isinstance(item, dict) for item in cats):
        raise PipelineError(f"{description} must contain a cats array of objects")
    indexed = {item.get(key): item for item in cats}
    if any(not isinstance(item.get(key), str) for item in cats) or len(indexed) != len(cats):
        raise PipelineError(f"{description} cat identities must be unique strings")
    return cast(dict[str, dict[str, Any]], indexed)


def _require_object(value: object, description: str) -> dict[str, Any]:
    """Require and return one JSON object."""
    if not isinstance(value, dict):
        raise PipelineError(f"{description} must be a JSON object")
    return cast(dict[str, Any], value)


def _require_array(value: object, description: str) -> list[object]:
    """Require and return one JSON array."""
    if not isinstance(value, list):
        raise PipelineError(f"{description} must be a JSON array")
    return cast(list[object], value)


def _load_object(path: Path, description: str) -> dict[str, Any]:
    """Load one authoritative input artifact as a JSON object."""
    try:
        return _require_object(json.loads(path.read_text(encoding="utf-8")), description)
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"could not read {description} artifact") from exc


def _publish_outputs(
    run_directory: Path,
    matches: list[dict[str, Any]],
    review_items: list[dict[str, Any]],
) -> tuple[Path, Path]:
    """Publish both validated cat outputs and roll back a failed pair."""
    matches_path = run_directory / MATCHES_FILENAME
    review_path = run_directory / REVIEW_FILENAME
    temporary = (
        run_directory / f".{MATCHES_FILENAME}.tmp",
        run_directory / f".{REVIEW_FILENAME}.tmp",
    )
    if matches_path.exists() or review_path.exists():
        raise PipelineError("cat matching artifacts already exist")
    try:
        # Both complete documents are staged before either public filename is
        # created. A later failure removes the whole newly created pair.
        temporary[0].write_text(json.dumps(matches, indent=2) + "\n", encoding="utf-8")
        temporary[1].write_text(json.dumps(review_items, indent=2) + "\n", encoding="utf-8")
        temporary[0].replace(matches_path)
        temporary[1].replace(review_path)
    except OSError as exc:
        _remove_matching_outputs((*temporary, matches_path, review_path))
        raise PipelineError("could not publish cat matching artifacts") from exc
    return matches_path, review_path


def _remove_matching_outputs(paths: tuple[Path, ...]) -> None:
    """Best-effort remove only incomplete outputs from the current publication."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
