"""Build and validate configuration for one invoice-pipeline run."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path

from errors import PipelineError
from global_constants import RUN_YEAR
from models_validation import _require_date, _require_text

AIRTABLE_ENV_VARS = {
    "token": "AIRTABLE_TOKEN",
    "base_id": "AIRTABLE_BASE_ID",
    "appointments_table_id": "AIRTABLE_APPOINTMENTS_TABLE_ID",
    "cats_table_id": "AIRTABLE_CATS_TABLE_ID",
}
PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_OUTPUT_DIR = PROJECT_DIR.parent / "bac-outputs"
DEFAULT_SERVICE_MAP_PATH = Path(__file__).with_name("service_mapping.json")
DEFAULT_SERVICE_OPTIONS_PATH = Path(__file__).with_name("airtable_service_options.json")


@dataclass(frozen=True)
class InputPaths:
    """Identify the source directory and service reference files for one run."""

    input_dir: Path
    service_map_path: Path
    service_options_path: Path

    def __post_init__(self) -> None:
        """Require resolved input and JSON reference paths without reading them."""
        _require_absolute_path(self.input_dir, "input directory")
        _require_json_path(self.service_map_path, "service map")
        _require_json_path(self.service_options_path, "service options")


@dataclass(frozen=True)
class AirtableConfig:
    """Hold credentials and stable schema identities for read-only Airtable work."""

    token: str = field(repr=False)
    base_id: str
    appointments_table_id: str
    cats_table_id: str

    def __post_init__(self) -> None:
        """Require a token and syntactically valid Airtable base and table IDs."""
        _require_text(self.token, "Airtable token")
        _require_airtable_id(self.base_id, "app", "Airtable base ID")
        _require_airtable_id(self.appointments_table_id, "tbl", "Airtable appointments table ID")
        _require_airtable_id(self.cats_table_id, "tbl", "Airtable cats table ID")


@dataclass(frozen=True)
class PipelineConfig:
    """Bundle all validated settings shared by the pipeline stages."""

    run_date: Date
    inputs: InputPaths
    output_dir: Path
    airtable: AirtableConfig
    review_enabled: bool = True

    def __post_init__(self) -> None:
        """Require a configured-year date and correctly typed settings."""
        _require_date(self.run_date, "pipeline run date")
        if self.run_date.year != RUN_YEAR:
            raise PipelineError(f"pipeline run date year must be {RUN_YEAR}")
        if not isinstance(self.inputs, InputPaths):
            raise PipelineError("pipeline inputs must be InputPaths")
        _require_absolute_path(self.output_dir, "output directory")
        if not isinstance(self.airtable, AirtableConfig):
            raise PipelineError("pipeline Airtable settings must be AirtableConfig")
        if not isinstance(self.review_enabled, bool):
            raise PipelineError("review enabled must be a boolean")


def build_pipeline_config(
    run_date: Date,
    input_dir: str | Path,
    *,
    output_dir: str | Path | None = None,
    service_map_path: str | Path | None = None,
    service_options_path: str | Path | None = None,
    airtable_token: str | None = None,
    airtable_base_id: str | None = None,
    airtable_appointments_table_id: str | None = None,
    airtable_cats_table_id: str | None = None,
    review_enabled: bool = True,
    env: Mapping[str, str] | None = None,
    base_dir: Path | None = None,
) -> PipelineConfig:
    """Build configuration from a confirmed run date without inspecting any path."""
    env_values = os.environ if env is None else env
    resolved_base_dir = Path.cwd() if base_dir is None else base_dir
    inputs = InputPaths(
        _resolve_path(input_dir, resolved_base_dir, "input directory"),
        _resolve_optional_path(
            service_map_path,
            DEFAULT_SERVICE_MAP_PATH,
            resolved_base_dir,
            "service map",
        ),
        _resolve_optional_path(
            service_options_path,
            DEFAULT_SERVICE_OPTIONS_PATH,
            resolved_base_dir,
            "service options",
        ),
    )
    airtable = _build_airtable_config(
        env_values,
        airtable_token,
        airtable_base_id,
        airtable_appointments_table_id,
        airtable_cats_table_id,
    )
    return PipelineConfig(
        run_date=run_date,
        inputs=inputs,
        output_dir=_resolve_optional_path(
            output_dir,
            DEFAULT_OUTPUT_DIR,
            resolved_base_dir,
            "output directory",
        ),
        airtable=airtable,
        review_enabled=review_enabled,
    )


def _build_airtable_config(
    env: Mapping[str, str],
    token: str | None,
    base_id: str | None,
    appointments_table_id: str | None,
    cats_table_id: str | None,
) -> AirtableConfig:
    """Prefer explicit Airtable values and otherwise use named environment entries."""
    overrides = {
        "token": token,
        "base_id": base_id,
        "appointments_table_id": appointments_table_id,
        "cats_table_id": cats_table_id,
    }
    airtable_values = {
        name: value if value is not None else env.get(AIRTABLE_ENV_VARS[name], "")
        for name, value in overrides.items()
    }
    return AirtableConfig(**airtable_values)


def _resolve_optional_path(
    value: str | Path | None,
    default: Path,
    base_dir: Path,
    field_name: str,
) -> Path:
    """Resolve an optional path, using its stable project default when omitted."""
    return _resolve_path(default if value is None else value, base_dir, field_name)


def _resolve_path(value: object, base_dir: Path, field_name: str) -> Path:
    """Expand and absolutize one textual or Path input without requiring it to exist."""
    if not isinstance(value, (str, Path)):
        raise PipelineError(f"{field_name} must be a path")
    if isinstance(value, str) and not value.strip():
        raise PipelineError(f"{field_name} must be non-empty")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _require_absolute_path(value: object, field_name: str) -> None:
    """Require an absolute Path without checking filesystem state."""
    if not isinstance(value, Path):
        raise PipelineError(f"{field_name} must be a Path")
    if not value.is_absolute():
        raise PipelineError(f"{field_name} must be absolute")


def _require_json_path(value: object, field_name: str) -> None:
    """Require an absolute path whose filename has a JSON extension."""
    _require_absolute_path(value, field_name)
    if value.suffix.casefold() != ".json":
        raise PipelineError(f"{field_name} must be a JSON path")


def _require_airtable_id(value: object, prefix: str, field_name: str) -> None:
    """Require an alphanumeric Airtable schema ID with its expected prefix."""
    if not isinstance(value, str) or re.fullmatch(rf"{prefix}[A-Za-z0-9]+", value) is None:
        raise PipelineError(f"{field_name} must start with {prefix} and be alphanumeric")
