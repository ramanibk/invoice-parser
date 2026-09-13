"""Tests for the shared pipeline failure type."""

from errors import PipelineError


def test_pipeline_error_preserves_validation_message() -> None:
    """Expose pipeline failures as value errors with their original message."""
    error = PipelineError("manifest date does not match")

    assert isinstance(error, ValueError)
    assert str(error) == "manifest date does not match"
