"""Tests for locating cat-mapping runs and generating copy-ready prompts."""

import json

import pytest

from treatment_sheet_parser.cat_mapping.errors import CatMappingPromptError
from treatment_sheet_parser.cat_mapping.prompt import generate_cat_mapping_prompt, main


def _write_run(outputs, name="26SEP02-NLF", *, date="2026-09-02", location="NLF"):
    """Write a minimal paired run with configurable identity metadata."""
    run = outputs / name
    run.mkdir(parents=True)
    extraction = {
        "run_id": name,
        "input_parameters": {"date": date, "vet": location},
        "cats": [],
    }
    needs_invoice = {"date": date, "location_code": location, "cats": []}
    (run / "extraction.json").write_text(json.dumps(extraction), encoding="utf-8")
    (run / "needs_invoice.json").write_text(json.dumps(needs_invoice), encoding="utf-8")
    return run


def test_first_pass_prompt_uses_newest_complete_paired_run(tmp_path) -> None:
    """Select the greatest complete run suffix and print all absolute paths."""
    outputs = tmp_path / "outputs"
    _write_run(outputs)
    latest = _write_run(outputs, "26SEP02-NLF.2")
    incomplete = outputs / "26SEP02-NLF.3"
    incomplete.mkdir()
    (incomplete / "extraction.json").write_text("{}", encoding="utf-8")

    prompt = generate_cat_mapping_prompt("2026-09-02", "nlf", outputs)

    assert str(latest / "extraction.json") in prompt
    assert str(latest / "needs_invoice.json") in prompt
    assert str(latest / "cat_mapping.json") in prompt
    assert str(latest / "cat_match_review.json") in prompt
    assert "/src/prompts/invariants.md" in prompt
    assert "/src/prompts/cat_matching_instructions.md" in prompt
    assert "first-pass cat mapping" in prompt
    assert "resolution instructions" not in prompt
    assert str(incomplete) not in prompt


@pytest.mark.parametrize(
    ("appointment_date", "location", "message"),
    [
        ("2026-9-2", "NLF", "YYYY-MM-DD"),
        ("2026-09-02", "unknown", "unknown location code"),
    ],
)
def test_prompt_rejects_malformed_arguments(tmp_path, appointment_date, location, message) -> None:
    """Reject noncanonical dates and unsupported location codes before lookup."""
    with pytest.raises(CatMappingPromptError, match=message):
        generate_cat_mapping_prompt(appointment_date, location, tmp_path)


@pytest.mark.parametrize(
    ("source", "date", "location", "message"),
    [
        ("extraction", "2026-09-01", "NLF", "extraction.json identity"),
        ("needs_invoice", "2026-09-02", "AMC", "needs_invoice.json identity"),
    ],
)
def test_prompt_rejects_snapshot_identity_mismatch(
    tmp_path, source, date, location, message
) -> None:
    """Do not generate a prompt from snapshots belonging to another run."""
    outputs = tmp_path / "outputs"
    run = _write_run(outputs)
    path = run / f"{source}.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    if source == "extraction":
        document["input_parameters"] = {"date": date, "vet": location}
    else:
        document.update({"date": date, "location_code": location})
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(CatMappingPromptError, match=message):
        generate_cat_mapping_prompt("2026-09-02", "NLF", outputs)


def test_prompt_rejects_malformed_snapshot(tmp_path) -> None:
    """Reject malformed JSON rather than emitting a prompt with unsafe inputs."""
    outputs = tmp_path / "outputs"
    run = _write_run(outputs)
    (run / "needs_invoice.json").write_text("[]", encoding="utf-8")

    with pytest.raises(CatMappingPromptError, match="must contain an object"):
        generate_cat_mapping_prompt("2026-09-02", "NLF", outputs)


def test_resolution_prompt_requires_and_uses_existing_artifacts(tmp_path) -> None:
    """Generate the short resolution prompt only for valid existing JSON artifacts."""
    outputs = tmp_path / "outputs"
    run = _write_run(outputs)
    (run / "cat_mapping.json").write_text("{}", encoding="utf-8")
    (run / "cat_match_review.json").write_text("{}", encoding="utf-8")

    prompt = generate_cat_mapping_prompt("2026-09-02", "NLF", outputs, resolve=True)

    assert "Resolve the cat matches" in prompt
    assert str(run / "cat_mapping.json") in prompt
    assert str(run / "cat_match_review.json") in prompt
    assert "first-pass instructions" not in prompt


def test_resolution_prompt_rejects_missing_review(tmp_path) -> None:
    """Do not emit a resolution prompt before both first-pass artifacts exist."""
    outputs = tmp_path / "outputs"
    run = _write_run(outputs)
    (run / "cat_mapping.json").write_text("{}", encoding="utf-8")

    with pytest.raises(CatMappingPromptError, match="cat_match_review.json"):
        generate_cat_mapping_prompt("2026-09-02", "NLF", outputs, resolve=True)


@pytest.mark.parametrize("flag", ["--first-pass", "--resolve"])
def test_cli_prints_selected_prompt(tmp_path, capsys, flag) -> None:
    """Expose each prompt mode as an explicit command flag."""
    outputs = tmp_path / "outputs"
    run = _write_run(outputs)
    (run / "cat_mapping.json").write_text("{}", encoding="utf-8")
    (run / "cat_match_review.json").write_text("{}", encoding="utf-8")

    status = main([flag, "2026-09-02", "NLF", "--outputs-dir", str(outputs)])

    assert status == 0
    assert str(run / "cat_mapping.json") in capsys.readouterr().out
