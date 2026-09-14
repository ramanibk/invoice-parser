"""Validate read-only prerequisites before numbered pipeline stages begin."""

import json
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime as DateTime
from pathlib import Path
from typing import Any

from errors import PipelineError
from models_treatment_sheet import ManifestEntry
from models_validation import _require_date, _require_non_empty_tuple_of
from pipeline_config import PipelineConfig
from pipeline_logging import OutputPlan, plan_output_paths, start_pipeline_log
from resolve_run_identity import make_run_id
from service_catalog import ServiceCatalog, load_service_catalog

MANIFEST_FILENAME = "manifest.json"
MANIFEST_KEYS = frozenset({"date", "treatmentSheets"})
MANIFEST_ENTRY_KEYS = frozenset({"owner", "catName", "fileName"})


@dataclass(frozen=True)
class RunInputFiles:
    """Identify the manifest and invoice discovered for one pipeline run."""

    manifest_path: Path
    invoice_path: Path

    def __post_init__(self) -> None:
        """Require absolute, correctly named files from one input directory."""
        _require_absolute_path(self.manifest_path, "manifest path")
        _require_absolute_path(self.invoice_path, "invoice path")
        if self.manifest_path.name != MANIFEST_FILENAME:
            raise PipelineError(f"manifest path must end with {MANIFEST_FILENAME}")
        if not _is_invoice_pdf(self.invoice_path):
            raise PipelineError("invoice path must be an invoice-named PDF")
        if self.manifest_path.parent != self.invoice_path.parent:
            raise PipelineError("manifest and invoice must share one input directory")


@dataclass(frozen=True)
class RunManifest:
    """Store a validated manifest and its aligned treatment-sheet source paths."""

    run_date: Date
    entries: tuple[ManifestEntry, ...]
    treatment_sheet_paths: tuple[Path, ...]

    def __post_init__(self) -> None:
        """Require typed entries paired in order with absolute PDF paths."""
        _require_date(self.run_date, "manifest date")
        entries = _require_non_empty_tuple_of(self.entries, ManifestEntry, "manifest entries")
        paths = _require_non_empty_tuple_of(
            self.treatment_sheet_paths,
            Path,
            "treatment sheet paths",
        )
        if len(entries) != len(paths):
            raise PipelineError(
                "manifest entries and treatment sheet paths must have equal lengths"
            )
        for entry, path in zip(entries, paths, strict=True):
            _validate_treatment_sheet_path(entry, path)


@dataclass(frozen=True)
class PreflightResult:
    """Carry every validated prerequisite into the first numbered stage."""

    config: PipelineConfig
    input_files: RunInputFiles
    manifest: RunManifest
    service_catalog: ServiceCatalog
    output_plan: OutputPlan

    def __post_init__(self) -> None:
        """Require aligned typed state and an active persistent log."""
        _require_preflight_types(self)
        if self.config.run_date != self.manifest.run_date:
            raise PipelineError("preflight configuration and manifest dates must match")
        if self.input_files.manifest_path.parent != self.config.inputs.input_dir:
            raise PipelineError("preflight input files must belong to the configured directory")
        if self.output_plan.output_dir != self.config.output_dir:
            raise PipelineError("preflight output plan must use the configured output directory")
        _validate_output_identity(self.output_plan, self.config.run_date)
        if not self.output_plan.log_path.is_file():
            raise PipelineError("preflight pipeline log must be active")


def run_preflight(
    config: PipelineConfig,
    *,
    started_at: DateTime | None = None,
) -> PreflightResult:
    """Validate all prerequisites, then start a log without creating a run directory."""
    if not isinstance(config, PipelineConfig):
        raise PipelineError("preflight requires PipelineConfig")
    input_files = discover_run_input_files(config.inputs.input_dir)
    manifest = load_run_manifest(config.run_date, input_files)
    service_catalog = load_service_catalog(config.inputs.service_catalog_path)
    validate_runtime_readiness(config)
    output_plan = plan_output_paths(config.output_dir, config.run_date, started_at=started_at)
    start_pipeline_log(output_plan, _preflight_log_messages(manifest, service_catalog))
    return PreflightResult(config, input_files, manifest, service_catalog, output_plan)


