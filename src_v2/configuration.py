"""Represent resolved pipeline settings without parsing CLI or environment input."""

import re
from dataclasses import dataclass, field
from datetime import date as Date
from pathlib import Path

from errors import PipelineError
from global_constants import RUN_YEAR
from models_validation import _require_date, _require_text


@dataclass(frozen=True)
class InputConfiguration:
    """Identify the source directory and service reference files for one run."""

    directory: Path
    service_mapping_file: Path
    service_options_file: Path

    def __post_init__(self) -> None:
        """Require resolved input and JSON reference paths without reading them."""
        _require_absolute_path(self.directory, "input directory")
        _require_json_path(self.service_mapping_file, "service mapping file")
        _require_json_path(self.service_options_file, "service options file")


@dataclass(frozen=True)
class OutputConfiguration:
    """Identify the parent directory reserved for published run artifacts."""

    directory: Path

    def __post_init__(self) -> None:
        """Require a resolved output path without creating it."""
        _require_absolute_path(self.directory, "output directory")


@dataclass(frozen=True)
class AirtableConfiguration:
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
class ReviewConfiguration:
    """Select whether the run requires interactive document review."""

    enabled: bool = True

    def __post_init__(self) -> None:
        """Reject truthy substitutes so review behavior remains explicit."""
        if not isinstance(self.enabled, bool):
            raise PipelineError("review enabled must be a boolean")


@dataclass(frozen=True)
class PipelineConfiguration:
    """Bundle the validated settings shared by every pipeline stage."""

    run_date: Date
    inputs: InputConfiguration
    outputs: OutputConfiguration
    airtable: AirtableConfiguration
    review: ReviewConfiguration

    def __post_init__(self) -> None:
        """Require a configured-year date and each typed settings group."""
        _require_date(self.run_date, "pipeline run date")
        if self.run_date.year != RUN_YEAR:
            raise PipelineError(f"pipeline run date year must be {RUN_YEAR}")
        _require_configuration_groups(self)


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


def _require_configuration_groups(configuration: PipelineConfiguration) -> None:
    """Require every aggregate setting to use its dedicated immutable class."""
    groups = (
        (configuration.inputs, InputConfiguration, "pipeline inputs"),
        (configuration.outputs, OutputConfiguration, "pipeline outputs"),
        (configuration.airtable, AirtableConfiguration, "pipeline Airtable settings"),
        (configuration.review, ReviewConfiguration, "pipeline review settings"),
    )
    for value, expected_type, field_name in groups:
        if not isinstance(value, expected_type):
            raise PipelineError(f"{field_name} must be {expected_type.__name__}")
