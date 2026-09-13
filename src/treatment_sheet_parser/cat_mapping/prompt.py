"""Locate matching run artifacts and generate a copy-ready Codex prompt."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from treatment_sheet_parser.cat_mapping.errors import CatMappingPromptError
from treatment_sheet_parser.shared.locations import LOCATION_NAMES
from treatment_sheet_parser.shared.output import DEFAULT_OUTPUTS, PROJECT_ROOT
from treatment_sheet_parser.shared.run_id import make_run_id

PROMPTS_ROOT = PROJECT_ROOT / "src" / "prompts"


@dataclass(frozen=True)
class CatMappingPaths:
    """Absolute input, instruction, and output paths for one paired run."""

    extraction: Path
    needs_invoice: Path
    invariants: Path
    matching_instructions: Path
    resolution_instructions: Path
    mapping: Path
    review: Path


def generate_cat_mapping_prompt(
    appointment_date: str,
    location: str,
    outputs_dir: str | Path = DEFAULT_OUTPUTS,
    *,
    resolve: bool = False,
) -> str:
    """Return one first-pass or resolution prompt for the newest complete run.

    A complete run directory contains both ``extraction.json`` and
    ``needs_invoice.json``. When numbered reruns exist, the greatest numeric
    suffix is selected and its two snapshots must pass identity validation.

    Raises:
        CatMappingPromptError: If arguments, artifacts, JSON, or run identities
            are invalid or no paired run exists.
    """
    code = _location_code(location)
    run_id = _run_id(appointment_date, code)
    run_directory = _latest_complete_run(Path(outputs_dir), run_id)
    paths = _mapping_paths(run_directory)
    extraction = _load_object(paths.extraction)
    needs_invoice = _load_object(paths.needs_invoice)
    _validate_extraction(extraction, appointment_date, code, run_directory.name)
    _validate_needs_invoice(needs_invoice, appointment_date, code)
    if resolve:
        _validate_resolution_artifacts(paths)
        return _resolution_prompt(appointment_date, code, paths)
    return _first_pass_prompt(appointment_date, code, paths)


def _location_code(value: str) -> str:
    """Normalize and validate a supported location abbreviation."""
    code = value.strip().upper()
    if code not in LOCATION_NAMES:
        supported = ", ".join(sorted(LOCATION_NAMES))
        raise CatMappingPromptError(f"unknown location code {value!r}; supported: {supported}")
    return code


def _run_id(appointment_date: str, code: str) -> str:
    """Build a run identity while converting date failures to the domain error."""
    try:
        return make_run_id(appointment_date, code)
    except ValueError as exc:
        raise CatMappingPromptError(str(exc)) from exc


def _latest_complete_run(outputs_dir: Path, run_id: str) -> Path:
    """Return the highest-sequence run containing both required snapshots."""
    try:
        candidates = [
            (sequence, child)
            for child in outputs_dir.iterdir()
            if (sequence := _run_sequence(child, run_id)) is not None
            and (child / "extraction.json").is_file()
            and (child / "needs_invoice.json").is_file()
        ]
    except OSError as exc:
        raise CatMappingPromptError(
            f"could not inspect outputs directory {outputs_dir}: {exc}"
        ) from exc
    if not candidates:
        raise CatMappingPromptError(
            f"no paired extraction and Needs Invoice run found for {run_id}"
        )
    return max(candidates, key=lambda item: item[0])[1]


def _run_sequence(path: Path, run_id: str) -> int | None:
    """Return a canonical run suffix, or ``None`` for an unrelated path."""
    if not path.is_dir():
        return None
    if path.name == run_id:
        return 0
    prefix = f"{run_id}."
    suffix = path.name.removeprefix(prefix)
    if not path.name.startswith(prefix) or not suffix.isdigit():
        return None
    sequence = int(suffix)
    return sequence if sequence > 0 and suffix == str(sequence) else None


def _mapping_paths(run_directory: Path) -> CatMappingPaths:
    """Construct absolute paths used by the generated prompt."""
    run = run_directory.resolve()
    return CatMappingPaths(
        extraction=run / "extraction.json",
        needs_invoice=run / "needs_invoice.json",
        invariants=(PROMPTS_ROOT / "invariants.md").resolve(),
        matching_instructions=(PROMPTS_ROOT / "cat_matching_instructions.md").resolve(),
        resolution_instructions=(PROMPTS_ROOT / "cat_match_resolution_instructions.md").resolve(),
        mapping=run / "cat_mapping.json",
        review=run / "cat_match_review.json",
    )


def _load_object(path: Path) -> dict[str, Any]:
    """Load one required JSON object without modifying the artifact."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatMappingPromptError(f"could not read valid JSON object from {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise CatMappingPromptError(f"JSON artifact must contain an object: {path}")
    return document


def _validate_extraction(
    document: dict[str, Any], appointment_date: str, code: str, run_id: str
) -> None:
    """Require extraction metadata to match the requested run identity."""
    parameters = document.get("input_parameters")
    identity = (document.get("run_id"), parameters)
    expected = (run_id, {"date": appointment_date, "vet": code})
    if not isinstance(parameters, dict) or identity != expected:
        raise CatMappingPromptError("extraction.json identity does not match the requested run")
    if not isinstance(document.get("cats"), list):
        raise CatMappingPromptError("extraction.json is missing its cats array")


def _validate_needs_invoice(document: dict[str, Any], appointment_date: str, code: str) -> None:
    """Require the Airtable snapshot to match the requested date and location."""
    identity = (document.get("date"), document.get("location_code"))
    if identity != (appointment_date, code):
        raise CatMappingPromptError("needs_invoice.json identity does not match the requested run")
    if not isinstance(document.get("cats"), list):
        raise CatMappingPromptError("needs_invoice.json is missing its cats array")


def _validate_resolution_artifacts(paths: CatMappingPaths) -> None:
    """Require valid mapping and review objects before generating a resolution prompt."""
    for path in (paths.mapping, paths.review):
        _load_object(path)


def _first_pass_prompt(appointment_date: str, code: str, paths: CatMappingPaths) -> str:
    """Render the concise first-pass prompt."""
    return f"""Create the first-pass cat mapping for {code} on {appointment_date}.

Read and follow: {paths.invariants}
Follow: {paths.matching_instructions}
Extraction: {paths.extraction}
Needs Invoice: {paths.needs_invoice}
Write mapping: {paths.mapping}
Write review: {paths.review}

Follow the instructions exactly, validate both outputs before writing, and do not modify the input
files or fill the review `resolution` fields.
"""


def _resolution_prompt(appointment_date: str, code: str, paths: CatMappingPaths) -> str:
    """Render the concise operator-resolution prompt."""
    return f"""Resolve the cat matches for {code} on {appointment_date}.

Read and follow: {paths.invariants}
Follow: {paths.resolution_instructions}
Extraction: {paths.extraction}
Needs Invoice: {paths.needs_invoice}
Mapping: {paths.mapping}
Review with my resolutions: {paths.review}

Apply the non-empty `resolution` fields exactly, validate the complete result before updating the
mapping, and leave the review file unchanged.
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Parse a run identity and print its copy-ready Codex prompt."""
    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    try:
        prompt = generate_cat_mapping_prompt(
            arguments.date,
            arguments.location,
            arguments.outputs_dir,
            resolve=arguments.resolve,
        )
    except CatMappingPromptError as exc:
        parser.error(str(exc))
    print(prompt)
    return 0


def _argument_parser() -> argparse.ArgumentParser:
    """Build the small prompt-generator command-line interface."""
    parser = argparse.ArgumentParser(description="Generate a copy-ready Codex cat-mapping prompt")
    stage = parser.add_mutually_exclusive_group(required=True)
    stage.add_argument("--first-pass", action="store_true")
    stage.add_argument("--resolve", action="store_true")
    parser.add_argument("date", metavar="YYYY-MM-DD")
    parser.add_argument("location", metavar="LOCATION_CODE")
    parser.add_argument("--outputs-dir", type=Path, default=DEFAULT_OUTPUTS)
    return parser
