"""Run the rewritten invoice pipeline through its canonical command."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from errors import PipelineError
from invoice_extraction import parse_invoice
from models_invoice import Invoice
from models_treatment_sheet import CatRecord
from pipeline_config import PipelineConfig, build_pipeline_config
from pipeline_logging import append_pipeline_log
from preflight import PreflightResult, run_preflight
from resolve_run_identity import resolve_run_date
from review import review_pdf
from treatment_sheet_extraction import extract_treatment_sheets


def main(argv: Sequence[str] | None = None) -> int:
    """Run every implemented pipeline stage and return a process status."""
    arguments = _argument_parser().parse_args(argv)
    try:
        config = _build_config(arguments)
        _print_start(config)
        result = run_preflight(config)
        _print_preflight_result(result)
        invoice = _run_invoice_extraction(result)
        _print_invoice_result(invoice, config.review_enabled)
        records = _run_treatment_sheet_extraction(result)
        _print_treatment_sheet_result(records, config.review_enabled)
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
    print("[ok] Output location ready")
    print(f"\nPlanned run directory: {result.output_plan.run_directory}")
    print(f"Log: {result.output_plan.log_path}")
    print("\nPreflight complete.")


def _run_invoice_extraction(result: PreflightResult) -> Invoice:
    """Parse, validate, review, and log the first numbered pipeline stage."""
    invoice = parse_invoice(result.input_files.invoice_path)
    review_pdf(
        invoice.source_file,
        "invoice PDF",
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
    print(f"[ok] Invoice review {review_status}")
    print("\nStage 1 complete.")


def _run_treatment_sheet_extraction(result: PreflightResult) -> tuple[CatRecord, ...]:
    """Parse, validate, review, and log every Stage 2 treatment sheet."""
    records = extract_treatment_sheets(result.manifest)
    for path in result.manifest.treatment_sheet_paths:
        review_pdf(
            path,
            f"treatment sheet {path.name}",
            review_enabled=result.config.review_enabled,
        )
    appointment_count = sum(len(record.appointments) for record in records)
    append_pipeline_log(
        result.output_plan,
        (
            "Stage 2 treatment-sheet extraction passed.",
            f"Validated {len(records)} treatment sheet(s).",
            f"Validated {appointment_count} treatment-sheet appointment(s).",
        ),
        sensitive_values=(result.config.airtable.token,),
    )
    return records


def _print_treatment_sheet_result(records: tuple[CatRecord, ...], review_enabled: bool) -> None:
    """Print the validated treatment-sheet summary and current safe stopping point."""
    appointment_count = sum(len(record.appointments) for record in records)
    review_status = "approved" if review_enabled else "skipped"
    print("\nStage 2: Treatment-sheet extraction")
    print(f"[ok] {len(records)} treatment sheet(s) parsed and identity-checked")
    print(f"[ok] {appointment_count} appointment(s) parsed")
    print(f"[ok] Treatment-sheet review {review_status}")
    print("\nStage 2 complete.")
    print("No run directory was created; later pipeline stages are not implemented yet.")


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
        help="Skip interactive review readiness and future PDF review prompts.",
    )
    parser.add_argument(
        "input_dir",
        type=Path,
        help="Directory containing manifest.json, one invoice PDF, and treatment-sheet PDFs.",
    )
    return parser