def discover_run_input_files(input_dir: Path) -> RunInputFiles:
    """Find the fixed manifest and sole invoice PDF without changing the input directory."""
    _require_absolute_path(input_dir, "input directory")
    if not input_dir.is_dir():
        raise PipelineError(f"input directory does not exist: {input_dir}")
    entries = _list_input_directory(input_dir)
    manifest_path = input_dir / MANIFEST_FILENAME
    _require_readable_file(manifest_path, "manifest")
    invoice_paths = tuple(path for path in entries if path.is_file() and _is_invoice_pdf(path))
    if len(invoice_paths) != 1:
        count = len(invoice_paths)
        raise PipelineError(
            f"input directory must contain exactly one invoice-named PDF; found {count}"
        )
    _require_readable_file(invoice_paths[0], "invoice")
    return RunInputFiles(manifest_path, invoice_paths[0])


def load_run_manifest(expected_run_date: Date, input_files: RunInputFiles) -> RunManifest:
    """Read and fully validate one manifest and all declared treatment-sheet files."""
    _require_date(expected_run_date, "expected run date")
    if not isinstance(input_files, RunInputFiles):
        raise PipelineError("input files must be RunInputFiles")
    manifest_data = _read_manifest_json(input_files.manifest_path)
    _require_exact_keys(manifest_data, MANIFEST_KEYS, "manifest")
    manifest_date = _parse_manifest_date(manifest_data["date"])
    if manifest_date != expected_run_date:
        raise PipelineError(
            f"expected run date {expected_run_date.isoformat()} does not match "
            f"manifest date {manifest_date.isoformat()}"
        )
    entries = _parse_manifest_entries(manifest_data["treatmentSheets"])
    _require_unique_filenames(entries)
    _reject_invoice_overlap(entries, input_files.invoice_path)
    # Resolve every declared source before constructing the accepted manifest.
    paths = tuple(
        _validated_treatment_sheet_path(input_files.manifest_path.parent, entry)
        for entry in entries
    )
    return RunManifest(manifest_date, entries, paths)


def validate_runtime_readiness(config: PipelineConfig) -> None:
    """Require terminal and PDF-viewer access only for interactive review runs."""
    if not isinstance(config, PipelineConfig):
        raise PipelineError("runtime readiness requires PipelineConfig")
    if not config.review_enabled:
        return
    _require_interactive_terminal()
    _require_pdf_viewer()


def _require_preflight_types(result: PreflightResult) -> None:
    """Require each integrated preflight field to use its dedicated model."""
    expected_types = (
        (result.config, PipelineConfig, "configuration"),
        (result.input_files, RunInputFiles, "input files"),
        (result.manifest, RunManifest, "manifest"),
        (result.service_catalog, ServiceCatalog, "service catalog"),
        (result.output_plan, OutputPlan, "output plan"),
    )
    for value, expected_type, description in expected_types:
        if not isinstance(value, expected_type):
            raise PipelineError(f"preflight {description} must be {expected_type.__name__}")


def _preflight_log_messages(
    manifest: RunManifest,
    service_catalog: ServiceCatalog,
) -> tuple[str, ...]:
    """Build privacy-conscious log entries for an accepted preflight."""
    return (
        f"Preflight passed for {manifest.run_date.isoformat()}.",
        f"Validated {len(manifest.entries)} treatment sheet(s).",
        f"Validated {len(service_catalog.services)} service catalog entries.",
    )


def _validate_output_identity(output_plan: OutputPlan, run_date: Date) -> None:
    """Require the future directory to use the configured run ID and optional sequence."""
    run_id = re.escape(make_run_id(run_date))
    if re.fullmatch(rf"{run_id}(?:\.[1-9]\d*)?", output_plan.run_directory.name) is None:
        raise PipelineError("preflight future run directory must match the configured run ID")


def _require_interactive_terminal() -> None:
    """Require standard input to support interactive review responses."""
    try:
        interactive = sys.stdin.isatty()
    except OSError as exc:
        raise PipelineError(f"could not inspect standard input: {exc}") from exc
    if not interactive:
        raise PipelineError("interactive review requires terminal input; use --no-review to skip")


def _require_pdf_viewer() -> None:
    """Require the macOS command used to open source PDFs for review."""
    if shutil.which("open") is None:
        raise PipelineError("interactive review requires the macOS 'open' command")


def _list_input_directory(input_dir: Path) -> tuple[Path, ...]:
    """List an input directory once and report access failures as pipeline errors."""
    try:
        return tuple(input_dir.iterdir())
    except OSError as exc:
        raise PipelineError(f"could not inspect input directory {input_dir}: {exc}") from exc


