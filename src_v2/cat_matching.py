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
MAPPING_FILENAME = "cat_mapping.json"
REVIEW_FILENAME = "cat_match_review.json"

Executor = Callable[..., subprocess.CompletedProcess[str]]


def run_codex_cat_matching(
    run_directory: Path, *, executor: Executor = subprocess.run
) -> tuple[Path, Path, int]:
    """Run read-only Codex matching, validate it, and publish both result files."""
    extraction_path = run_directory / "extraction.json"
    needs_invoice_path = run_directory / "needs_invoice.json"
    extraction = _load_object(extraction_path, "extraction")
    needs_invoice = _load_object(needs_invoice_path, "Airtable snapshot")
    # Validate the frozen state before paying for or exposing data to a model.
    _validate_input_identity(run_directory, extraction, needs_invoice)
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
            input=_matching_prompt(extraction_path, needs_invoice_path),
            text=True,
            capture_output=True,
            check=False,
            env=_codex_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PipelineError(f"could not run Codex cat matching: {exc}") from exc
    if result.returncode != 0:
        # Do not relay stderr; it may contain model progress or source identities.
        raise PipelineError("Codex cat matching failed")
    output = _decode_codex_output(result.stdout)
    mapping, review = _validate_output(output, extraction, needs_invoice)
    mapping_path, review_path = _publish_outputs(run_directory, mapping, review)
    return mapping_path, review_path, len(review)


def _matching_prompt(extraction_path: Path, needs_invoice_path: Path) -> str:
    """Build a path-specific prompt that preserves the shared AI invariants."""
    return f"""Match the extraction cats to the Airtable cats for this run.

Read and follow {INVARIANTS_PATH} before beginning.
Read and follow {INSTRUCTIONS_PATH} for matching rules and evidence priorities.
Read the authoritative extraction snapshot at {extraction_path}.
Read the authoritative Airtable snapshot at {needs_invoice_path}.

Do not modify any file or Airtable record. Return one JSON object with exactly two keys:
`cat_mapping` containing the complete cat_mapping.json object and `cat_match_review` containing
the complete cat_match_review.json object. Validate both conceptual artifacts before returning.
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


def _validate_input_identity(
    run_directory: Path, extraction: dict[str, Any], needs_invoice: dict[str, Any]
) -> None:
    """Require both authoritative snapshots to describe the selected run."""
    parameters = extraction.get("input_parameters")
    if extraction.get("run_id") != run_directory.name or not isinstance(parameters, dict):
        raise PipelineError("extraction identity does not match its run directory")
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
    output: dict[str, Any], extraction: dict[str, Any], needs_invoice: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate structure, identities, names, uniqueness, and complete coverage."""
    if set(output) != {"cat_mapping", "cat_match_review"}:
        raise PipelineError("Codex output must contain only cat_mapping and cat_match_review")
    mapping = _require_object(output["cat_mapping"], "cat_mapping")
    review = _require_object(output["cat_match_review"], "cat_match_review")
    # The JSON schema checks shape; these indexes enable source-aware checks that
    # a static schema cannot express.
    extraction_cats = _index_cats(extraction, "cat_id", "extraction")
    airtable_cats = _index_cats(needs_invoice, "airtable_cat_id", "Airtable snapshot")
    _validate_mapping(mapping, extraction_cats, airtable_cats)
    _validate_review(review, mapping, extraction_cats, airtable_cats)
    _validate_coverage(mapping, review, extraction_cats, airtable_cats)
    return mapping, review


def _validate_mapping(
    mapping: dict[str, Any],
    extraction: dict[str, dict[str, Any]],
    airtable: dict[str, dict[str, Any]],
) -> None:
    """Require every confident match to reference exact source identities and names."""
    assigned: set[str] = set()
    for cat_id, value in mapping.items():
        if cat_id not in extraction:
            raise PipelineError("cat_mapping contains an unknown extraction cat")
        entry = _match_entry(value, review=False)
        airtable_id = cast(str, entry["airtable_id"])
        # One-to-one assignment prevents two extraction cats from silently
        # targeting the same Airtable cat.
        if airtable_id not in airtable or airtable_id in assigned:
            raise PipelineError("cat_mapping contains an unknown or duplicate Airtable cat")
        _require_source_names(entry, extraction[cat_id], airtable[airtable_id])
        assigned.add(airtable_id)


def _validate_review(
    review: dict[str, Any],
    mapping: dict[str, Any],
    extraction: dict[str, dict[str, Any]],
    airtable: dict[str, dict[str, Any]],
) -> None:
    """Validate every extraction-keyed or Airtable-keyed review record."""
    for record_id, value in review.items():
        entry = _match_entry(value, review=True)
        if record_id in extraction:
            _validate_extraction_review(record_id, entry, mapping, extraction)
        elif record_id in airtable:
            _validate_airtable_review(record_id, entry, airtable)
        else:
            raise PipelineError("cat_match_review contains an unknown identity")


