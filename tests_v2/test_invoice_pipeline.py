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
from models_airtable import AirtableCatRecord, AirtableSnapshot
from models_invoice import Invoice, InvoiceAppointment, InvoiceServiceLine
from models_treatment_sheet import TreatmentCat, TreatmentSheetAppointment

AIRTABLE_ENV = {
    "AIRTABLE_TOKEN": "secret",
    "AIRTABLE_BASE_ID": "appBase1",
    "AIRTABLE_APPOINTMENTS_TABLE_ID": "tblAppointments1",
    "AIRTABLE_CATS_TABLE_ID": "tblCats1",
}


@pytest.fixture(autouse=True)
def _replace_external_matching_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replace Airtable and Codex calls with deterministic complete results."""
    snapshot = AirtableSnapshot(
        date(2026, 9, 3),
        "NLF",
        "Nine Lives Foundation",
        1,
        (
            AirtableCatRecord(
                "recCat1",
                "recAppointment1",
                "Sample Cat",
                "Pet",
                appointment_owner_or_trapper="Sample Owner",
            ),
        ),
    )
    monkeypatch.setattr(invoice_pipeline, "query_airtable_snapshot", lambda *_args: snapshot)

    def match(run_directory: Path) -> tuple[Path, Path, int]:
        """Create representative validated outputs for orchestration tests."""
        matches = run_directory / "cat_matches.json"
        review = run_directory / "cat_match_review.json"
        matches.write_text("[]\n", encoding="utf-8")
        review.write_text("[]\n", encoding="utf-8")
        return matches, review, 0

    monkeypatch.setattr(invoice_pipeline, "run_codex_cat_matching", match)


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
        "(F) Sample Cat",
        "26-7001",
        "Sample Owner",
        "(F) Sample Cat (26-7001) Sample Owner",
        (service,),
        Decimal("125.00"),
    )
    return Invoice(path, (appointment,), Decimal("125.00"))


def _parsed_records() -> tuple[TreatmentCat, ...]:
    """Return one valid treatment-sheet record for CLI orchestration tests."""
    appointment = TreatmentSheetAppointment(date(2026, 9, 3), "Female", None, "Black", None)
    return (
        TreatmentCat(
            "26SEP03-NLF-1",
            "Sample Cat",
            "Sample Cat",
            "Sample Owner",
            (appointment,),
        ),
    )


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
    monkeypatch.setattr(
        invoice_pipeline, "extract_treatment_sheets", lambda _manifest: _parsed_records()
    )
    monkeypatch.setattr(invoice_pipeline, "review_extraction", record_review)

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
    assert "Stage 2: Treatment-sheet extraction" in captured.out
    assert "1 treatment sheet(s) parsed and identity-checked" in captured.out
    assert "Stage 3: Invoice-to-treatment-sheet mapping" in captured.out
    assert "1 latest appointment(s) matched one-to-one" in captured.out
    assert "Extraction artifact published:" in captured.out
    assert "Stage 4: Airtable retrieval" in captured.out
    assert "Stage 5: Codex cat matching" in captured.out
    assert '"source_file"' not in captured.out
    assert '"medical_findings"' not in captured.out
    assert captured.err == ""
    artifact_path = output_dir / "26SEP03-NLF" / "extraction.json"
    assert artifact_path.is_file()
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    assert artifact["cats"][0]["appointments"]["2026-09-03"]["services"] == {"Spay / Neuter": 125.0}
    assert artifact["invoice"]["total_cost"] == "125.00"
    assert (artifact_path.parent / "needs_invoice.json").is_file()
    assert (artifact_path.parent / "cat_matches.json").is_file()
    assert (artifact_path.parent / "cat_match_review.json").is_file()
    log_paths = tuple((output_dir / "logs").glob("*.log"))
    assert len(log_paths) == 1
    log_text = log_paths[0].read_text(encoding="utf-8")
    assert f"Input directory: {input_dir}" in log_text
    assert f"Future run directory: {output_dir / '26SEP03-NLF'}" in log_text
    assert AIRTABLE_ENV["AIRTABLE_TOKEN"] not in log_text
    assert "Stage 1 invoice extraction passed." in log_text
    assert "Stage 2 treatment-sheet extraction passed." in log_text
    assert "Stage 3 invoice-to-treatment-sheet mapping passed." in log_text
    assert "Matched 1 latest appointment(s) one-to-one." in log_text
    assert review_calls == [
        (input_dir / "clinic-invoice.pdf", "invoice", False),
        (
            input_dir / "sample-cat.pdf",
            "treatment sheet sample-cat.pdf",
            False,
        ),
    ]


def test_review_prints_each_extraction_before_opening_its_pdf(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print invoice and sheet values before handing each source to review."""
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_run_inputs(input_dir)
    _set_airtable_environment(monkeypatch)
    events = []
    monkeypatch.setattr(preflight, "validate_runtime_readiness", lambda _config: None)
    monkeypatch.setattr(invoice_pipeline, "parse_invoice", _parsed_invoice)
    monkeypatch.setattr(
        invoice_pipeline, "extract_treatment_sheets", lambda _manifest: _parsed_records()
    )
    monkeypatch.setattr(
        invoice_pipeline,
        "print_invoice_extraction",
        lambda _invoice: events.append("print invoice"),
    )
    monkeypatch.setattr(
        invoice_pipeline,
        "print_treatment_sheet_extraction",
        lambda _record, _path: events.append("print treatment sheet"),
    )
    monkeypatch.setattr(
        invoice_pipeline,
        "review_extraction",
        lambda _path, description, *, review_enabled: events.append(
            f"review {description} {review_enabled}"
        ),
    )

    status = main(["--date", "09/03", "--outputs-dir", str(output_dir), str(input_dir)])

    assert status == 0
    assert capsys.readouterr().err == ""
    assert events == [
        "print invoice",
        "review invoice True",
        "print treatment sheet",
        "review treatment sheet sample-cat.pdf True",
    ]


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
        "review_extraction",
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


