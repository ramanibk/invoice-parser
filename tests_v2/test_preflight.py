"""Tests for read-only pipeline preflight validation."""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import preflight
import pytest
from errors import PipelineError
from models_treatment_sheet import RunManifest
from pipeline_config import AirtableConfig, InputPaths, PipelineConfig
from pipeline_logging import OutputPlan
from preflight import (
    PreflightResult,
    RunInputFiles,
    discover_run_input_files,
    load_run_manifest,
    run_preflight,
    validate_runtime_readiness,
)
from service_catalog import ServiceCatalog


def _create_valid_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """Create the minimum readable files required for input discovery."""
    manifest_path = tmp_path / "manifest.json"
    invoice_path = tmp_path / "Clinic Invoice.PDF"
    manifest_path.write_text("{}", encoding="utf-8")
    invoice_path.write_bytes(b"%PDF-placeholder")
    return manifest_path, invoice_path


def _write_valid_manifest(tmp_path: Path, entries: list[dict[str, object]] | None = None) -> None:
    """Write a representative manifest and each declared treatment-sheet PDF."""
    sheets = entries or [
        {
            "owner": "Alexa Camorlinga",
            "catName": "Nebula",
            "fileName": "Nebula.pdf",
        }
    ]
    (tmp_path / "manifest.json").write_text(
        json.dumps({"date": "2026-09-03", "treatmentSheets": sheets}),
        encoding="utf-8",
    )
    for sheet in sheets:
        filename = sheet.get("fileName")
        if isinstance(filename, str) and Path(filename).name == filename:
            (tmp_path / filename).write_bytes(b"%PDF-placeholder")


def _valid_discovered_inputs(tmp_path: Path) -> RunInputFiles:
    """Create and discover valid manifest and invoice source files."""
    _write_valid_manifest(tmp_path)
    (tmp_path / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")
    return discover_run_input_files(tmp_path)


def _pipeline_config(tmp_path: Path, *, review_enabled: bool) -> PipelineConfig:
    """Build valid runtime settings for review-readiness tests."""
    return PipelineConfig(
        run_date=date(2026, 9, 3),
        inputs=InputPaths(
            tmp_path / "inputs",
            tmp_path / "service_catalog.json",
        ),
        output_dir=tmp_path / "outputs",
        airtable=AirtableConfig("secret", "appBase1", "tblAppointments1", "tblCats1"),
        review_enabled=review_enabled,
    )


def _integrated_config(tmp_path: Path, *, catalog_path: Path) -> PipelineConfig:
    """Build complete noninteractive settings for integrated preflight tests."""
    return PipelineConfig(
        run_date=date(2026, 9, 3),
        inputs=InputPaths(tmp_path / "inputs", catalog_path),
        output_dir=tmp_path / "outputs",
        airtable=AirtableConfig("secret", "appBase1", "tblAppointments1", "tblCats1"),
        review_enabled=False,
    )


def _write_minimal_catalog(path: Path) -> None:
    """Write a complete two-field service catalog for integrated tests."""
    catalog = {
        "format_version": "1.0",
        "services": [
            {
                "airtable_field_name": "Services",
                "airtable_service_option": "Spay / Neuter",
                "recognized_invoice_descriptions": ["Cat Spay"],
            },
            {
                "airtable_field_name": "Additional Services",
                "airtable_service_option": "Pregnant",
                "recognized_invoice_descriptions": ["Pregnant surcharge"],
            },
        ],
    }
    path.write_text(json.dumps(catalog), encoding="utf-8")


def test_discovers_manifest_and_one_invoice_without_changes(tmp_path: Path) -> None:
    """Return exact source paths while leaving directory contents unchanged."""
    manifest_path, invoice_path = _create_valid_inputs(tmp_path)
    treatment_sheet_path = tmp_path / "Nebula.pdf"
    treatment_sheet_path.write_bytes(b"%PDF-placeholder")
    names_before = {path.name for path in tmp_path.iterdir()}

    discovered = discover_run_input_files(tmp_path)

    assert discovered == RunInputFiles(manifest_path, invoice_path)
    assert {path.name for path in tmp_path.iterdir()} == names_before


@pytest.mark.parametrize("input_dir", ["inputs", Path("inputs")])
def test_rejects_unresolved_input_directory(input_dir: object) -> None:
    """Require the configuration layer to provide an absolute Path."""
    with pytest.raises(PipelineError, match="input directory must"):
        discover_run_input_files(input_dir)  # type: ignore[arg-type]


def test_rejects_missing_input_directory(tmp_path: Path) -> None:
    """Reject an absolute input path that does not identify a directory."""
    missing = tmp_path / "missing"

    with pytest.raises(PipelineError, match="input directory does not exist"):
        discover_run_input_files(missing)


def test_rejects_missing_manifest(tmp_path: Path) -> None:
    """Require the exact manifest filename in every input directory."""
    (tmp_path / "clinic-invoice.pdf").write_bytes(b"%PDF-placeholder")

    with pytest.raises(PipelineError, match="must contain manifest.json"):
        discover_run_input_files(tmp_path)


def test_rejects_manifest_directory(tmp_path: Path) -> None:
    """Reject a directory masquerading as the required manifest file."""
    (tmp_path / "manifest.json").mkdir()
    (tmp_path / "clinic-invoice.pdf").write_bytes(b"%PDF-placeholder")

    with pytest.raises(PipelineError, match="must contain manifest.json"):
        discover_run_input_files(tmp_path)


def test_rejects_missing_invoice(tmp_path: Path) -> None:
    """Reject inputs without an invoice-named PDF candidate."""
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "receipt.pdf").write_bytes(b"%PDF-placeholder")

    with pytest.raises(PipelineError, match="invoice-named PDF; found 0"):
        discover_run_input_files(tmp_path)


