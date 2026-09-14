"""Validate read-only prerequisites before numbered pipeline stages begin."""

from dataclasses import dataclass
from pathlib import Path

from errors import PipelineError

MANIFEST_FILENAME = "manifest.json"


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


def _list_input_directory(input_dir: Path) -> tuple[Path, ...]:
    """List an input directory once and report access failures as pipeline errors."""
    try:
        return tuple(input_dir.iterdir())
    except OSError as exc:
        raise PipelineError(f"could not inspect input directory {input_dir}: {exc}") from exc


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