def test_cli_reports_treatment_sheet_failure_without_partial_run_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Stop after Stage 1 when the treatment-sheet batch fails validation."""
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_run_inputs(input_dir)
    _set_airtable_environment(monkeypatch)
    review_calls = []
    monkeypatch.setattr(invoice_pipeline, "parse_invoice", _parsed_invoice)
    monkeypatch.setattr(
        invoice_pipeline,
        "review_extraction",
        lambda path, description, *, review_enabled: review_calls.append(
            (path, description, review_enabled)
        ),
    )

    def fail_sheets(_manifest: object) -> tuple[TreatmentCat, ...]:
        """Simulate a manifest identity mismatch after invoice success."""
        raise PipelineError("sample-cat.pdf: owner mismatch")

    monkeypatch.setattr(invoice_pipeline, "extract_treatment_sheets", fail_sheets)

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
    assert "Stage 1 complete." in captured.out
    assert "sample-cat.pdf: owner mismatch" in captured.err
    assert review_calls == [(input_dir / "clinic-invoice.pdf", "invoice", False)]
    assert not (output_dir / "26SEP03-NLF").exists()


def test_cli_reports_mapping_failure_without_partial_run_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Reject cross-source identity mismatches before publishing extraction JSON."""
    input_dir = tmp_path / "inputs"
    output_dir = tmp_path / "outputs"
    _write_run_inputs(input_dir)
    _set_airtable_environment(monkeypatch)
    invoice = _parsed_invoice(input_dir / "clinic-invoice.pdf")
    mismatched = _parsed_records()[0]
    records = (
        TreatmentCat(
            mismatched.cat_id,
            "Different Cat",
            "Different Cat",
            mismatched.owner_name,
            mismatched.appointments,
        ),
    )
    monkeypatch.setattr(invoice_pipeline, "parse_invoice", lambda _path: invoice)
    monkeypatch.setattr(invoice_pipeline, "extract_treatment_sheets", lambda _manifest: records)
    monkeypatch.setattr(invoice_pipeline, "review_extraction", lambda *_args, **_kwargs: None)

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
    assert "no invoice appointment matches" in captured.err
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