def test_discovers_nlf_invoice_filename(tmp_path: Path) -> None:
    """Recognize the dated invoice number, location, and total filename format."""
    manifest_path = tmp_path / "manifest.json"
    invoice_path = tmp_path / "2026-09-02 5100 Nine Lives Foundation $505.00.pdf"
    manifest_path.write_text("{}", encoding="utf-8")
    invoice_path.write_bytes(b"%PDF-placeholder")

    assert discover_run_input_files(tmp_path) == RunInputFiles(manifest_path, invoice_path)


@pytest.mark.parametrize(
    "invoice_name",
    [
        "2026-09-02 Nine Lives Foundation $505.00.pdf",
        "2026-09-02 5100 Nine Lives Foundation 505.00.pdf",
        "2026-09-02 5100 Nine Lives Foundation $505.pdf",
    ],
)
def test_rejects_malformed_nlf_invoice_filename(tmp_path: Path, invoice_name: str) -> None:
    """Reject NLF-like filenames missing a required invoice identity component."""
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / invoice_name).write_bytes(b"%PDF-placeholder")

    with pytest.raises(PipelineError, match="invoice-named PDF; found 0"):
        discover_run_input_files(tmp_path)


def test_rejects_ambiguous_invoices(tmp_path: Path) -> None:
    """Reject multiple invoice PDF candidates instead of guessing between them."""
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "first invoice.pdf").write_bytes(b"%PDF-placeholder")
    (tmp_path / "INVOICE-second.PDF").write_bytes(b"%PDF-placeholder")

    with pytest.raises(PipelineError, match="invoice-named PDF; found 2"):
        discover_run_input_files(tmp_path)


@pytest.mark.parametrize(
    ("manifest_name", "invoice_name", "message"),
    [
        ("run.json", "invoice.pdf", "manifest path must end with manifest.json"),
        ("manifest.json", "receipt.pdf", "invoice path must be an invoice-named PDF"),
    ],
)
def test_rejects_malformed_discovery_result(
    tmp_path: Path,
    manifest_name: str,
    invoice_name: str,
    message: str,
) -> None:
    """Protect the discovered-file contract from incorrectly named paths."""
    with pytest.raises(PipelineError, match=message):
        RunInputFiles(tmp_path / manifest_name, tmp_path / invoice_name)


