# NLF preparation recipe

This recipe runs the complete read-only Airtable query, treatment-sheet and invoice extraction,
and cat-mapping preparation workflow.

## 1. Install the project

Run these commands from the `invoice-parser/` repository:

```bash
cd /path/to/Documents/Projects/invoice-parser
git pull
uv sync
```

## 2. Configure Airtable

Create an ignored `.env` file with the Airtable credentials and identifiers. The personal access
token needs `data.records:read` access to the configured base.

```bash
AIRTABLE_TOKEN="your-token"
AIRTABLE_BASE_ID="app..."
AIRTABLE_APPOINTMENTS_TABLE_ID="tbl..."
AIRTABLE_CATS_TABLE_ID="tbl..."
```

Load these values into the current shell:

```bash
source .env
```

## 3. Prepare the input directory

Create the run input directory under the sibling `bac-invoices` project. Place `manifest.json`, all
treatment-sheet PDFs referenced by the manifest, and exactly one PDF whose filename contains
`invoice` (case-insensitive) in that directory:

```text
../bac-invoices/run-inputs/
├── manifest.json
├── clinic invoice.pdf
├── cat-one-treatment-sheet.pdf
└── cat-two-treatment-sheet.pdf
```

The manifest date must match the command date. Each treatment sheet must use a bare filename from
the same input directory.

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

## 4. Run preparation

Replace the example date and input path with the values for the run:

```bash
uv run prepare --date 2026-09-03 ../bac-invoices/run-inputs
```

The command queries Airtable, validates and extracts the PDFs, and writes paired artifacts to a
new run directory such as:

```text
../bac-outputs/26SEP03-NLF/
├── extraction.json
└── needs_invoice.json
```

When extraction sees an unfamiliar invoice service, it may interactively ask for a mapping. A
confirmed mapping updates the configured service-mapping JSON file.

## 5. Run the mapping prompt

The preparation command prints a cat-mapping prompt. Paste that prompt into Codex to create these
files in the same run directory:

```text
cat_mapping.json
cat_match_review.json
```

## 6. Resolve uncertain matches

If the review contains unresolved matches, fill the empty `resolution` fields in
`cat_match_review.json`, then generate a resolution prompt:

```bash
uv run generate-cat-mapping-prompt --resolve 2026-09-03 NLF
```

Paste that prompt into Codex to apply and validate the decisions.

## 7. Verify repository changes

Run the project checks after changing code or a service mapping:

```bash
uv run pytest
uv run ruff format --check
uv run ruff check
git status --short
```
