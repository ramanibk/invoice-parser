"""Tests for read-only pipeline preflight validation."""

from pathlib import Path

import pytest
from errors import PipelineError
from preflight import RunInputFiles, discover_run_input_files


def _create_valid_inputs(tmp_path: Path) -> tuple[Path, Path]:
    """Create the minimum readable files required for input discovery."""
    manifest_path = tmp_path / "manifest.json"
    invoice_path = tmp_path / "Clinic Invoice.PDF"
    manifest_path.write_text("{}", encoding="utf-8")
    invoice_path.write_bytes(b"%PDF-placeholder")
    return manifest_path, invoice_path


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