def test_rejects_discovery_result_from_different_directories(tmp_path: Path) -> None:
    """Require the manifest and invoice to belong to one run input directory."""
    with pytest.raises(PipelineError, match="must share one input directory"):
        RunInputFiles(tmp_path / "manifest.json", tmp_path / "other" / "invoice.pdf")


def test_loads_complete_manifest_in_source_order(tmp_path: Path) -> None:
    """Return validated entries aligned with their readable treatment sheets."""
    entries = [
        {"owner": " Alexa ", "catName": " Nebula ", "fileName": "Nebula.pdf"},
        {"owner": "N/A", "catName": "Miso", "fileName": "Miso.pdf"},
    ]
    _write_valid_manifest(tmp_path, entries)
    (tmp_path / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")
    input_files = discover_run_input_files(tmp_path)

    manifest = load_run_manifest(date(2026, 9, 3), input_files)

    assert isinstance(manifest, RunManifest)
    assert manifest.run_date == date(2026, 9, 3)
    assert [entry.cat_name for entry in manifest.entries] == ["Nebula", "Miso"]
    assert manifest.treatment_sheet_paths == (tmp_path / "Nebula.pdf", tmp_path / "Miso.pdf")


def test_loads_manifest_with_valid_downloader_metadata(tmp_path: Path) -> None:
    """Accept a complete downloader manifest when its counts confirm all sheets succeeded."""
    entries = [{"owner": "Alexa", "catName": "Nebula", "fileName": "Nebula.pdf"}]
    payload = {
        "date": "2026-09-03",
        "invoiceCount": 1,
        "total": 1,
        "completed": 1,
        "treatmentSheets": entries,
        "failures": [],
    }
    _write_valid_manifest(tmp_path, entries)
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")

    manifest = load_run_manifest(date(2026, 9, 3), discover_run_input_files(tmp_path))

    assert len(manifest.entries) == 1


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        ({"invoiceCount": 2, "total": 1, "completed": 1, "failures": []}, "invoiceCount"),
        ({"invoiceCount": 1, "total": 2, "completed": 1, "failures": []}, "total"),
        ({"invoiceCount": 1, "total": 1, "completed": 0, "failures": []}, "completed"),
        ({"invoiceCount": 1, "total": 1, "completed": 1, "failures": ["failed"]}, "failures"),
    ],
)
def test_rejects_inconsistent_manifest_metadata(
    tmp_path: Path,
    metadata: dict[str, object],
    message: str,
) -> None:
    """Reject metadata that signals missing inputs or disagrees with validated sheets."""
    entries = [{"owner": "Alexa", "catName": "Nebula", "fileName": "Nebula.pdf"}]
    payload = {"date": "2026-09-03", "treatmentSheets": entries, **metadata}
    _write_valid_manifest(tmp_path, entries)
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")

    with pytest.raises(PipelineError, match=message):
        load_run_manifest(date(2026, 9, 3), discover_run_input_files(tmp_path))


