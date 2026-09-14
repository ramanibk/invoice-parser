"""Tests for output planning and persistent pipeline log startup."""

from datetime import date, datetime, timezone
from pathlib import Path

import pipeline_logging
import pytest
from errors import PipelineError
from pipeline_logging import OutputPlan, plan_output_paths, start_pipeline_log

STARTED_AT = datetime(2026, 9, 13, 14, 30, 5, 123456, tzinfo=timezone.utc)


def test_plans_future_run_and_timestamped_log_without_creating_them(tmp_path: Path) -> None:
    """Select stable output identities without creating either destination."""
    output_dir = tmp_path / "outputs"

    plan = plan_output_paths(output_dir, date(2026, 9, 3), started_at=STARTED_AT)

    assert plan.run_directory == output_dir / "26SEP03-NLF"
    assert plan.log_path == output_dir / "logs/20260913T143005123456+0000-26SEP03-NLF.log"
    assert not output_dir.exists()


def test_plans_numbered_run_after_existing_identity(tmp_path: Path) -> None:
    """Avoid overwriting an existing published run directory."""
    output_dir = tmp_path / "outputs"
    (output_dir / "26SEP03-NLF").mkdir(parents=True)

    plan = plan_output_paths(output_dir, date(2026, 9, 3), started_at=STARTED_AT)

    assert plan.run_directory == output_dir / "26SEP03-NLF.1"


def test_starts_log_without_creating_future_run_directory(tmp_path: Path) -> None:
    """Persist complete initial messages while leaving publication unreserved."""
    plan = plan_output_paths(tmp_path / "outputs", date(2026, 9, 3), started_at=STARTED_AT)

    log_path = start_pipeline_log(plan, ("Preflight passed.", "Validated 2 sheets."))

    assert log_path.read_text(encoding="utf-8") == ("Preflight passed.\nValidated 2 sheets.\n")
    assert not plan.run_directory.exists()


def test_numbers_colliding_log_without_changing_extension(tmp_path: Path) -> None:
    """Preserve both persistent logs when two starts share one timestamp."""
    output_dir = tmp_path / "outputs"
    first_plan = plan_output_paths(output_dir, date(2026, 9, 3), started_at=STARTED_AT)
    start_pipeline_log(first_plan, ("First preflight passed.",))

    second_plan = plan_output_paths(output_dir, date(2026, 9, 3), started_at=STARTED_AT)

    assert second_plan.log_path.name.endswith("-26SEP03-NLF.1.log")


def test_rejects_output_path_that_is_a_file(tmp_path: Path) -> None:
    """Reject a prospective output parent that cannot contain run artifacts."""
    output_path = tmp_path / "outputs"
    output_path.write_text("occupied", encoding="utf-8")

    with pytest.raises(PipelineError, match="output path is not a directory"):
        plan_output_paths(output_path, date(2026, 9, 3), started_at=STARTED_AT)


def test_rejects_unwritable_output_ancestor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail readiness when the closest existing output ancestor is not writable."""
    monkeypatch.setattr(pipeline_logging.os, "access", lambda _path, _mode: False)

    with pytest.raises(PipelineError, match="output directory is not writable"):
        plan_output_paths(tmp_path / "missing/outputs", date(2026, 9, 3), started_at=STARTED_AT)


def test_rejects_naive_log_timestamp(tmp_path: Path) -> None:
    """Require timestamp identities that are unambiguous across time zones."""
    with pytest.raises(PipelineError, match="start time must be timezone-aware"):
        plan_output_paths(
            tmp_path / "outputs",
            date(2026, 9, 3),
            started_at=datetime(2026, 9, 13, 14, 30),
        )


def test_rejects_misaligned_output_plan(tmp_path: Path) -> None:
    """Protect future publication paths from escaping the configured output root."""
    with pytest.raises(PipelineError, match="future run directory must belong"):
        OutputPlan(
            tmp_path / "outputs",
            tmp_path / "elsewhere/26SEP03-NLF",
            tmp_path / "outputs/logs/run.log",
        )
