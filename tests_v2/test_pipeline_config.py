"""Tests for building and validating invoice-pipeline configuration."""

from datetime import date, datetime
from pathlib import Path

import pytest
from errors import PipelineError
from pipeline_config import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_SERVICE_CATALOG_PATH,
    AirtableConfig,
    InputPaths,
    PipelineConfig,
    build_pipeline_config,
)

AIRTABLE_ENV = {
    "AIRTABLE_TOKEN": "secret",
    "AIRTABLE_BASE_ID": "appBase1",
    "AIRTABLE_APPOINTMENTS_TABLE_ID": "tblAppointments1",
    "AIRTABLE_CATS_TABLE_ID": "tblCats1",
}


def _input_paths(tmp_path: Path) -> InputPaths:
    """Build valid input paths without creating their targets."""
    return InputPaths(
        tmp_path / "inputs",
        tmp_path / "service_catalog.json",
    )


def _airtable(**changes: object) -> AirtableConfig:
    """Build valid Airtable settings with selected test overrides."""
    values = {
        "token": "secret",
        "base_id": "appBase1",
        "appointments_table_id": "tblAppointments1",
        "cats_table_id": "tblCats1",
    }
    values.update(changes)
    return AirtableConfig(**values)  # type: ignore[arg-type]


def _config(tmp_path: Path, **changes: object) -> PipelineConfig:
    """Build a valid typed configuration with selected test overrides."""
    values = {
        "run_date": date(2026, 9, 3),
        "inputs": _input_paths(tmp_path),
        "output_dir": tmp_path / "outputs",
        "airtable": _airtable(),
        "review_enabled": True,
    }
    values.update(changes)
    return PipelineConfig(**values)  # type: ignore[arg-type]


def _build(tmp_path: Path, **changes: object) -> PipelineConfig:
    """Build configuration from valid raw inputs with selected overrides."""
    values = {
        "run_date": date(2026, 9, 3),
        "input_dir": "run-inputs",
        "env": AIRTABLE_ENV,
        "base_dir": tmp_path,
    }
    values.update(changes)
    return build_pipeline_config(**values)  # type: ignore[arg-type]


def test_input_paths_accept_absent_targets(tmp_path: Path) -> None:
    """Represent absent paths because existence checks belong to preflight."""
    inputs = _input_paths(tmp_path)

    assert inputs.input_dir == tmp_path / "inputs"
    assert not inputs.input_dir.exists()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (("inputs", Path("/tmp/catalog.json")), "must be a Path"),
        ((Path("inputs"), Path("/tmp/catalog.json")), "must be absolute"),
        ((Path("/tmp/in"), Path("/tmp/catalog.txt")), "JSON path"),
    ],
)
def test_rejects_invalid_input_paths(values: tuple[object, ...], message: str) -> None:
    """Reject unresolved input paths and non-JSON reference filenames."""
    with pytest.raises(PipelineError, match=message):
        InputPaths(*values)  # type: ignore[arg-type]


def test_airtable_config_hides_token_from_representation() -> None:
    """Avoid exposing the Airtable bearer token through configuration output."""
    assert "secret" not in repr(_airtable())


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"token": ""}, "token must be non-empty"),
        ({"base_id": "base1"}, "base ID must start with app"),
        ({"appointments_table_id": "tabAppointments"}, "table ID must start with tbl"),
        ({"cats_table_id": "tblCats-1"}, "table ID must start with tbl"),
    ],
)
def test_rejects_invalid_airtable_config(changes: dict[str, object], message: str) -> None:
    """Reject missing credentials and malformed Airtable schema IDs."""
    with pytest.raises(PipelineError, match=message):
        _airtable(**changes)


def test_pipeline_config_preserves_typed_settings(tmp_path: Path) -> None:
    """Retain the run date, paths, Airtable settings, and review choice."""
    config = _config(tmp_path)

    assert config.run_date == date(2026, 9, 3)
    assert config.output_dir == tmp_path / "outputs"
    assert config.review_enabled is True


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"run_date": datetime(2026, 9, 3)}, "run date must be a date"),
        ({"run_date": date(2025, 9, 3)}, "run date year must be 2026"),
        ({"inputs": object()}, "pipeline inputs must be InputPaths"),
        ({"output_dir": Path("outputs")}, "output directory must be absolute"),
        ({"airtable": object()}, "pipeline Airtable settings must be AirtableConfig"),
        ({"review_enabled": "yes"}, "review enabled must be a boolean"),
    ],
)
def test_rejects_invalid_pipeline_config(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    """Reject malformed run identity, paths, settings groups, and review values."""
    with pytest.raises(PipelineError, match=message):
        _config(tmp_path, **changes)


def test_builds_defaults_without_inspecting_filesystem(tmp_path: Path) -> None:
    """Build typed settings while allowing all represented paths to remain absent."""
    config = _build(tmp_path)

    assert config.run_date == date(2026, 9, 3)
    assert config.inputs.input_dir == tmp_path / "run-inputs"
    assert config.inputs.service_catalog_path == DEFAULT_SERVICE_CATALOG_PATH
    assert config.output_dir == DEFAULT_OUTPUT_DIR
    assert config.airtable.base_id == "appBase1"
    assert config.review_enabled is True
    assert not config.inputs.input_dir.exists()


def test_builds_relative_path_overrides_from_base_directory(tmp_path: Path) -> None:
    """Give every relative user path one explicit and testable resolution base."""
    config = _build(
        tmp_path,
        output_dir="artifacts",
        service_catalog_path="references/service_catalog.json",
    )

    assert config.output_dir == tmp_path / "artifacts"
    assert config.inputs.service_catalog_path == tmp_path / "references/service_catalog.json"


def test_explicit_airtable_values_override_environment(tmp_path: Path) -> None:
    """Use explicit schema and credential values ahead of environment fallbacks."""
    config = _build(
        tmp_path,
        airtable_token="override-token",
        airtable_base_id="appOverride1",
        airtable_appointments_table_id="tblAppointmentsOverride1",
        airtable_cats_table_id="tblCatsOverride1",
    )

    assert config.airtable.base_id == "appOverride1"
    assert config.airtable.appointments_table_id == "tblAppointmentsOverride1"
    assert config.airtable.cats_table_id == "tblCatsOverride1"
    assert "override-token" not in repr(config)


def test_builds_disabled_review_mode(tmp_path: Path) -> None:
    """Preserve an explicit no-review choice for later readiness checks."""
    assert _build(tmp_path, review_enabled=False).review_enabled is False


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"run_date": "09/03"}, "pipeline run date must be a date"),
        ({"input_dir": ""}, "input directory must be non-empty"),
        ({"output_dir": 12}, "output directory must be a path"),
        ({"service_catalog_path": "catalog.txt"}, "service catalog must be a JSON path"),
        ({"env": {}}, "Airtable token must be non-empty"),
        ({"airtable_base_id": "baseWrong"}, "base ID must start with app"),
        ({"review_enabled": "no"}, "review enabled must be a boolean"),
    ],
)
def test_rejects_malformed_raw_config(
    tmp_path: Path, changes: dict[str, object], message: str
) -> None:
    """Reject invalid paths, credentials, identities, and review flags."""
    with pytest.raises(PipelineError, match=message):
        _build(tmp_path, **changes)
