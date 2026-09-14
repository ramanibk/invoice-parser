"""Tests for the canonical invoice-pipeline command."""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import invoice_pipeline
import preflight
import pytest
from errors import PipelineError
from invoice_pipeline import main
from models_invoice import Invoice, InvoiceAppointment, InvoiceServiceLine

AIRTABLE_ENV = {
    "AIRTABLE_TOKEN": "secret",
    "AIRTABLE_BASE_ID": "appBase1",
    "AIRTABLE_APPOINTMENTS_TABLE_ID": "tblAppointments1",
    "AIRTABLE_CATS_TABLE_ID": "tblCats1",
}


def _write_run_inputs(input_dir: Path, *, manifest_date: str = "2026-09-03") -> None:
    """Create a complete synthetic input set for CLI tests."""
    input_dir.mkdir()
    manifest = {
        "date": manifest_date,
        "treatmentSheets": [
            {
                "owner": "Sample Owner",
                "catName": "Sample Cat",
                "fileName": "sample-cat.pdf",
            }
        ],
    }
    (input_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (input_dir / "clinic-invoice.pdf").write_bytes(b"%PDF-placeholder")
    (input_dir / "sample-cat.pdf").write_bytes(b"%PDF-placeholder")


def _set_airtable_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide syntactically valid read-only Airtable settings to the command."""
    for name, value in AIRTABLE_ENV.items():
        monkeypatch.setenv(name, value)


def _clear_airtable_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every Airtable fallback to exercise explicit CLI overrides."""
    for name in AIRTABLE_ENV:
        monkeypatch.delenv(name, raising=False)


def _parsed_invoice(path: Path) -> Invoice:
    """Return a valid typed invoice for CLI orchestration tests."""
    service = InvoiceServiceLine("Cat Spay", Decimal("125.00"))
    appointment = InvoiceAppointment(
        date(2026, 9, 3),
        "Sample Cat, Sample Owner",
        (service,),
        Decimal("125.00"),
    )
    return Invoice(path, (appointment,), Decimal("125.00"))


def test_cli_runs_preflight_and_prints_complete_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Report validated inputs, planned output, and the current stopping point."""
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_run_inputs(input_dir)
    _set_airtable_environment(monkeypatch)
    review_calls = []

    def record_review(path: Path, description: str, *, review_enabled: bool) -> None:
        """Record the review handoff after parsing succeeds."""
        review_calls.append((path, description, review_enabled))

    monkeypatch.setattr(invoice_pipeline, "parse_invoice", _parsed_invoice)
    monkeypatch.setattr(invoice_pipeline, "review_pdf", record_review)

    status = main(
        [
            "--date",
            "09/03",
            "--no-review",
            "--outputs-dir",
            str(output_dir),
            str(input_dir),
        ]
    )

    captured = capsys.readouterr()
    assert status == 0
    assert "Invoice pipeline" in captured.out
    assert "[ok] Invoice found: clinic-invoice.pdf" in captured.out
    assert "[ok] 1 treatment sheet(s) validated" in captured.out
    assert "[ok] 67 service catalog entries validated" in captured.out
    assert "Stage 1: Invoice extraction" in captured.out
    assert "Invoice total validated: USD 125.00" in captured.out
    assert "later extraction stages are not implemented yet" in captured.out
    assert captured.err == ""
    assert not (output_dir / "26SEP03-NLF").exists()
    log_paths = tuple((output_dir / "logs").glob("*.log"))
    assert len(log_paths) == 1
    log_text = log_paths[0].read_text(encoding="utf-8")
    assert f"Input directory: {input_dir}" in log_text
    assert f"Future run directory: {output_dir / '26SEP03-NLF'}" in log_text
    assert AIRTABLE_ENV["AIRTABLE_TOKEN"] not in log_text
    assert "Stage 1 invoice extraction passed." in log_text
    assert review_calls == [(input_dir / "clinic-invoice.pdf", "invoice PDF", False)]


def test_cli_reports_invoice_failure_without_partial_run_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop after preflight when invoice extraction fails without creating a run."""
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_run_inputs(input_dir)
    _set_airtable_environment(monkeypatch)

    def fail_parse(_path: Path) -> Invoice:
        """Simulate a structurally invalid invoice after successful preflight."""
        raise PipelineError("invoice appointment header is malformed")

    monkeypatch.setattr(invoice_pipeline, "parse_invoice", fail_parse)
    monkeypatch.setattr(
        invoice_pipeline,
        "review_pdf",
        lambda *_args, **_kwargs: pytest.fail("invalid invoices must not reach review"),
    )

    status = main(
        [
            "--date",
            "09/03",
            "--no-review",
            "--outputs-dir",
            str(output_dir),
            str(input_dir),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "invoice appointment header is malformed" in captured.err
    assert not (output_dir / "26SEP03-NLF").exists()


def test_cli_reports_preflight_failure_without_partial_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Return a controlled error when manifest identity does not match the run."""
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_run_inputs(input_dir, manifest_date="2026-09-04")
    _set_airtable_environment(monkeypatch)

    status = main(
        [
            "--date",
            "09/03",
            "--no-review",
            "--outputs-dir",
            str(output_dir),
            str(input_dir),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "does not match manifest date 2026-09-04" in captured.err
    assert not output_dir.exists()


def test_cli_reports_malformed_date_without_starting_pipeline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject a non-MM/DD date before resolving or inspecting any paths."""
    _set_airtable_environment(monkeypatch)

    status = main(["--date", "2026-09-03", "--no-review", str(tmp_path / "missing")])

    captured = capsys.readouterr()
    assert status == 1
    assert captured.out == ""
    assert "run date must use MM/DD format" in captured.err


def test_cli_requires_airtable_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject absent Airtable settings without making a network request."""
    _clear_airtable_environment(monkeypatch)

    status = main(["--date", "09/03", "--no-review", str(tmp_path / "missing")])

    captured = capsys.readouterr()
    assert status == 1
    assert "Airtable token must be non-empty" in captured.err


def test_cli_help_exposes_only_operator_safe_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Keep secrets, schema IDs, and internal reference paths out of CLI options."""
    with pytest.raises(SystemExit, match="0"):
        main(["--help"])

    help_text = capsys.readouterr().out
    assert "--date" in help_text
    assert "--outputs-dir" in help_text
    assert "--no-review" in help_text
    assert "--airtable" not in help_text
    assert "--service-catalog" not in help_text


def test_cli_default_review_requires_interactive_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Explain how to bypass review readiness in a noninteractive invocation."""
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_run_inputs(input_dir)
    _set_airtable_environment(monkeypatch)
    monkeypatch.setattr(preflight.sys, "stdin", Mock(isatty=Mock(return_value=False)))

    status = main(["--date", "09/03", "--outputs-dir", str(output_dir), str(input_dir)])

    captured = capsys.readouterr()
    assert status == 1
    assert "use --no-review to skip" in captured.err
    assert not output_dir.exists()