def _validate_extraction_review(
    cat_id: str,
    entry: dict[str, Any],
    mapping: dict[str, Any],
    extraction: dict[str, dict[str, Any]],
) -> None:
    """Validate unresolved or bounded-cohort extraction review entries."""
    if entry["extraction_cat_display_name"] != extraction[cat_id].get("display_name"):
        raise PipelineError("cat_match_review has an incorrect extraction display name")
    if cat_id in mapping:
        # Bounded-cohort matches intentionally appear in both artifacts; their
        # review copy must be byte-for-byte equivalent apart from resolution.
        expected = {**mapping[cat_id], "resolution": ""}
        if entry != expected:
            raise PipelineError("bounded-cohort review must copy its cat_mapping entry")
    elif entry["airtable_id"] is not None or entry["airtable_display_name"] is not None:
        raise PipelineError("unresolved extraction review must have null Airtable fields")


def _validate_airtable_review(
    airtable_id: str, entry: dict[str, Any], airtable: dict[str, dict[str, Any]]
) -> None:
    """Validate one unassigned Airtable cat review entry."""
    if entry["extraction_cat_display_name"] is not None or entry["airtable_id"] != airtable_id:
        raise PipelineError("unassigned Airtable review has inconsistent identities")
    if entry["airtable_display_name"] != airtable[airtable_id].get("cat_name"):
        raise PipelineError("unassigned Airtable review has an incorrect display name")


def _validate_coverage(
    mapping: dict[str, Any],
    review: dict[str, Any],
    extraction: dict[str, dict[str, Any]],
    airtable: dict[str, dict[str, Any]],
) -> None:
    """Require every source cat to be assigned or represented for review."""
    # No cat from either authoritative input may disappear merely because Codex
    # was uncertain or failed to mention it.
    if not set(extraction).issubset(mapping.keys() | review.keys()):
        raise PipelineError("Codex output does not cover every extraction cat")
    assigned = {cast(dict[str, Any], entry)["airtable_id"] for entry in mapping.values()}
    if not set(airtable).issubset(assigned | review.keys()):
        raise PipelineError("Codex output does not cover every Airtable cat")


def _match_entry(value: object, *, review: bool) -> dict[str, Any]:
    """Validate one mapping or review entry's exact field contract."""
    entry = _require_object(value, "cat match entry")
    fields = {"extraction_cat_display_name", "airtable_id", "airtable_display_name", "match_reason"}
    expected = fields | ({"resolution"} if review else set())
    if set(entry) != expected or not isinstance(entry.get("match_reason"), str):
        raise PipelineError("cat match entry has invalid fields")
    if not entry["match_reason"]:
        raise PipelineError("cat match reason must be non-empty")
    if review:
        _validate_review_field_values(entry)
    else:
        _validate_mapping_field_values(entry, fields)
    return entry


def _validate_review_field_values(entry: dict[str, Any]) -> None:
    """Require an empty resolution and nullable string identity fields."""
    if entry["resolution"] != "":
        raise PipelineError("cat match review resolution must be empty")
    names = ("extraction_cat_display_name", "airtable_id", "airtable_display_name")
    invalid = any(entry[name] is not None and not isinstance(entry[name], str) for name in names)
    if invalid:
        raise PipelineError("cat match review identity fields must be strings or null")


def _validate_mapping_field_values(entry: dict[str, Any], fields: set[str]) -> None:
    """Require every confident mapping field to be a non-empty string."""
    if any(not isinstance(entry[name], str) or not entry[name] for name in fields):
        raise PipelineError("cat_mapping fields must be non-empty strings")


def _require_source_names(
    entry: dict[str, Any], extraction: dict[str, Any], airtable: dict[str, Any]
) -> None:
    """Require output display names to be copied exactly from source snapshots."""
    if entry["extraction_cat_display_name"] != extraction.get("display_name"):
        raise PipelineError("cat_mapping has an incorrect extraction display name")
    if entry["airtable_display_name"] != airtable.get("cat_name"):
        raise PipelineError("cat_mapping has an incorrect Airtable display name")


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


def _load_object(path: Path, description: str) -> dict[str, Any]:
    """Load one authoritative input artifact as a JSON object."""
    try:
        return _require_object(json.loads(path.read_text(encoding="utf-8")), description)
    except (OSError, json.JSONDecodeError) as exc:
        raise PipelineError(f"could not read {description} artifact") from exc


def _publish_outputs(
    run_directory: Path, mapping: dict[str, Any], review: dict[str, Any]
) -> tuple[Path, Path]:
    """Publish both validated cat outputs and roll back a failed pair."""
    mapping_path = run_directory / MAPPING_FILENAME
    review_path = run_directory / REVIEW_FILENAME
    temporary = (
        run_directory / f".{MAPPING_FILENAME}.tmp",
        run_directory / f".{REVIEW_FILENAME}.tmp",
    )
    if mapping_path.exists() or review_path.exists():
        raise PipelineError("cat matching artifacts already exist")
    try:
        # Both complete documents are staged before either public filename is
        # created. A later failure removes the whole newly created pair.
        temporary[0].write_text(json.dumps(mapping, indent=2) + "\n", encoding="utf-8")
        temporary[1].write_text(json.dumps(review, indent=2) + "\n", encoding="utf-8")
        temporary[0].replace(mapping_path)
        temporary[1].replace(review_path)
    except OSError as exc:
        _remove_matching_outputs((*temporary, mapping_path, review_path))
        raise PipelineError("could not publish cat matching artifacts") from exc
    return mapping_path, review_path


def _remove_matching_outputs(paths: tuple[Path, ...]) -> None:
    """Best-effort remove only incomplete outputs from the current publication."""
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
