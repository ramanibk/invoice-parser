"""Plan output identities and start a persistent pipeline log safely."""

import os
from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime as DateTime
from pathlib import Path

from errors import PipelineError
from global_constants import LOG_DIRECTORY_NAME
from models_validation import (
    _require_date,
    _require_non_empty_tuple_of,
    _require_text,
    _require_tuple_of,
)
from resolve_run_identity import make_run_id


@dataclass(frozen=True)
class OutputPlan:
    """Identify future publication and persistent log paths for one run."""

    output_dir: Path
    run_directory: Path
    log_path: Path

    def __post_init__(self) -> None:
        """Require absolute, correctly nested output paths."""
        _require_absolute_path(self.output_dir, "output directory")
        _require_absolute_path(self.run_directory, "future run directory")
        _require_absolute_path(self.log_path, "pipeline log path")
        if self.run_directory.parent != self.output_dir:
            raise PipelineError("future run directory must belong to the output directory")
        if self.log_path.parent != self.output_dir / LOG_DIRECTORY_NAME:
            raise PipelineError("pipeline log must belong to the output log directory")
        if self.log_path.suffix != ".log":
            raise PipelineError("pipeline log path must end with .log")


def plan_output_paths(
    output_dir: Path,
    run_date: Date,
    *,
    started_at: DateTime | None = None,
) -> OutputPlan:
    """Validate output readiness and select paths without creating either path."""
    _require_absolute_path(output_dir, "output directory")
    _require_date(run_date, "output run date")
    _validate_output_directory(output_dir)
    effective_start = DateTime.now().astimezone() if started_at is None else started_at
    _require_datetime(effective_start)
    run_id = make_run_id(run_date)
    run_directory = _next_available_path(output_dir, run_id)
    log_name = f"{effective_start:%Y%m%dT%H%M%S%f%z}-{run_directory.name}.log"
    log_path = _next_available_log_path(output_dir / LOG_DIRECTORY_NAME, log_name)
    return OutputPlan(output_dir, run_directory, log_path)


def start_pipeline_log(
    plan: OutputPlan,
    messages: tuple[str, ...],
    *,
    sensitive_values: tuple[str, ...] = (),
) -> Path:
    """Create a new log with every supplied sensitive value redacted."""
    if not isinstance(plan, OutputPlan):
        raise PipelineError("pipeline log requires an OutputPlan")
    entries = _require_non_empty_tuple_of(messages, str, "pipeline log messages")
    for message in entries:
        _require_text(message, "pipeline log message")
    sanitized_entries = _redact_sensitive_values(entries, sensitive_values)
    _create_log_parent(plan)
    try:
        with plan.log_path.open("x", encoding="utf-8") as log_file:
            for message in sanitized_entries:
                log_file.write(f"{message}\n")
    except OSError as exc:
        _remove_partial_log(plan.log_path)
        _remove_empty_log_directories(plan)
        raise PipelineError(f"could not start pipeline log {plan.log_path}: {exc}") from exc
    return plan.log_path


def _redact_sensitive_values(
    messages: tuple[str, ...],
    sensitive_values: tuple[str, ...],
) -> tuple[str, ...]:
    """Replace every non-empty sensitive value before messages reach disk."""
    secrets = _require_tuple_of(sensitive_values, str, "sensitive log values")
    for secret in secrets:
        _require_text(secret, "sensitive log value")
    return tuple(_redact_message(message, secrets) for message in messages)


def _redact_message(message: str, secrets: tuple[str, ...]) -> str:
    """Return one log message with all exact sensitive values removed."""
    sanitized = message
    for secret in secrets:
        sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized


def _validate_output_directory(output_dir: Path) -> None:
    """Require an existing directory or a writable ancestor for a future one."""
    if output_dir.exists() and not output_dir.is_dir():
        raise PipelineError(f"output path is not a directory: {output_dir}")
    writable_parent = output_dir if output_dir.is_dir() else _nearest_existing_parent(output_dir)
    if not os.access(writable_parent, os.W_OK | os.X_OK):
        raise PipelineError(f"output directory is not writable: {output_dir}")


def _nearest_existing_parent(path: Path) -> Path:
    """Return the first existing ancestor of a prospective output directory."""
    candidate = path.parent
    while not candidate.exists():
        candidate = candidate.parent
    if not candidate.is_dir():
        raise PipelineError(f"output directory has no usable parent: {path}")
    return candidate


def _next_available_path(parent: Path, name: str) -> Path:
    """Return an unused path, adding a numeric suffix after collisions."""
    candidate = parent / name
    sequence = 1
    while candidate.exists():
        candidate = parent / f"{name}.{sequence}"
        sequence += 1
    return candidate


def _next_available_log_path(parent: Path, name: str) -> Path:
    """Return an unused log path while preserving its final .log extension."""
    path = Path(name)
    candidate = parent / name
    sequence = 1
    while candidate.exists():
        candidate = parent / f"{path.stem}.{sequence}{path.suffix}"
        sequence += 1
    return candidate


def _create_log_parent(plan: OutputPlan) -> None:
    """Create only the output and log directories, never the future run directory."""
    try:
        plan.log_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PipelineError(f"could not create pipeline log directory: {exc}") from exc


def _remove_empty_log_directories(plan: OutputPlan) -> None:
    """Best-effort remove directories created for a log that failed to start."""
    for directory in (plan.log_path.parent, plan.output_dir):
        try:
            directory.rmdir()
        except OSError:
            return


def _remove_partial_log(log_path: Path) -> None:
    """Best-effort remove an incomplete log while preserving the original failure."""
    try:
        log_path.unlink(missing_ok=True)
    except OSError:
        pass


def _require_datetime(value: object) -> None:
    """Require a timezone-aware datetime for an unambiguous log identity."""
    if not isinstance(value, DateTime) or value.tzinfo is None or value.utcoffset() is None:
        raise PipelineError("pipeline log start time must be timezone-aware")


def _require_absolute_path(value: object, field_name: str) -> None:
    """Require an absolute Path without checking whether it exists."""
    if not isinstance(value, Path):
        raise PipelineError(f"{field_name} must be a Path")
    if not value.is_absolute():
        raise PipelineError(f"{field_name} must be absolute")
