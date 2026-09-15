"""Tests for read-only Codex cat matching and output validation."""

import json
import subprocess
from pathlib import Path

import pytest
from cat_matching import run_codex_cat_matching
from errors import PipelineError

SCHEMA_PATH = Path(__file__).parents[1] / "src_v2" / "cat_matching_output.schema.json"


def _write_inputs(run_directory: Path) -> None:
    """Write one extraction cat and one Airtable cat as authoritative inputs."""
    run_directory.mkdir()
    extraction = {
        "run_id": run_directory.name,
        "input_parameters": {"date": "2026-09-03", "vet": "NLF"},
        "cats": [{"cat_id": "26SEP03-NLF-1", "display_name": "(F) Luna Owner"}],
    }
    airtable = {
        "date": "2026-09-03",
        "location_code": "NLF",
        "cats": [{"airtable_cat_id": "recCat1", "cat_name": "Luna"}],
    }
    (run_directory / "extraction.json").write_text(json.dumps(extraction), encoding="utf-8")
    (run_directory / "needs_invoice.json").write_text(json.dumps(airtable), encoding="utf-8")


def _output() -> dict[str, object]:
    """Return one complete direct-match Codex response."""
    return {
        "matches": [
            {
                "paperwork_cat_id": "26SEP03-NLF-1",
                "paperwork_display_name": "(F) Luna Owner",
                "airtable_cat_id": "recCat1",
                "airtable_display_name": "Luna",
                "match_reason": "Direct obvious name variation with consistent owner.",
            }
        ],
        "review_items": [],
    }


