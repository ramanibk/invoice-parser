"""Run the rewritten invoice pipeline through its canonical command."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from airtable_snapshot import query_airtable_snapshot
from cat_matching import run_codex_cat_matching
from errors import PipelineError
from invoice_extraction import parse_invoice
from invoice_treatment_mapping import match_invoice_to_treatment_sheets
from models_airtable import AirtableSnapshot
from models_extraction import ExtractionCat
from models_invoice import Invoice
from models_treatment_sheet import TreatmentCat
from pipeline_config import PipelineConfig, build_pipeline_config
from pipeline_logging import append_pipeline_log
from preflight import PreflightResult, run_preflight
from resolve_run_identity import resolve_run_date
from review import review_extraction
from review_output import print_invoice_extraction, print_treatment_sheet_extraction
from run_artifacts import publish_run_snapshots
from treatment_sheet_extraction import extract_treatment_sheets


def main(argv: Sequence[str] | None = None) -> int:
    """Run every implemented pipeline stage and return a process status."""
    arguments = _argument_parser().parse_args(argv)
    try:
        config = _build_config(arguments)
        _print_start(config)
        preflight = run_preflight(config)
        _print_preflight_result(preflight)
        # Each stage returns fully validated immutable state for the next stage;
        # publication occurs only after all source reconciliation succeeds.
        invoice = _run_invoice_extraction(preflight)
        _print_invoice_result(invoice, config.review_enabled)
        treatment_cats = _run_treatment_sheet_extraction(preflight)
        _print_treatment_sheet_result(treatment_cats, config.review_enabled)
        extraction_cats = _run_invoice_treatment_mapping(preflight, invoice, treatment_cats)
        _print_mapping_result(extraction_cats)
        airtable_snapshot = _run_airtable_retrieval(preflight)
        # The run becomes resumable only when both authoritative inputs are
        # complete, so publication waits for the Airtable query and normalization.
        extraction_path, needs_invoice_path = _publish_snapshots(
            preflight, extraction_cats, invoice, airtable_snapshot
        )
        _print_airtable_result(
            airtable_snapshot.linked_cat_count, extraction_path, needs_invoice_path
        )
        matches_path, review_path, review_count = _run_cat_matching(preflight)
        _print_cat_matching_result(matches_path, review_path, review_count)
    except PipelineError as exc:
        print(f"invoice-pipeline: error: {exc}", file=sys.stderr)
        return 1
    return 0


def _build_config(arguments: argparse.Namespace) -> PipelineConfig:
    """Resolve CLI values into the validated configuration model."""
    run_date = resolve_run_date(arguments.date)
    return build_pipeline_config(
        run_date,
        arguments.input_dir,
        output_dir=arguments.outputs_dir,
        review_enabled=not arguments.no_review,
    )


def _print_start(config: PipelineConfig) -> None:
    """Print the resolved run identity before read-only preflight begins."""
    review_mode = "enabled" if config.review_enabled else "disabled"
    print("Invoice pipeline")
    print(f"Run date: {config.run_date.isoformat()}")
    print(f"Input directory: {config.inputs.input_dir}")
    print(f"Review: {review_mode}")
    print("\nPreflight", flush=True)


def _print_preflight_result(result: PreflightResult) -> None:
    """Print validated input counts and output identities."""
    print("[ok] Manifest validated")
    print(f"[ok] Invoice found: {result.input_files.invoice_path.name}")
    print(f"[ok] {len(result.manifest.entries)} treatment sheet(s) validated")
    print(f"[ok] {len(result.service_catalog.services)} service catalog entries validated")
    print("[ok] Airtable configuration validated (no network request made)")
    print("[ok] Codex command available")
    print("[ok] Output location ready")
    print(f"\nPlanned run directory: {result.output_plan.run_directory}")
    print(f"Log: {result.output_plan.log_path}")
    print("\nPreflight complete.")


def _run_invoice_extraction(result: PreflightResult) -> Invoice:
    """Parse, validate, review, and log the first numbered pipeline stage."""
    invoice = parse_invoice(result.input_files.invoice_path)
    # Show extracted values before opening the source PDF so the operator has a
    # stable comparison view when the external viewer takes focus.
    if result.config.review_enabled:
        print_invoice_extraction(invoice)
    review_extraction(
        invoice.source_file,
        "invoice",
        review_enabled=result.config.review_enabled,
    )
    service_count = sum(len(appointment.services) for appointment in invoice.appointments)
    append_pipeline_log(
        result.output_plan,
        (
            "Stage 1 invoice extraction passed.",
            f"Validated {len(invoice.appointments)} invoice appointment(s).",
            f"Validated {service_count} invoice service line(s).",
            f"Validated invoice total: USD {invoice.total_cost:.2f}.",
        ),
        sensitive_values=(result.config.airtable.token,),
    )
    return invoice


def _print_invoice_result(invoice: Invoice, review_enabled: bool) -> None:
    """Print the validated invoice summary."""
    service_count = sum(len(appointment.services) for appointment in invoice.appointments)
    review_status = "approved" if review_enabled else "skipped"
    print("\nStage 1: Invoice extraction")
    print(f"[ok] {len(invoice.appointments)} appointment(s) parsed")
    print(f"[ok] {service_count} service line(s) parsed")
    print(f"[ok] Invoice total validated: USD {invoice.total_cost:.2f}")
    print(f"[ok] Invoice extraction review {review_status}")
    print("\nStage 1 complete.")


def _run_treatment_sheet_extraction(preflight: PreflightResult) -> tuple[TreatmentCat, ...]:
    """Parse, validate, review, and log every Stage 2 treatment sheet."""
    treatment_cats = extract_treatment_sheets(preflight.manifest)
    # Strict positional pairing preserves the manifest-to-source relationship
    # already validated during preflight.
    for treatment_cat, path in zip(
        treatment_cats,
        preflight.manifest.treatment_sheet_paths,
        strict=True,
    ):
        if preflight.config.review_enabled:
            print_treatment_sheet_extraction(treatment_cat, path)
        review_extraction(
            path,
            f"treatment sheet {path.name}",
            review_enabled=preflight.config.review_enabled,
        )
    appointment_count = sum(len(cat.appointments) for cat in treatment_cats)
    append_pipeline_log(
        preflight.output_plan,
        (
            "Stage 2 treatment-sheet extraction passed.",
            f"Validated {len(treatment_cats)} treatment sheet(s).",
            f"Validated {appointment_count} treatment-sheet appointment(s).",
        ),
        sensitive_values=(preflight.config.airtable.token,),
    )
    return treatment_cats


def _print_treatment_sheet_result(
    treatment_cats: tuple[TreatmentCat, ...], review_enabled: bool
) -> None:
    """Print the validated treatment-sheet summary."""
    appointment_count = sum(len(cat.appointments) for cat in treatment_cats)
    review_status = "approved" if review_enabled else "skipped"
    print("\nStage 2: Treatment-sheet extraction")
    print(f"[ok] {len(treatment_cats)} treatment sheet(s) parsed and identity-checked")
    print(f"[ok] {appointment_count} appointment(s) parsed")
    print(f"[ok] Treatment-sheet extraction review {review_status}")
    print("\nStage 2 complete.")


def _run_invoice_treatment_mapping(
    preflight: PreflightResult,
    invoice: Invoice,
    treatment_cats: tuple[TreatmentCat, ...],
) -> tuple[ExtractionCat, ...]:
    """Match each latest visit, retain history, and log the completed stage."""
    extraction_cats = match_invoice_to_treatment_sheets(
        invoice,
        treatment_cats,
        preflight.service_catalog,
    )
    appointment_count = sum(
        appointment.total_cost is not None
        for cat in extraction_cats
        for appointment in cat.appointments
    )
    append_pipeline_log(
        preflight.output_plan,
        (
            "Stage 3 invoice-to-treatment-sheet mapping passed.",
            f"Matched {appointment_count} latest appointment(s) one-to-one.",
        ),
        sensitive_values=(preflight.config.airtable.token,),
    )
    return extraction_cats


def _print_mapping_result(
    extraction_cats: tuple[ExtractionCat, ...],
) -> None:
    """Print the invoice-to-treatment mapping summary."""
    appointment_count = sum(
        appointment.total_cost is not None
        for cat in extraction_cats
        for appointment in cat.appointments
    )
    print("\nStage 3: Invoice-to-treatment-sheet mapping")
    print(f"[ok] {appointment_count} latest appointment(s) matched one-to-one")
    print("[ok] Invoice services mapped to Airtable service names")
    print("\nStage 3 complete.")


def _run_airtable_retrieval(preflight: PreflightResult) -> AirtableSnapshot:
    """Query, normalize, and log the complete read-only Airtable scope."""
    snapshot = query_airtable_snapshot(preflight.config.airtable, preflight.config.run_date)
    append_pipeline_log(
        preflight.output_plan,
        (
            "Stage 4 Airtable retrieval passed.",
            f"Validated {snapshot.appointment_record_count} appointment record(s).",
            f"Validated {snapshot.linked_cat_count} linked cat record(s).",
        ),
        sensitive_values=(preflight.config.airtable.token,),
    )
    return snapshot


def _publish_snapshots(
    preflight: PreflightResult,
    extraction_cats: tuple[ExtractionCat, ...],
    invoice: Invoice,
    airtable_snapshot: AirtableSnapshot,
) -> tuple[Path, Path]:
    """Publish paired extraction and Airtable snapshots after full validation."""
    paths = publish_run_snapshots(
        preflight.output_plan,
        preflight.input_files.manifest_path,
        preflight.config.run_date,
        extraction_cats,
        invoice,
        airtable_snapshot,
    )
    append_pipeline_log(
        preflight.output_plan,
        ("Published extraction and Airtable snapshot artifacts.",),
        sensitive_values=(preflight.config.airtable.token,),
    )
    return paths


def _print_airtable_result(cat_count: int, extraction_path: Path, needs_invoice_path: Path) -> None:
    """Print Airtable retrieval counts and both authoritative snapshot paths."""
    print("\nStage 4: Airtable retrieval")
    print(f"[ok] {cat_count} linked cat record(s) validated")
    print(f"[ok] Extraction artifact published: {extraction_path}")
    print(f"[ok] Airtable snapshot published: {needs_invoice_path}")
    print("\nStage 4 complete.")


def _run_cat_matching(preflight: PreflightResult) -> tuple[Path, Path, int]:
    """Invoke Codex, publish its validated artifacts, and log safe counts."""
    result = run_codex_cat_matching(preflight.output_plan.run_directory)
    append_pipeline_log(
        preflight.output_plan,
        ("Stage 5 Codex cat matching passed.", f"Review entries: {result[2]}."),
        sensitive_values=(preflight.config.airtable.token,),
    )
    return result


def _print_cat_matching_result(matches_path: Path, review_path: Path, review_count: int) -> None:
    """Print matching artifact paths and the operator-review count."""
    print("\nStage 5: Codex cat matching")
    print(f"[ok] Cat matches published: {matches_path}")
    print(f"[ok] Cat match review published: {review_path}")
    print(f"[ok] {review_count} review entr{'y' if review_count == 1 else 'ies'}")
    print("\nStage 5 complete.")


def _argument_parser() -> argparse.ArgumentParser:
    """Build the stable command-line interface for one pipeline run."""
    parser = argparse.ArgumentParser(
        prog="invoice-pipeline",
        description="Validate and run the invoice pipeline without modifying source inputs.",
    )
    parser.add_argument("--date", required=True, help="Run date in MM/DD format.")
    parser.add_argument("--outputs-dir", type=Path, help="Generated output parent directory.")
    parser.add_argument(
        "--no-review",
        action="store_true",
        help="Skip extracted-value printing, PDF viewing, and interactive approval prompts.",
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing manifest.json, one invoice PDF, and treatment-sheet PDFs.",
    )
    return parser