def test_rejects_manifest_date_identity_mismatch(tmp_path: Path) -> None:
    """Reject a manifest belonging to a different configured run date."""
    input_files = _valid_discovered_inputs(tmp_path)

    with pytest.raises(PipelineError, match="expected run date 2026-09-04 does not match"):
        load_run_manifest(date(2026, 9, 4), input_files)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "manifest must contain a JSON object"),
        ({"date": "2026-09-03"}, "manifest must contain either its two core fields"),
        (
            {"date": "2026-09-03", "treatmentSheets": [], "extra": True},
            "manifest must contain either its two core fields",
        ),
        (
            {"date": "09/03/2026", "treatmentSheets": [{}]},
            "manifest date must use YYYY-MM-DD",
        ),
        (
            {"date": "2026-09-03", "treatmentSheets": []},
            "treatmentSheets must be a non-empty array",
        ),
        (
            {"date": "2026-09-03", "treatmentSheets": ["Nebula.pdf"]},
            r"treatmentSheets\[1\] must be an object",
        ),
        (
            {"date": "2026-09-03", "treatmentSheets": [{"owner": "Alexa"}]},
            r"treatmentSheets\[1\] must contain exactly these fields",
        ),
        (
            {
                "date": "2026-09-03",
                "treatmentSheets": [{"owner": "", "catName": "Nebula", "fileName": "Nebula.pdf"}],
            },
            r"treatmentSheets\[1\].owner must be a non-empty string",
        ),
    ],
)
def test_rejects_malformed_manifest_json(
    tmp_path: Path,
    payload: object,
    message: str,
) -> None:
    """Reject malformed manifest roots, fields, dates, arrays, and entries."""
    (tmp_path / "manifest.json").write_text(json.dumps(payload), encoding="utf-8")
    (tmp_path / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")
    input_files = discover_run_input_files(tmp_path)

    with pytest.raises(PipelineError, match=message):
        load_run_manifest(date(2026, 9, 3), input_files)


def test_rejects_invalid_manifest_json_syntax(tmp_path: Path) -> None:
    """Translate JSON decoding failures into a manifest-specific pipeline error."""
    (tmp_path / "manifest.json").write_text("{not JSON", encoding="utf-8")
    (tmp_path / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")
    input_files = discover_run_input_files(tmp_path)

    with pytest.raises(PipelineError, match="could not read manifest"):
        load_run_manifest(date(2026, 9, 3), input_files)


def test_rejects_duplicate_treatment_sheet_filenames(tmp_path: Path) -> None:
    """Reject duplicate manifest files even when only letter casing differs."""
    entries = [
        {"owner": "Alexa", "catName": "Nebula", "fileName": "Nebula.pdf"},
        {"owner": "Alexa", "catName": "Miso", "fileName": "NEBULA.PDF"},
    ]
    _write_valid_manifest(tmp_path, entries)
    (tmp_path / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")
    input_files = discover_run_input_files(tmp_path)

    with pytest.raises(PipelineError, match="filenames must be unique"):
        load_run_manifest(date(2026, 9, 3), input_files)


def test_rejects_missing_treatment_sheet(tmp_path: Path) -> None:
    """Reject a manifest when any declared treatment sheet is unavailable."""
    input_files = _valid_discovered_inputs(tmp_path)
    (tmp_path / "Nebula.pdf").unlink()

    with pytest.raises(PipelineError, match="must contain Nebula.pdf"):
        load_run_manifest(date(2026, 9, 3), input_files)


def test_rejects_invoice_declared_as_treatment_sheet(tmp_path: Path) -> None:
    """Prevent the invoice PDF from also being processed as a treatment sheet."""
    entries = [{"owner": "Alexa", "catName": "Nebula", "fileName": "clinic invoice.pdf"}]
    _write_valid_manifest(tmp_path, entries)
    input_files = discover_run_input_files(tmp_path)

    with pytest.raises(PipelineError, match="cannot also be a treatment sheet"):
        load_run_manifest(date(2026, 9, 3), input_files)


def test_no_review_skips_terminal_and_viewer_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Allow noninteractive execution when review was explicitly disabled."""
    monkeypatch.setattr(preflight.sys, "stdin", Mock(isatty=Mock(return_value=False)))
    monkeypatch.setattr(
        preflight.shutil, "which", lambda command: "/usr/bin/codex" if command == "codex" else None
    )

    assert validate_runtime_readiness(_pipeline_config(tmp_path, review_enabled=False)) is None


def test_all_runs_require_codex_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject a run before processing when automatic matching cannot start."""
    monkeypatch.setattr(preflight.shutil, "which", lambda _command: None)

    with pytest.raises(PipelineError, match="requires the 'codex' command"):
        validate_runtime_readiness(_pipeline_config(tmp_path, review_enabled=False))


def test_review_requires_interactive_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject interactive review when standard input is not a terminal."""
    monkeypatch.setattr(preflight.sys, "stdin", Mock(isatty=Mock(return_value=False)))
    monkeypatch.setattr(preflight.shutil, "which", lambda _command: "/usr/bin/tool")

    with pytest.raises(PipelineError, match="interactive review requires terminal input"):
        validate_runtime_readiness(_pipeline_config(tmp_path, review_enabled=True))


def test_review_requires_pdf_viewer_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reject interactive review when the platform PDF opener is unavailable."""
    monkeypatch.setattr(preflight.sys, "stdin", Mock(isatty=Mock(return_value=True)))
    monkeypatch.setattr(
        preflight.shutil, "which", lambda command: "/usr/bin/codex" if command == "codex" else None
    )

    with pytest.raises(PipelineError, match="requires the macOS 'open' command"):
        validate_runtime_readiness(_pipeline_config(tmp_path, review_enabled=True))


def test_review_accepts_terminal_and_pdf_viewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Accept review mode when terminal input and the PDF opener are available."""
    monkeypatch.setattr(preflight.sys, "stdin", Mock(isatty=Mock(return_value=True)))
    monkeypatch.setattr(preflight.shutil, "which", lambda _command: "/usr/bin/open")

    assert validate_runtime_readiness(_pipeline_config(tmp_path, review_enabled=True)) is None


def test_integrated_preflight_returns_complete_state_and_starts_log(tmp_path: Path) -> None:
    """Return aligned prerequisites and log success before any numbered stage begins."""
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    _write_valid_manifest(input_dir)
    (input_dir / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")
    catalog_path = tmp_path / "service_catalog.json"
    _write_minimal_catalog(catalog_path)
    config = _integrated_config(tmp_path, catalog_path=catalog_path)

    result = run_preflight(
        config,
        started_at=datetime(2026, 9, 13, 14, 30, tzinfo=timezone.utc),
    )

    assert isinstance(result, PreflightResult)
    assert isinstance(result.service_catalog, ServiceCatalog)
    assert result.manifest.run_date == config.run_date
    assert result.output_plan.log_path.is_file()
    assert "Validated 1 treatment sheet(s)." in result.output_plan.log_path.read_text(
        encoding="utf-8"
    )
    assert not result.output_plan.run_directory.exists()


def test_integrated_preflight_failure_creates_no_output(tmp_path: Path) -> None:
    """Leave no log or run directory when a late read-only prerequisite is malformed."""
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    _write_valid_manifest(input_dir)
    (input_dir / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")
    catalog_path = tmp_path / "service_catalog.json"
    catalog_path.write_text("{}", encoding="utf-8")
    config = _integrated_config(tmp_path, catalog_path=catalog_path)

    with pytest.raises(PipelineError, match="service catalog must contain exactly"):
        run_preflight(config)

    assert not config.output_dir.exists()


def test_integrated_preflight_rejects_input_identity_mismatch(tmp_path: Path) -> None:
    """Reject a configuration whose source files belong to another input directory."""
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    _write_valid_manifest(input_dir)
    (input_dir / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")
    catalog_path = tmp_path / "service_catalog.json"
    _write_minimal_catalog(catalog_path)
    config = _integrated_config(tmp_path, catalog_path=catalog_path)
    result = run_preflight(config)
    other_config = PipelineConfig(
        run_date=config.run_date,
        inputs=InputPaths(tmp_path / "other-inputs", catalog_path),
        output_dir=config.output_dir,
        airtable=config.airtable,
        review_enabled=False,
    )

    with pytest.raises(PipelineError, match="must belong to the configured directory"):
        PreflightResult(
            other_config,
            result.input_files,
            result.manifest,
            result.service_catalog,
            result.output_plan,
        )


def test_integrated_preflight_rejects_output_identity_mismatch(tmp_path: Path) -> None:
    """Reject a future run directory derived from a different run identity."""
    input_dir = tmp_path / "inputs"
    input_dir.mkdir()
    _write_valid_manifest(input_dir)
    (input_dir / "clinic invoice.pdf").write_bytes(b"%PDF-placeholder")
    catalog_path = tmp_path / "service_catalog.json"
    _write_minimal_catalog(catalog_path)
    config = _integrated_config(tmp_path, catalog_path=catalog_path)
    result = run_preflight(config)
    mismatched_plan = OutputPlan(
        result.output_plan.output_dir,
        result.output_plan.output_dir / "26SEP04-NLF",
        result.output_plan.log_path,
    )

    with pytest.raises(PipelineError, match="must match the configured run ID"):
        PreflightResult(
            result.config,
            result.input_files,
            result.manifest,
            result.service_catalog,
            mismatched_plan,
        )
