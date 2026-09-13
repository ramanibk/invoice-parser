"""Tests for shared output-path configuration."""

from treatment_sheet_parser.shared.output import DEFAULT_OUTPUTS, PROJECT_ROOT


def test_default_outputs_use_separate_bac_outputs_directory() -> None:
    """Keep generated artifacts outside the parser and input repositories."""
    assert DEFAULT_OUTPUTS == PROJECT_ROOT.parent / "bac-outputs"