def test_runs_read_only_codex_and_publishes_validated_pair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pass exact artifact paths to ephemeral Codex and publish its valid result."""
    monkeypatch.setenv("AIRTABLE_TOKEN", "must-not-reach-codex")
    run_directory = tmp_path / "26SEP03-NLF"
    _write_inputs(run_directory)
    calls = []

    def execute(command, **kwargs):
        """Capture a Codex invocation and return a schema-shaped response."""
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, json.dumps(_output()), "")

    matches, review, review_count = run_codex_cat_matching(run_directory, executor=execute)

    assert calls[0][0][0:6] == ("codex", "exec", "--ephemeral", "--sandbox", "read-only", "--cd")
    assert "src/prompts/invariants.md" in calls[0][1]["input"]
    assert str(run_directory / "extraction.json") in calls[0][1]["input"]
    assert "AIRTABLE_TOKEN" not in calls[0][1]["env"]
    assert matches.name == "cat_matches.json"
    assert json.loads(matches.read_text(encoding="utf-8")) == _output()["matches"]
    assert json.loads(review.read_text(encoding="utf-8")) == []
    assert review_count == 0


def test_preserves_snapshots_when_codex_fails(tmp_path: Path) -> None:
    """Keep authoritative inputs and publish no match artifacts after a failed call."""
    run_directory = tmp_path / "26SEP03-NLF"
    _write_inputs(run_directory)

    def fail(command, **_kwargs):
        """Return a failed Codex process without exposing its stderr."""
        stderr = '"code": "invalid_json_schema", "private": "sensitive output"'
        return subprocess.CompletedProcess(command, 1, "", stderr)

    with pytest.raises(PipelineError, match="invalid output schema"):
        run_codex_cat_matching(run_directory, executor=fail)

    assert (run_directory / "extraction.json").is_file()
    assert (run_directory / "needs_invoice.json").is_file()
    assert not (run_directory / "cat_matches.json").exists()
    assert not (run_directory / "cat_match_review.json").exists()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"airtable_cat_id": "recInvented"}, "unknown or duplicate Airtable cat"),
        ({"airtable_display_name": "Wrong Name"}, "incorrect Airtable display name"),
    ],
)
def test_rejects_identity_mismatches(
    tmp_path: Path, change: dict[str, object], message: str
) -> None:
    """Reject Codex matches that invent or mislabel an Airtable identity."""
    run_directory = tmp_path / "26SEP03-NLF"
    _write_inputs(run_directory)
    output = _output()
    output["matches"][0].update(change)  # type: ignore[index,union-attr]

    def execute(command, **_kwargs):
        """Return the deliberately inconsistent structured response."""
        return subprocess.CompletedProcess(command, 0, json.dumps(output), "")

    with pytest.raises(PipelineError, match=message):
        run_codex_cat_matching(run_directory, executor=execute)

    assert not (run_directory / "cat_matches.json").exists()


@pytest.mark.parametrize(
    ("review_item", "message"),
    [
        (
            {
                "review_kind": "unresolved_paperwork",
                "paperwork_cat_id": "26SEP03-NLF-1",
                "paperwork_display_name": "(F) Luna Owner",
                "airtable_cat_id": None,
                "airtable_display_name": None,
                "match_reason": "Incorrectly marked unresolved.",
                "resolution": "",
            },
            "accepted paperwork match cannot be unresolved",
        ),
        (
            {
                "review_kind": "unassigned_airtable",
                "paperwork_cat_id": None,
                "paperwork_display_name": None,
                "airtable_cat_id": "recCat1",
                "airtable_display_name": "Luna",
                "match_reason": "Incorrectly marked unassigned.",
                "resolution": "",
            },
            "accepted Airtable match cannot be unassigned",
        ),
    ],
)
def test_rejects_review_items_that_conflict_with_accepted_matches(
    tmp_path: Path, review_item: dict[str, object], message: str
) -> None:
    """Reject review classifications that contradict an accepted match."""
    run_directory = tmp_path / "26SEP03-NLF"
    _write_inputs(run_directory)
    output = _output()
    output["review_items"] = [review_item]

    def execute(command, **_kwargs):
        """Return an accepted match with a contradictory review classification."""
        return subprocess.CompletedProcess(command, 0, json.dumps(output), "")

    with pytest.raises(PipelineError, match=message):
        run_codex_cat_matching(run_directory, executor=execute)


def test_rejects_malformed_codex_json(tmp_path: Path) -> None:
    """Reject non-JSON output without creating either matching artifact."""
    run_directory = tmp_path / "26SEP03-NLF"
    _write_inputs(run_directory)

    def execute(command, **_kwargs):
        """Return text that cannot satisfy the response contract."""
        return subprocess.CompletedProcess(command, 0, "not json", "")

    with pytest.raises(PipelineError, match="did not return valid JSON"):
        run_codex_cat_matching(run_directory, executor=execute)


def test_rejects_legacy_object_shaped_match_output(tmp_path: Path) -> None:
    """Reject the former arbitrary-key object contract in favor of fixed arrays."""
    run_directory = tmp_path / "26SEP03-NLF"
    _write_inputs(run_directory)
    output = {"matches": {}, "review_items": {}}

    def execute(command, **_kwargs):
        """Return JSON with the obsolete object-shaped collections."""
        return subprocess.CompletedProcess(command, 0, json.dumps(output), "")

    with pytest.raises(PipelineError, match="matches must be a JSON array"):
        run_codex_cat_matching(run_directory, executor=execute)


def test_publishes_explicit_unresolved_and_unassigned_review_items(tmp_path: Path) -> None:
    """Publish complete typed review coverage when neither source cat is matched."""
    run_directory = tmp_path / "26SEP03-NLF"
    _write_inputs(run_directory)
    output = {
        "matches": [],
        "review_items": [
            {
                "review_kind": "unresolved_paperwork",
                "paperwork_cat_id": "26SEP03-NLF-1",
                "paperwork_display_name": "(F) Luna Owner",
                "airtable_cat_id": None,
                "airtable_display_name": None,
                "match_reason": "No strong evidence establishes identity.",
                "resolution": "",
            },
            {
                "review_kind": "unassigned_airtable",
                "paperwork_cat_id": None,
                "paperwork_display_name": None,
                "airtable_cat_id": "recCat1",
                "airtable_display_name": "Luna",
                "match_reason": "No paperwork cat was confidently assigned.",
                "resolution": "",
            },
        ],
    }

    def execute(command, **_kwargs):
        """Return complete review-only output for both source sides."""
        return subprocess.CompletedProcess(command, 0, json.dumps(output), "")

    matches, review, review_count = run_codex_cat_matching(run_directory, executor=execute)

    assert json.loads(matches.read_text(encoding="utf-8")) == []
    assert json.loads(review.read_text(encoding="utf-8")) == output["review_items"]
    assert review_count == 2


def test_schema_uses_fixed_arrays_and_typed_resolution() -> None:
    """Keep the Codex transport schema within strict Structured Outputs rules."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["required"] == ["matches", "review_items"]
    assert schema["properties"]["matches"]["type"] == "array"
    assert schema["properties"]["review_items"]["type"] == "array"
    assert schema["$defs"]["review_item"]["properties"]["resolution"] == {
        "type": "string",
        "const": "",
    }


def test_rejects_mismatched_snapshot_identity_before_codex(tmp_path: Path) -> None:
    """Do not invoke Codex when the paired snapshots identify different runs."""
    run_directory = tmp_path / "26SEP03-NLF"
    _write_inputs(run_directory)
    path = run_directory / "needs_invoice.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["date"] = "2026-09-04"
    path.write_text(json.dumps(document), encoding="utf-8")

    def execute(*_args, **_kwargs):
        """Fail the test if invalid authoritative inputs reach Codex."""
        pytest.fail("Codex must not run for mismatched snapshot identities")

    with pytest.raises(PipelineError, match="snapshot identities do not match"):
        run_codex_cat_matching(run_directory, executor=execute)
