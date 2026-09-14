"""Tests for explicit system-PDF review behavior."""

import builtins
from pathlib import Path
from subprocess import CompletedProcess

import pytest
import review
from errors import PipelineError
from review import review_extraction


def _pdf(tmp_path: Path) -> Path:
    """Create one readable placeholder PDF for review tests."""
    path = tmp_path / "source.pdf"
    path.write_bytes(b"%PDF-placeholder")
    return path


def test_opens_pdf_and_accepts_explicit_approval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Launch the source in the system viewer before collecting approval."""
    pdf_path = _pdf(tmp_path)
    commands = []

    def fake_run(command: tuple[str, str], *, check: bool) -> CompletedProcess[str]:
        """Record the viewer command and report a successful launch."""
        commands.append((command, check))
        return CompletedProcess(command, 0)

    monkeypatch.setattr(review.subprocess, "run", fake_run)
    prompts = []

    def approve(prompt: str) -> str:
        """Record the extraction wording and approve the review."""
        prompts.append(prompt)
        return "yes"

    monkeypatch.setattr(builtins, "input", approve)

    assert review_extraction(pdf_path, "invoice") is None
    assert commands == [(("open", str(pdf_path)), False)]
    assert prompts == ["Approve invoice extraction? [y/N] "]


def test_no_review_skips_viewer_and_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Perform no interactive side effects after review is explicitly disabled."""
    monkeypatch.setattr(
        review.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("viewer must not run"),
    )
    monkeypatch.setattr(
        builtins,
        "input",
        lambda _prompt: pytest.fail("confirmation must not run"),
    )

    assert review_extraction(tmp_path / "absent.pdf", "invoice", review_enabled=False) is None


def test_rejects_viewer_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abort when the operating-system viewer reports a failed launch."""
    pdf_path = _pdf(tmp_path)
    monkeypatch.setattr(
        review.subprocess,
        "run",
        lambda command, *, check: CompletedProcess(command, 3),
    )

    with pytest.raises(PipelineError, match="viewer exited with status 3"):
        review_extraction(pdf_path, "invoice")


def test_rejects_operator_decline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Abort without interpreting a negative or empty response as approval."""
    pdf_path = _pdf(tmp_path)
    monkeypatch.setattr(
        review.subprocess,
        "run",
        lambda command, *, check: CompletedProcess(command, 0),
    )
    monkeypatch.setattr(builtins, "input", lambda _prompt: "no")

    with pytest.raises(PipelineError, match="invoice extraction was not approved"):
        review_extraction(pdf_path, "invoice")


def test_rejects_confirmation_eof(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Translate a closed input stream into a controlled review failure."""
    pdf_path = _pdf(tmp_path)
    monkeypatch.setattr(
        review.subprocess,
        "run",
        lambda command, *, check: CompletedProcess(command, 0),
    )

    def raise_eof(_prompt: str) -> str:
        """Simulate a terminal input stream closing during confirmation."""
        raise EOFError

    monkeypatch.setattr(builtins, "input", raise_eof)

    with pytest.raises(PipelineError, match="could not confirm invoice extraction review"):
        review_extraction(pdf_path, "invoice")


def test_rejects_malformed_review_source(tmp_path: Path) -> None:
    """Reject a missing or non-PDF path before attempting viewer launch."""
    with pytest.raises(PipelineError, match="existing PDF"):
        review_extraction(tmp_path / "source.txt", "invoice")
