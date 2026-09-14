# Invoice pipeline recipe

This is the living runbook for the rewritten invoice pipeline. The `invoice-pipeline` command
performs preflight, Stage 1 invoice extraction, Stage 2 treatment-sheet extraction, and Stage 3
invoice-to-treatment-sheet matching. It validates all source identities and runtime prerequisites,
starts a persistent log, validates every printed invoice total, parses every treatment sheet, checks
its printed cat and owner identities against the manifest, and matches every invoice visit by date,
cat, and owner before atomically publishing `extraction.json`. It does not yet query Airtable or
match extracted cats to Airtable records.

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
totals with exact decimal arithmetic, the command prints every extracted value, opens the invoice,
and asks the operator to approve the extraction. Stage 2 then parses and identity-checks every
treatment sheet before printing each record, opening its source PDF, and asking the operator to
approve that extraction. For automation or noninteractive testing, disable review explicitly:

```bash
uv run --env-file .env invoice-pipeline --date 09/03 --no-review ../bac-invoices/run-inputs
```

Successful output includes the validated input counts, planned run directory, persistent log path,
and published extraction artifact. The log is created under `../bac-outputs/logs/`; the run
directory is created only after every extraction and invoice match passes validation.

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

Abbreviated successful result (the detailed JSON printed before each review is omitted here):

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
[ok] Invoice extraction review approved

Stage 1 complete.

Stage 2: Treatment-sheet extraction
[ok] 2 treatment sheet(s) parsed and identity-checked
[ok] 2 appointment(s) parsed
[ok] Treatment-sheet extraction review approved

Stage 2 complete.

Stage 3: Invoice-to-treatment-sheet mapping
[ok] 2 latest appointment(s) matched one-to-one
[ok] Invoice services mapped to Airtable service names
[ok] Extraction artifact published: /path/to/bac-outputs/26SEP03-NLF/extraction.json

Stage 3 complete.
Airtable retrieval and cat matching are not implemented yet.
```

Stage 1 preserves each invoice animal display name, animal reference, owner name, combined identity
text, and service descriptions for later matching. It uses the PDF's detected Animal, Owner, and
Species column boundaries so visually wrapped names and references remain in the correct field. It
rejects unreadable PDFs, malformed appointment rows, non-numeric service prices, missing or
inconsistent totals, service sums that differ from their appointment total, and appointment totals
that differ from the invoice total.

Stage 2 preserves the complete printed cat display name, dated characteristics, and exact medical
field text. Pages without a service-date header continue the preceding appointment. Before returning
any records, the stage requires every owner to match its manifest entry after case and punctuation
normalization, and every manifest cat name to occur as a contiguous whole-token phrase in the printed
display name. A malformed sheet or identity mismatch stops the batch without creating a run
directory.

Stage 3 requires each cat's latest treatment-sheet appointment to match exactly one unused invoice
appointment by service date, contiguous whole-token cat name, and normalized owner tokens. Earlier
treatment-sheet appointments remain in the extraction as informational history with empty services
and a null total. The manifest's explicit `N/A` owner sentinel does not constrain the invoice owner.
Every invoice appointment must be consumed, and every invoice service description must exist in the
validated service catalog.
Only after the complete match succeeds does the pipeline create the run directory and atomically
write `extraction.json`; matching or publication failures never leave a partial artifact.

## 5. Verify repository changes

Run the project checks after changing code or a service mapping:

```bash
uv run pytest
uv run ruff format --check
uv run ruff check
git status --short
```
