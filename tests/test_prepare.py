"""Tests for the combined NLF prepare command."""

from pathlib import Path

import pytest

from treatment_sheet_parser import prepare
from treatment_sheet_parser.needs_invoice.query import NeedsInvoiceResult


def _arguments(tmp_path: Path) -> list[str]:
    """Return a complete prepare CLI argument list with explicit Airtable IDs."""
    inputs = tmp_path / "inputs"
    inputs.mkdir(exist_ok=True)
    (inputs / "manifest.json").touch()
    (inputs / "clinic invoice.pdf").touch()
    return [
        "--date",
        "2026-09-03",
        "--outputs-dir",
        str(tmp_path / "outputs"),
        "--base-id",
        "appBase",
        "--table-id",
        "tblAppointments",
        "--cats-table-id",
        "tblCats",
        str(inputs),
    ]


def test_prepare_runs_query_extraction_save_and_prompt_in_order(
    tmp_path, monkeypatch, capsys
) -> None:
    """Run every stage once and print only the generated first-pass prompt."""
    events = []
    run = tmp_path / "outputs" / "26SEP03-NLF"
    result = NeedsInvoiceResult("2026-09-03", "NLF", "Nine Lives Foundation", 0, 0, ())

    monkeypatch.setattr(prepare, "AirtableClient", lambda *args, **kwargs: object())

    def query(*args, **kwargs):
        """Record the query stage and return its prepared result."""
        events.append("query")
        return result

    def extract(*args, **kwargs):
        """Record extraction and verify its resolved source paths."""
        events.append("extract")
        assert args[0] == tmp_path / "inputs" / "manifest.json"
        assert kwargs["invoice_path"] == tmp_path / "inputs" / "clinic invoice.pdf"
        return run / "extraction.json"

    def save(*args, **kwargs):
        """Record publication of the prepared Airtable snapshot."""
        events.append("save")
        return run / "needs_invoice.json"

    def prompt(*args, **kwargs):
        """Record prompt generation and return recognizable output."""
        events.append("prompt")
        return "mapping prompt"

    monkeypatch.setattr(prepare, "query_needs_invoice", query)
    monkeypatch.setattr(prepare, "extract_manifest", extract)
    monkeypatch.setattr(prepare, "save_needs_invoice", save)
    monkeypatch.setattr(prepare, "generate_cat_mapping_prompt", prompt)

    assert prepare.main(_arguments(tmp_path)) == 0
    assert events == ["query", "extract", "save", "prompt"]
    assert capsys.readouterr().out == "mapping prompt\n"


def test_prepare_rejects_malformed_date_before_running(tmp_path, monkeypatch) -> None:
    """Reject a noncanonical date before contacting Airtable or reading inputs."""
    monkeypatch.setattr(
        prepare,
        "query_needs_invoice",
        lambda *args, **kwargs: pytest.fail("query should not run"),
    )
    arguments = _arguments(tmp_path)
    arguments[1] = "2026-9-3"

    with pytest.raises(SystemExit):
        prepare.main(arguments)


@pytest.mark.parametrize("invoice_count", [0, 2])
def test_prepare_requires_exactly_one_invoice_pdf(
    tmp_path, monkeypatch, capsys, invoice_count
) -> None:
    """Reject an input folder with a missing or ambiguous invoice PDF."""
    arguments = _arguments(tmp_path)
    inputs = tmp_path / "inputs"
    (inputs / "clinic invoice.pdf").unlink()
    for index in range(invoice_count):
        (inputs / f"invoice-{index}.pdf").touch()
    monkeypatch.setattr(
        prepare,
        "query_needs_invoice",
        lambda *args, **kwargs: pytest.fail("query should not run"),
    )

    with pytest.raises(SystemExit):
        prepare.main(arguments)

    assert f"found {invoice_count}" in capsys.readouterr().err


def test_prepare_rejects_incomplete_run_before_query(tmp_path, monkeypatch, capsys) -> None:
    """Do not mix a new prepare step with an existing unpaired artifact."""
    run = tmp_path / "outputs" / "26SEP03-NLF"
    run.mkdir(parents=True)
    (run / "extraction.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        prepare,
        "query_needs_invoice",
        lambda *args, **kwargs: pytest.fail("query should not run"),
    )

    with pytest.raises(SystemExit):
        prepare.main(_arguments(tmp_path))

    assert "incomplete run directories" in capsys.readouterr().err


def test_prepare_rejects_mismatched_output_directories(tmp_path, monkeypatch, capsys) -> None:
    """Do not generate a mapping prompt when stage outputs have different identities."""
    result = NeedsInvoiceResult("2026-09-03", "NLF", "Nine Lives Foundation", 0, 0, ())
    outputs = tmp_path / "outputs"
    monkeypatch.setattr(prepare, "AirtableClient", lambda *args, **kwargs: object())
    monkeypatch.setattr(prepare, "query_needs_invoice", lambda *args, **kwargs: result)
    monkeypatch.setattr(
        prepare,
        "extract_manifest",
        lambda *args, **kwargs: outputs / "26SEP03-NLF" / "extraction.json",
    )
    monkeypatch.setattr(
        prepare,
        "save_needs_invoice",
        lambda *args, **kwargs: outputs / "26SEP03-NLF.1" / "needs_invoice.json",
    )
    monkeypatch.setattr(
        prepare,
        "generate_cat_mapping_prompt",
        lambda *args, **kwargs: pytest.fail("prompt should not run"),
    )

    with pytest.raises(SystemExit):
        prepare.main(_arguments(tmp_path))

    assert "not written to the same run" in capsys.readouterr().err
