# Invoice pipeline recipe

This is the living runbook for the rewritten invoice pipeline. The `invoice-pipeline` command
performs preflight, Stage 1 invoice extraction, and Stage 2 treatment-sheet extraction. It validates
all source identities and runtime prerequisites, plans the future output location, starts a
persistent log, validates every printed invoice total, parses every treatment sheet, and checks its
printed cat and owner identities against the manifest. It does not yet query Airtable, match invoice
services to cats, create a run directory, or publish extraction artifacts.

## 1. Install the project

Run these commands from the `invoice-parser/` repository:

```bash
uv sync
```

## 2. Configure Airtable

Create an ignored `.env` file with the Airtable credentials and identifiers. Preflight validates
their presence and syntax but makes no network request.

```bash
AIRTABLE_TOKEN="your-token"
AIRTABLE_BASE_ID="app..."
AIRTABLE_APPOINTMENTS_TABLE_ID="tbl..."
AIRTABLE_CATS_TABLE_ID="tbl..."
```

Pass this file to `uv run` with `--env-file .env`; it loads the values for the child command without
requiring the assignments to use shell-specific `export` syntax.

## 3. Prepare read-only inputs

Place `manifest.json`, all treatment-sheet PDFs referenced by it, and exactly one invoice PDF in one
directory. The preferred invoice filename format is
`YYYY-MM-DD NUMBER Nine Lives Foundation $TOTAL.pdf`; legacy names containing `invoice` remain
supported. Treat all of these files as read-only.

```text
../bac-invoices/run-inputs/
├── manifest.json
├── 2026-09-03 5101 Nine Lives Foundation $250.00.pdf
├── cat-one-treatment-sheet.pdf
└── cat-two-treatment-sheet.pdf
```

The manifest date must match the command date. Each treatment sheet must use a bare filename from
the same input directory. Downloader metadata (`invoiceCount`, `total`, `completed`, and `failures`)
may also be present as a complete set; its counts must match the inputs and `failures` must be empty.

```json
{
  "date": "2026-09-03",
  "treatmentSheets": [
    {
      "owner": "Jane Smith",
      "catName": "Fluffy",
      "fileName": "cat-one-treatment-sheet.pdf"
    },
    {
      "owner": "N/A",
      "catName": "Mittens",
      "fileName": "cat-two-treatment-sheet.pdf"
    }
  ]
}
```

## 4. Run preflight and extraction

Pass the configured-year date in `MM/DD` form and the input directory:

```bash
uv run --env-file .env invoice-pipeline --date 09/03 ../bac-invoices/run-inputs
```

Interactive review is the default. Preflight checks that terminal input and the macOS PDF viewer
are available. After Stage 1 parses the invoice and validates its service, appointment, and invoice
totals with exact decimal arithmetic, the command opens the invoice and requires explicit approval.
Stage 2 then parses and identity-checks every treatment sheet before opening each one for approval.
For automation or noninteractive testing, disable review explicitly:

```bash
uv run --env-file .env invoice-pipeline --date 09/03 --no-review ../bac-invoices/run-inputs
```

Successful output includes the validated input counts, planned future run directory, and persistent
log path. The log is created under `../bac-outputs/logs/`; the planned run directory is not created.

Use overrides when testing outside the normal project layout:

```bash
uv run --env-file .env invoice-pipeline \
  --date 09/03 \
  --no-review \
  --outputs-dir /tmp/invoice-pipeline-outputs \
  /absolute/path/to/run-inputs
```

The command always uses the bundled service catalog and reads all Airtable settings from the
environment. It does not accept secrets or schema IDs as command-line arguments, preventing the
token from remaining in shell history or process listings. Persistent logging also redacts the
configured token before writing any message.

Example successful result:

```text
Invoice pipeline
Run date: 2026-09-03
Input directory: /absolute/path/to/run-inputs
Review: disabled

Preflight
[ok] Manifest validated
[ok] Invoice found: clinic-invoice.pdf
[ok] 2 treatment sheet(s) validated
[ok] 67 service catalog entries validated
[ok] Airtable configuration validated (no network request made)
[ok] Output location ready

Planned run directory: /path/to/bac-outputs/26SEP03-NLF
Log: /path/to/bac-outputs/logs/TIMESTAMP-26SEP03-NLF.log

Preflight complete.

Stage 1: Invoice extraction
[ok] 2 appointment(s) parsed
[ok] 5 service line(s) parsed
[ok] Invoice total validated: USD 250.00
[ok] Invoice review approved

Stage 1 complete.

Stage 2: Treatment-sheet extraction
[ok] 2 treatment sheet(s) parsed and identity-checked
[ok] 2 appointment(s) parsed
[ok] Treatment-sheet review approved

Stage 2 complete.
No run directory was created; later pipeline stages are not implemented yet.
```

Stage 1 preserves invoice appointment identity and service descriptions for later matching. It
rejects unreadable PDFs, malformed appointment rows, non-numeric service prices, missing or
inconsistent totals, service sums that differ from their appointment total, and appointment totals
that differ from the invoice total. The service catalog is not applied until a later stage.

Stage 2 preserves the complete printed cat display name, dated characteristics, and exact medical
field text. Pages without a service-date header continue the preceding appointment. Before returning
any records, the stage requires every owner to match its manifest entry after case and punctuation
normalization, and every manifest cat name to occur as a contiguous whole-token phrase in the printed
display name. A malformed sheet or identity mismatch stops the batch without creating a run
directory.

## 5. Verify repository changes

Run the project checks after changing code or a service mapping:

```bash
uv run pytest
uv run ruff format --check
uv run ruff check
git status --short
```
