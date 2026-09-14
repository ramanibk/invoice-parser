"""Open source PDFs and collect explicit terminal review decisions."""

import subprocess
from pathlib import Path

from errors import PipelineError
from models_validation import _require_text


def review_extraction(pdf_path: Path, description: str, *, review_enabled: bool = True) -> None:
    """Open one source PDF and require approval of its printed extraction."""
    if not isinstance(review_enabled, bool):
        raise PipelineError("review enabled must be a boolean")
    if not review_enabled:
        return
    _validate_review_input(pdf_path, description)
    _open_pdf(pdf_path, description)
    _confirm_extraction(description)


def _validate_review_input(pdf_path: object, description: object) -> None:
    """Require an absolute readable PDF and a concise review description."""
    if not isinstance(pdf_path, Path) or not pdf_path.is_absolute():
        raise PipelineError("review PDF path must be an absolute Path")
    if pdf_path.suffix.casefold() != ".pdf" or not pdf_path.is_file():
        raise PipelineError(f"review source must be an existing PDF: {pdf_path}")
    _require_text(description, "review description")


def _open_pdf(pdf_path: Path, description: str) -> None:
    """Launch the configured system PDF viewer and report launch failures."""
    try:
        completed = subprocess.run(("open", str(pdf_path)), check=False)
    except OSError as exc:
        raise PipelineError(f"could not open {description} for review: {exc}") from exc
    if completed.returncode != 0:
        raise PipelineError(
            f"could not open {description} for review: viewer exited with "
            f"status {completed.returncode}"
        )


def _confirm_extraction(description: str) -> None:
    """Require approval after the operator compares printed data with its PDF."""
    try:
        response = input(f"Approve {description} extraction? [y/N] ")
    except (EOFError, OSError) as exc:
        raise PipelineError(f"could not confirm {description} extraction review") from exc
    if response.strip().casefold() not in {"y", "yes"}:
        raise PipelineError(f"{description} extraction was not approved")
