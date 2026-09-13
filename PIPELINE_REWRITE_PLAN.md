# Flat, Clean-Slate Invoice Pipeline Rewrite

## Summary

Rebuild the invoice workflow from scratch in parallel `src_v2/` and `tests_v2/` directories. All
Python modules, prompts, and JSON reference files will sit directly at one level—no nested packages.
Existing code may be copied and adapted, but the new stage modules will own their logic rather than
wrapping or importing the old implementation.

Work will proceed one reviewable slice at a time. After each slice, run the required checks and stop
without committing.

## Implementation Sequence

1. **Clean skeleton**
   - Delete the current implementation, old tests, and the uncommitted wrapper-style Step 1.
   - Create flat `src_v2/` and `tests_v2/` roots with minimal packaging and smoke-test configuration.
   - Remove all existing console commands.
   - Accept that the repository temporarily has no working invoice command.

2. **Shared foundations**
   - Add flat modules for models, errors, run identity, configuration, and in-memory pipeline state.
   - Move and simplify only reusable definitions needed by multiple stages.
   - Add unit tests for validation and serialization contracts.

3. **Preflight and logging**
   - Validate the date, input directory, manifest, invoice presence, Airtable settings, output state,
     reference files, and interactive-terminal availability.
   - Start the timestamped persistent log before numbered processing begins.
   - Do not create a run directory or extraction artifacts during preflight.

4. **Review support**
   - Add the system PDF-opening and terminal-confirmation behavior.
   - Default to interactive review; `--no-review` skips opening and confirmation.
   - Viewer failure, rejection, EOF, or noninteractive stdin aborts cleanly.

5. **Step 1: treatment sheets**
   - Move manifest validation, treatment-sheet parsing, identity matching, and review behavior directly
     into `step_01_extract_treatment_sheets.py`.
   - Process every manifest entry once and in order.
   - Keep all validated records in memory and write nothing.

6. **Step 2: invoice**
   - Move invoice parsing, total validation, terminal summary, and review directly into
     `step_02_extract_invoice.py`.
   - Preserve exact appointment identities, services, subtotals, currency, and total.

7. **Step 3: invoice matching**
   - Move identity matching and service normalization into `step_03_match_invoice.py`.
   - Keep approved service-reference changes in memory.
   - Reject unmatched appointments, identity conflicts, ambiguous matches, malformed costs, and
     inconsistent totals.

8. **Step 4: Airtable**
   - Move Airtable fetching, pagination, response validation, and record normalization into
     `step_04_query_airtable.py`.
   - Keep the complete validated result in memory and perform no remote writes.

9. **Step 5: publication**
   - Build `extraction.json` and `needs_invoice.json` only after Steps 1–4 succeed.
   - Write both into one hidden staging directory and publish them with one directory rename.
   - Apply staged service-reference changes only during successful publication, restoring the
     original reference if final publication fails.
   - Never leave a partial final run directory.

10. **Step 6 and CLI**
    - Move prompt validation and generation into `step_06_generate_mapping_prompt.py`.
    - Target the exact directory published by Step 5.
    - Add the sole command:
      `invoice-pipeline --date YYYY-MM-DD [overrides] [--no-review] INPUT_DIR`.
    - Send progress and review details to stderr; keep only the mapping prompt on stdout.

11. **Documentation and final promotion**
    - Update the README and recipe for the single pipeline workflow.
    - Rename `src_v2/` to `src/` and `tests_v2/` to `tests/`.
    - Configure packaging to include the flat Markdown prompts and JSON references.
    - Remove all temporary v2 naming and verify the installed command.

## Interfaces and Structure

- The six numbered stage files contain their stage-specific parsing, validation, and transformation
  helpers; they do not forward to legacy modules.
- Only genuinely cross-stage behavior lives in flat support modules such as models, errors,
  preflight, review, logging, and publication.
- There are no compatibility shims for old Python imports or CLI commands.
- Low-level functions remain testable but their new module paths are a clean break.
- Every AI prompt reads and explicitly requires compliance with the flat `invariants.md` file.

## Test Plan

- Each slice adds focused tests for successful behavior, malformed input, and identity mismatches
  where applicable.
- Verify stage order and exactly-once execution.
- Verify interactive review, rejection, viewer failure, non-TTY behavior, and `--no-review`.
- Verify log contents exclude detailed medical findings while terminal output includes them.
- Verify failures never publish partial artifacts or premature service-map updates.
- Verify stdout contains only the generated prompt and stderr contains progress.
- After every slice run `uv run pytest`, `uv run ruff format --check`, `uv run ruff check`, and
  `git diff --check`.

## Assumptions

- Deleting the current implementation in the first slice is intentional; Git history remains the
  reference for selectively recovering useful logic.
- The temporary rewrite roots are `src_v2/` and `tests_v2/`; the final repository returns to `src/`
  and `tests/`.
- “Flat” applies to Python, Markdown prompts, and JSON reference data.
- The operating system controls viewer window placement.
- No slice is committed automatically; implementation pauses after checks for review and
  user-managed Git operations.
