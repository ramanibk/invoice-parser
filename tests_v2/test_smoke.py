"""Smoke tests for the clean invoice-pipeline skeleton."""

import invoice_pipeline


def test_invoice_pipeline_imports() -> None:
    """Import the rewritten pipeline root from the flat source directory."""
    assert invoice_pipeline.__doc__
