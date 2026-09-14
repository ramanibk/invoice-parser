# Invoice pipeline recipe

This is the living runbook for the rewritten invoice pipeline. The `invoice-pipeline` command
currently performs preflight only: it validates all source identities and runtime prerequisites,
plans the future output location, and starts a persistent log. It does not yet parse PDF contents,
query Airtable, create a run directory, or publish extraction artifacts.

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

## 4. Run preflight

Pass the configured-year date in `MM/DD` form and the input directory:

```bash
uv run --env-file .env invoice-pipeline --date 09/03 ../bac-invoices/run-inputs
```

Interactive review is the default. At this stage it checks that terminal input and the macOS PDF
viewer are available; the numbered extraction stages will use that support to open PDFs later. For
automation or preflight-only testing, disable review readiness explicitly:

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
No extraction stages are implemented yet; no run directory was created.
```

## 5. Verify repository changes

Run the project checks after changing code or a service mapping:

```bash
uv run pytest
uv run ruff format --check
uv run ruff check
git status --short
```
