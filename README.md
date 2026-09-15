# Invoice parser

This repository is the programmatic tool. Keep source manifests, invoice PDFs, and treatment-sheet
PDFs in `~/Documents/Projects/bac-invoices`; keep standalone AI-processing and
volunteer-processing runbooks in `~/Documents/Projects/invoice-instructions`. Generated run artifacts go
to the sibling `../bac-outputs/` directory by default and do not belong in any of the three
repositories.

The files under `src/prompts/` are the authoritative AI instructions used by automatic Codex cat
matching. Broader operator and volunteer instructions belong in `invoice-instructions`.

## Current pipeline

Run the active five-stage pipeline with:

```bash
source .env
uv run invoice-pipeline --date 09/03 /path/to/run-inputs
```

Use `--no-review` to skip the interactive PDF review or `--outputs-dir PATH` to change the generated
output parent. The pipeline validates all sources before publication, then runs these stages:

1. Extract and review the invoice.
2. Extract and review every treatment sheet.
3. Match invoice appointments to treatment-sheet appointments and map service costs.
4. Query the read-only Airtable Needs Invoice scope and atomically publish the paired
   `extraction.json` and normalized `needs_invoice.json` snapshots.
5. Run `codex exec` in an ephemeral read-only sandbox, validate its structured identity matches,
   and publish `cat_matches.json` with `cat_match_review.json`.

Codex uses names, owners, addresses, microchips, and vouchers as strong identity evidence. Gender,
color, age, appointment context, services, and cost are supporting evidence only. Codex never writes
the artifacts itself: the Python pipeline validates source IDs, exact display names, one-to-one
assignments, and complete review coverage before writing both output files.

If Airtable validation fails, no run directory is published. If Codex fails, the paired extraction
and Airtable snapshots remain as resumable state and neither matching artifact is created. A
non-empty `cat_match_review.json` is a successful first pass that requires operator resolution.

This package reads NLF treatment-sheet PDFs and emits cat records. The full patient name is preserved
exactly as printed in `display_name`, including markers such as `(F)` or `(?)`. When a manifest is
used, its verified cat name is stored separately in `cat_name`.

Records use `YYMMMDD-VET-N` IDs derived from the required ISO date and the PDF's one-based manifest
position. For example, the fourth sheet for NLF on `2026-06-12` is `26JUN12-NLF-4`.

Appointments are keyed by ISO service date. Each appointment contains `gender`,
`microchip_number`, `color`, `weight`, `medical_findings`, `services`, and `total_cost`.
`medical_findings` preserves exact treatment-sheet text for later AI review without making mapping
or reporting decisions. Service keys are exact
Airtable `Services` or `Additional Services` values and their costs are JSON numbers. A blank
printed value is `null`; notes such as `Already chipped` do not populate a blank microchip number.
Without an invoice, `services` is an empty object and `total_cost` is `null`.

```json
{
  "cat_id": "26AUG17-NLF-1",
  "display_name": "(F) Nebula Delgado",
  "cat_name": "(F) Nebula Delgado",
  "owner_name": "N/A",
  "appointments": {
    "2026-08-06": {
      "gender": "Female",
      "microchip_number": null,
      "color": "Black",
      "weight": "7.70 lbs",
      "medical_findings": {
        "services_received": "Cat Spay\nRabies 1 year vaccine\nFVRCP vaccine - 1 year",
        "appointment_animal_notes": "",
        "exam": "Sterilization Status: Yes ...",
        "surgery_decline_reason": "",
        "high_risk_waiver_reason": "Heart Murmur: ...\n2/6",
        "owner_response": "Accepted",
        "medical_flag": "Lactating, VACCINES GIVEN: Yes, No Microchip: Yes",
        "drugs_administered": "Meloxicam ...",
        "surgical_summary": "Spay (Ventral midline incision) ...",
        "internal_notes": "Surgery/Anesthesia High Risk Waiver: Accepted",
        "notes": "",
        "tests": "",
        "rx": "",
        "client_communication": "Your pet was lactating ..."
      },
      "services": {
        "Spay / Neuter": 125.0,
        "Rabies": 0.0
      },
      "total_cost": "125.00"
    },
    "2026-08-17": {
      "gender": "Female",
      "microchip_number": null,
      "color": "Black",
      "weight": null,
      "services": {},
      "total_cost": null
    }
  }
}
```