def _read_manifest_json(manifest_path: Path) -> dict[str, Any]:
    """Read a UTF-8 manifest whose root value must be a JSON object."""
    try:
        value = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PipelineError(f"could not read manifest {manifest_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PipelineError("manifest must contain a JSON object")
    return value


def _parse_manifest_date(value: object) -> Date:
    """Parse a manifest date only when it uses canonical ISO form."""
    if not isinstance(value, str):
        raise PipelineError("manifest date must use YYYY-MM-DD format")
    try:
        parsed = Date.fromisoformat(value)
    except ValueError as exc:
        raise PipelineError("manifest date must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise PipelineError("manifest date must use YYYY-MM-DD format")
    return parsed


def _parse_manifest_entries(value: object) -> tuple[ManifestEntry, ...]:
    """Parse a non-empty manifest treatment-sheet array in source order."""
    if not isinstance(value, list) or not value:
        raise PipelineError("manifest treatmentSheets must be a non-empty array")
    return tuple(_parse_manifest_entry(item, index) for index, item in enumerate(value, start=1))


def _parse_manifest_entry(value: object, index: int) -> ManifestEntry:
    """Parse one exactly shaped manifest treatment-sheet entry."""
    location = f"treatmentSheets[{index}]"
    if not isinstance(value, dict):
        raise PipelineError(f"{location} must be an object")
    _require_exact_keys(value, MANIFEST_ENTRY_KEYS, location)
    return ManifestEntry(
        owner_name=_required_manifest_text(value, "owner", location),
        cat_name=_required_manifest_text(value, "catName", location),
        filename=_required_manifest_text(value, "fileName", location),
    )


def _required_manifest_text(value: dict[str, Any], key: str, location: str) -> str:
    """Return one required manifest string with surrounding whitespace removed."""
    field = value[key]
    if not isinstance(field, str) or not field.strip():
        raise PipelineError(f"{location}.{key} must be a non-empty string")
    return field.strip()


def _require_exact_keys(value: dict[str, Any], expected: frozenset[str], location: str) -> None:
    """Reject missing and unknown JSON object fields with a stable message."""
    actual = set(value)
    if actual != expected:
        required = ", ".join(sorted(expected))
        raise PipelineError(f"{location} must contain exactly these fields: {required}")


def _require_unique_filenames(entries: tuple[ManifestEntry, ...]) -> None:
    """Reject treatment-sheet filenames repeated with any letter casing."""
    filenames = [entry.filename.casefold() for entry in entries]
    if len(filenames) != len(set(filenames)):
        raise PipelineError("manifest treatment sheet filenames must be unique")


def _reject_invoice_overlap(entries: tuple[ManifestEntry, ...], invoice_path: Path) -> None:
    """Reject a manifest that declares the discovered invoice as a treatment sheet."""
    invoice_name = invoice_path.name.casefold()
    if any(entry.filename.casefold() == invoice_name for entry in entries):
        raise PipelineError("invoice PDF cannot also be a treatment sheet")


def _validated_treatment_sheet_path(input_dir: Path, entry: ManifestEntry) -> Path:
    """Return one declared treatment-sheet path after read-only validation."""
    path = input_dir / entry.filename
    _require_readable_file(path, "treatment sheet")
    return path


def _validate_treatment_sheet_path(entry: ManifestEntry, path: Path) -> None:
    """Require an absolute PDF path aligned with its manifest filename."""
    _require_absolute_path(path, "treatment sheet path")
    if path.name != entry.filename:
        raise PipelineError("treatment sheet path must match its manifest filename")


def _require_readable_file(path: Path, description: str) -> None:
    """Require a regular file that can be opened without modifying it."""
    if not path.is_file():
        raise PipelineError(f"input directory must contain {path.name}")
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise PipelineError(f"could not read {description} file {path}: {exc}") from exc


def _is_invoice_pdf(path: Path) -> bool:
    """Return whether a filename identifies an invoice PDF candidate."""
    return path.suffix.casefold() == ".pdf" and "invoice" in path.name.casefold()


def _require_absolute_path(value: object, field_name: str) -> None:
    """Require an absolute Path without checking whether it exists."""
    if not isinstance(value, Path):
        raise PipelineError(f"{field_name} must be a Path")
    if not value.is_absolute():
        raise PipelineError(f"{field_name} must be absolute")
