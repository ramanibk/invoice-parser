"""Tests for resolved pipeline configuration objects."""

from datetime import date
from pathlib import Path

import pytest
from configuration import (
    AirtableConfiguration,
    InputConfiguration,
    OutputConfiguration,
    PipelineConfiguration,
    ReviewConfiguration,
)
from errors import PipelineError


def _inputs(tmp_path: Path) -> InputConfiguration:
    """Build valid input paths without creating the represented files."""
    return InputConfiguration(
        tmp_path / "inputs",
        tmp_path / "service_mapping.json",
        tmp_path / "service_options.json",
    )


def _airtable(**changes: object) -> AirtableConfiguration:
    """Build valid Airtable settings with selected test overrides."""
    values = {
        "token": "secret",
        "base_id": "appBase1",
        "appointments_table_id": "tblAppointments1",
        "cats_table_id": "tblCats1",
    }
    values.update(changes)
    return AirtableConfiguration(**values)  # type: ignore[arg-type]


def _configuration(tmp_path: Path, **changes: object) -> PipelineConfiguration:
    """Build a complete valid pipeline configuration with selected overrides."""
    values = {
        "run_date": date(2026, 9, 3),
        "inputs": _inputs(tmp_path),
        "outputs": OutputConfiguration(tmp_path / "outputs"),
        "airtable": _airtable(),
        "review": ReviewConfiguration(),
    }
    values.update(changes)
    return PipelineConfiguration(**values)  # type: ignore[arg-type]


def test_input_configuration_accepts_resolved_paths_without_reading_them(tmp_path: Path) -> None:
    """Represent absent paths because existence checks belong to preflight."""
    inputs = _inputs(tmp_path)

    assert inputs.directory == tmp_path / "inputs"
    assert not inputs.directory.exists()


@pytest.mark.parametrize(
    ("values", "message"),
    [
        (("inputs", Path("/tmp/map.json"), Path("/tmp/options.json")), "must be a Path"),
        ((Path("inputs"), Path("/tmp/map.json"), Path("/tmp/options.json")), "must be absolute"),
        ((Path("/tmp/in"), Path("/tmp/map.txt"), Path("/tmp/options.json")), "JSON path"),
        ((Path("/tmp/in"), Path("/tmp/map.json"), Path("/tmp/options.txt")), "JSON path"),
    ],
)
def test_rejects_invalid_input_configuration(values: tuple[object, ...], message: str) -> None:
    """Reject unresolved input paths and non-JSON reference filenames."""
    with pytest.raises(PipelineError, match=message):
        InputConfiguration(*values)  # type: ignore[arg-type]


def test_output_configuration_requires_absolute_path() -> None:
    """Reject an output location whose meaning depends on process directory."""
    with pytest.raises(PipelineError, match="output directory must be absolute"):
        OutputConfiguration(Path("outputs"))


def test_airtable_configuration_hides_token_from_representation() -> None:
    """Avoid exposing the Airtable bearer token through configuration repr output."""
    configuration = _airtable()

    assert "secret" not in repr(configuration)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"token": ""}, "token must be non-empty"),
        ({"base_id": "base1"}, "base ID must start with app"),
        ({"appointments_table_id": "tabAppointments"}, "table ID must start with tbl"),
        ({"cats_table_id": "tblCats-1"}, "table ID must start with tbl"),
    ],
)
def test_rejects_invalid_airtable_configuration(changes: dict[str, object], message: str) -> None:
    """Reject missing credentials and malformed stable Airtable schema IDs."""
    with pytest.raises(PipelineError, match=message):
        _airtable(**changes)


@pytest.mark.parametrize("enabled", [1, "yes", None])
def test_review_configuration_requires_boolean(enabled: object) -> None:
    """Reject implicit truthiness that could accidentally bypass review."""
    with pytest.raises(PipelineError, match="review enabled must be a boolean"):
        ReviewConfiguration(enabled)  # type: ignore[arg-type]


def test_pipeline_configuration_groups_all_resolved_settings(tmp_path: Path) -> None:
    """Retain the configured run date and each dedicated settings object."""
    configuration = _configuration(tmp_path)

    assert configuration.run_date == date(2026, 9, 3)
    assert configuration.review.enabled is True


def test_pipeline_configuration_rejects_wrong_run_year(tmp_path: Path) -> None:
    """Reject a date that bypasses configured-year run identity resolution."""
    with pytest.raises(PipelineError, match="run date year must be 2026"):
        _configuration(tmp_path, run_date=date(2025, 9, 3))


@pytest.mark.parametrize(
    ("field_name", "message"),
    [
        ("inputs", "pipeline inputs must be InputConfiguration"),
        ("outputs", "pipeline outputs must be OutputConfiguration"),
        ("airtable", "pipeline Airtable settings must be AirtableConfiguration"),
        ("review", "pipeline review settings must be ReviewConfiguration"),
    ],
)
def test_pipeline_configuration_rejects_wrong_settings_class(
    tmp_path: Path, field_name: str, message: str
) -> None:
    """Reject aggregate settings placed in the wrong configuration slot."""
    with pytest.raises(PipelineError, match=message):
        _configuration(tmp_path, **{field_name: object()})