Pages without a `Service Date` header are treated as continuation pages rather than additional
appointments.

## Legacy package organization

The retained pre-rewrite workflow is specific to Nine Lives Foundation. All of its code and
reference data live under `treatment_sheet_parser/nlf/`, including manifest validation, treatment
sheet and invoice parsing, identity matching, service mapping, output construction, and its CLI.

Only infrastructure with multiple real consumers lives under `treatment_sheet_parser/shared/`:
location codes, run-ID formatting, shared run-directory selection, and atomic JSON publication.
The read-only multi-location Airtable workflow remains under
`treatment_sheet_parser/needs_invoice/`, while cat-mapping prompt generation has its own
`treatment_sheet_parser/cat_mapping/` feature package. AMC does not use NLF manifests and should
receive a separate workflow if extraction support is added later.

## Extract a manifest

`invoice-extract` accepts exactly one `manifest.json` per invocation and processes every entry in its
`treatmentSheets` list. It verifies the manifest owner against the PDF owner field and verifies that
the manifest cat name occurs in the PDF's full display name. The wrapper writes only after every
sheet passes validation.

```bash
uv run invoice-extract \
  --date 2026-08-24 \
  --invoice "/path/to/2026-08-24 invoice.pdf" \
  /path/to/pdfs/manifest.json
```

`--invoice` is optional. When present, the parser validates numeric service sums and the overall
invoice total before writing anything. A non-numeric service price is deferred to the explicit
error-or-skip decision described below. The parser matches invoice visits to manifest cats by
service date, cat name, and owner when one is known. Appointment and invoice totals remain exact
two-decimal strings; mapped per-service costs are JSON numbers. The root `invoice` object records
`currency`, `source_file`, and `total_cost`.

`treatment_sheet_parser/nlf/service_mapping.json` is the editable invoice-to-Airtable reference. Its top-level service keys
are exact Airtable option values, and each entry lists the invoice spellings that map to it and the
owning Airtable field. `treatment_sheet_parser/nlf/airtable_service_options.json` is the temporary
local copy of every allowed `Cat.Services` and `Cat.Additional Services` schema option. Keep that
option file synchronized with the Airtable schema; extraction reads it but never contacts or changes
Airtable.

When the CLI encounters a new invoice description, it asks whether to map that spelling to an
existing Airtable option or report and ignore it for structured services. Ignored service costs
still participate in the invoice parser's service-to-cat-total and cat-total-to-invoice-total
checks. For a map, the CLI displays all schema options and accepts either the displayed number or
the exact option name. It shows the proposed mapping and updates `service_mapping.json` only after
explicit confirmation; declining aborts extraction. Confirmed mappings are saved atomically only
after the complete invoice merge succeeds. An odd, non-numeric cost for a mapped service requires a
separate error-or-skip choice. Library callers do not prompt implicitly: they must provide decision
callbacks or receive a `ServiceMappingError`. Use `--service-map JSON` or
`--service-options JSON` to work with different reference files.

An invoice may contain appointments on multiple dates, and all are parsed. A cat's treatment-sheet
appointments are keyed by service date, so the current output format supports one appointment per
cat per date. Different cats may still have appointments on the same date.

The first run writes `../bac-outputs/26AUG24-NLF/extraction.json`; later runs use
`../bac-outputs/26AUG24-NLF.1/extraction.json`, then `.2`, and so on. Each cat ID uses the run prefix and
its manifest position, for example `26AUG24-NLF-1`. Use `--outputs-dir PATH` to change the output
root.

## Local environment and checks

Keep the generated virtual environment at `invoice-parser/.venv`, then run commands from the
`invoice-parser/` repository:

```bash
uv sync
uv run pytest
uv run ruff format --check
uv run ruff check
```

Ruff's McCabe complexity rule (`C90`) is enabled with a maximum complexity of 5.

## Query Airtable for appointments needing invoices

