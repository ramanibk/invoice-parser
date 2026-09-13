"""Create and publish JSON run artifacts without leaving partial output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROJECTS_ROOT = PROJECT_ROOT.parent
DEFAULT_OUTPUTS = PROJECTS_ROOT / "bac-outputs"


def create_run_directory(
    outputs_dir: Path,
    base_name: str,
    artifact_name: str,
    error_type: type[ValueError],
) -> Path:
    """Reserve a run directory that does not already contain the artifact.

    The unsuffixed name is attempted first, followed by ``.1``, ``.2``, and
    so on. Existing directories are reused when they contain a companion run
    artifact but not ``artifact_name``. This keeps related artifacts together
    without overwriting an earlier artifact of the same type.

    Args:
        outputs_dir: Parent directory that will contain the new run.
        base_name: Validated base name for the run directory.
        artifact_name: File whose presence makes a run directory unavailable.
        error_type: Domain exception class used to report filesystem failures.

    Returns:
        A new or existing run directory reserved for this artifact type.

    Raises:
        ValueError: Via ``error_type`` when the parent or run directory cannot
            be created for a reason other than a name collision.
    """
    try:
        outputs_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise error_type(f"could not create output directory {outputs_dir}: {exc}") from exc
    sequence = 0
    while True:
        suffix = "" if sequence == 0 else f".{sequence}"
        candidate = outputs_dir / f"{base_name}{suffix}"
        if _reserve_candidate(candidate, artifact_name, error_type):
            return candidate
        sequence += 1


def _reserve_candidate(directory: Path, artifact_name: str, error_type: type[ValueError]) -> bool:
    """Create a candidate or allow reuse when only its companion artifact exists."""
    try:
        directory.mkdir()
    except FileExistsError:
        return directory.is_dir() and not (directory / artifact_name).exists()
    except OSError as exc:
        raise error_type(f"could not create output directory {directory}: {exc}") from exc
    return True


def write_json(
    path: Path,
    document: dict[str, Any],
    error_type: type[ValueError],
    description: str,
) -> Path:
    """Serialize a complete document and atomically publish it at ``path``.

    JSON is first written beside the destination, then renamed only after the
    complete serialization succeeds. A failed write removes the temporary file
    and the reserved run directory when it is still empty.

    Args:
        path: Final JSON artifact path inside a reserved run directory.
        document: Complete JSON-serializable payload.
        error_type: Domain exception class used to report filesystem failures.
        description: Short artifact label included in failure messages.

    Returns:
        ``path`` after the atomic rename succeeds.

    Raises:
        ValueError: Via ``error_type`` when the artifact cannot be written or
            published.
    """
    temporary = path.with_suffix(".json.tmp")
    try:
        temporary.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        _remove_empty_directory(path.parent)
        raise error_type(f"could not write {description} {path}: {exc}") from exc
    return path


def _remove_empty_directory(path: Path) -> None:
    """Best-effort remove an empty directory during failed-write cleanup.

    Cleanup errors are ignored so callers receive the original, more useful
    artifact-write failure.
    """
    try:
        path.rmdir()
    except OSError:
        pass
