"""Run the rewritten invoice pipeline through its canonical command."""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from errors import PipelineError
from pipeline_config import PipelineConfig, build_pipeline_config
from preflight import PreflightResult, run_preflight
from resolve_run_identity import resolve_run_date


def main(argv: Sequence[str] | None = None) -> int:
    """Run every implemented pipeline stage and return a process status."""
    arguments = _argument_parser().parse_args(argv)
    try:
        config = _build_config(arguments)
        _print_start(config)
        result = run_preflight(config)
    except PipelineError as exc:
        print(f"invoice-pipeline: error: {exc}", file=sys.stderr)
        return 1
    _print_preflight_result(result)
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
    """Print validated input counts and the safe stopping point of this build."""
    print("[ok] Manifest validated")
    print(f"[ok] Invoice found: {result.input_files.invoice_path.name}")
    print(f"[ok] {len(result.manifest.entries)} treatment sheet(s) validated")
    print(f"[ok] {len(result.service_catalog.services)} service catalog entries validated")
    print("[ok] Airtable configuration validated (no network request made)")
    print("[ok] Output location ready")
    print(f"\nPlanned run directory: {result.output_plan.run_directory}")
    print(f"Log: {result.output_plan.log_path}")
    print("\nPreflight complete.")
    print("No extraction stages are implemented yet; no run directory was created.")


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