`needs-invoice` implements the canonical read-only `Needs_Invoice` query from the adjacent invoice
workflow. It uses Airtable list-record `GET` requests only, follows every pagination offset, and
prints JSON containing the requested date and location, total matching appointment records, and
comparison-ready linked-cat records. Comparable characteristics use the same keys as extraction,
including `cat_name`, `gender`, `microchip_number`, `color`, `weight`, `services`, and `total_cost`.
Airtable cat and appointment IDs remain separate metadata. Values unavailable in Airtable, such as
treatment-sheet weight, remain `null`.

For Version 1 identity matching, each cat also includes appointment type, displayed linked
owner/trapper values, cat and appointment addresses, city, age, ear-tip status, and displayed
voucher values. Linked displays are fetched separately in Airtable string format. Owner/trapper
IDs are used only for validation. Voucher IDs are retained alongside voucher numbers so downstream
updates can target exact records.

The Airtable snapshot stores `services` as an ordered list of selected service names. Airtable has
one appointment-level `total_cost`, so it does not add meaningless per-service price placeholders.

Because each query covers exactly one date, every `cats` entry stores those comparable fields
directly rather than nesting them beneath an `appointments` date key.

All feature modules live under `treatment_sheet_parser/needs_invoice/`. The package separates its
read-only Airtable client, schema configuration, query orchestration, validation, record
normalization, output persistence, and CLI entry point.

Create an Airtable personal access token with `data.records:read` access to the Bay Area Cats base.
Store the credential and Airtable identifiers in the ignored `.env` file, then load it into the
shell before running the command:

```bash
source .env
uv run needs-invoice 2026-09-03 NLF
```

The required variables are `AIRTABLE_TOKEN`, `AIRTABLE_BASE_ID`,
`AIRTABLE_APPOINTMENTS_TABLE_ID`, and `AIRTABLE_CATS_TABLE_ID`.

After validating the complete Airtable response, the command saves JSON at
`../bac-outputs/26SEP03-NLF/needs_invoice.json` and prints that path. This is the same run directory
used by extraction. Later queries for the same date and location use `.1`, `.2`, and so on instead
of overwriting an earlier snapshot. Use `--outputs-dir PATH` to change the output parent directory.

The supported location abbreviations are `NLF` for Nine Lives Foundation and `AMC` for Animal
Medical Center. Unknown abbreviations are rejected rather than guessed.

`--base-id`, `--table-id`, and `--cats-table-id` override their corresponding environment values
when querying an equivalent schema in another base. This command does not create, update, or delete
Airtable records.

## Generate the cat-mapping prompt

After extraction and the Needs Invoice query have produced a paired run, generate the copy-ready
first-pass Codex prompt with the date and location code:

```bash
uv run generate-cat-mapping-prompt --first-pass 2026-09-02 NLF
```

The command validates the two snapshots and selects the newest numbered run directory containing
both `extraction.json` and `needs_invoice.json`. Paste its short output into Codex to create
`cat_matches.json` and `cat_match_review.json`. The AI instructions and their shared invariants live
under `src/prompts/`.

After filling the empty `resolution` fields in the review file, generate and paste the separate
resolution prompt:

```bash
uv run generate-cat-mapping-prompt --resolve 2026-09-02 NLF
```

Exactly one of `--first-pass` and `--resolve` is required. Resolution mode also validates that the
accepted-match and review artifacts already exist as JSON arrays.

Use `--outputs-dir PATH` when the paired artifacts are outside the default sibling
`../bac-outputs/` directory. The generator reads and validates files but does not create or modify any
artifact itself.

## Prepare a run

`prepare` runs the read-only Needs Invoice query, NLF extraction, and first-pass mapping-prompt
generation as one step. The Airtable response is validated before extraction output is written,
and the two JSON artifacts must land in the same run directory before a prompt is emitted.

```bash
source .env
uv run prepare --date 2026-09-03 /path/to/run-inputs
```

`prepare` accepts the same `--outputs-dir`, `--service-map`, and `--service-options` overrides as
extraction, plus the Airtable ID overrides supported by `needs-invoice`. It is NLF-specific because
the manifest extraction workflow is NLF-specific.

See [RECIPE.md](RECIPE.md) for the complete setup and run procedure.
