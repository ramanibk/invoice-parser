# Shared AI Invariants

Every AI instruction prompt in this repository MUST require the AI to read and follow this file
before beginning work. These invariants apply in every stage and workflow. A later instruction may
add a stricter requirement, but it MUST NOT weaken an invariant. If instructions conflict, follow
this file, leave affected items unchanged, and report the exact conflict to the Processing
Operator.

## Non-negotiable invariants

1. **No deletion.** The AI Parser MUST NOT delete any Airtable record, attachment, comment, source
   file, or other stored artifact. Reads are allowed. Update an individual `Cats`, `Appointments`,
   or `Vouchers` record ONLY when the applicable instructions allow it. An authorized field update
   may remove a field selection or unlink a record. It MUST NOT delete the linked record. If a
   required step needs deletion, **STOP**, leave the item unchanged, and report what needs
   clarification.
2. **Frozen scope.** The exact Airtable record IDs saved in a run's `needs_invoice.json` MUST define
   the Processing Scope. A resumed run MUST use those IDs and MUST NOT replace them with a new query
   result. Expand scope ONLY through an action expressly authorized by the processing rules or
   explicitly approved by the Processing Operator, and record every addition with its origin and
   authorization. The voucher workflow is the only rule that may rerun `Needs_Invoice` to discover
   additions.
3. **Evidence, uncertainty, and protected fields.** The AI Parser MUST NOT invent values or take an
   action unsupported by the sources or an applicable processing rule. A documented inference
   expressly permitted by the processing rules is supported work, not a guess. Perform
   rule-authorized updates without requesting additional permission. Ask the Processing Operator
   only when no rule resolves a required action, required evidence is absent, or material evidence
   conflicts. Continue safe, independent work, and follow [Protected fields](#protected-fields)
   for every update.
4. **Source fidelity.** Exact medical wording recorded in a `detail_comment` MUST match the source.
   `detail_comment` content MUST NOT be redirected to `Appointments.Notes` or `Cats.Notes`.
5. **Approval gates.** Extraction, the initial `Needs_Invoice` snapshot, and first-pass cat matching
   MUST NOT change Airtable. Work that depends on an unresolved match or decision MUST NOT proceed.
   The resolution workflow MUST use only non-empty Processing Operator resolutions for dependent
   work. Files MUST NOT be uploaded without explicit upload approval from the Processing Operator.
6. **Files and filenames.** Source filenames and extensions MUST remain unchanged. Required local
   run artifacts MUST remain together in the applicable `../bac-outputs/[run-id]/` directory and use
   their defined filenames, including `extraction.json`, `needs_invoice.json`, `cat_matches.json`,
   and `cat_match_review.json`. Do not overwrite an existing artifact unless the applicable
   instructions or Processing Operator explicitly authorize replacement.
7. **Identifiers and reporting.** Run-specific Airtable IDs MUST appear only in authorized local
   machine-readable state artifacts, including `needs_invoice.json`, an operator-requested cat
   mapping or review artifact, and any later explicitly authorized action artifact. They MUST NOT
   appear in operator-facing messages. Stable schema IDs MAY appear only in a technical schema map.
   Follow the applicable concise operator-facing reporting rules for all operator-facing wording
   and formatting.
8. **Authoritative run state.** The exact paired `extraction.json` and `needs_invoice.json`, plus
   their validated mapping and review artifacts when created, are the authoritative resumable run
   state. Resume from those artifacts rather than chat history, and do not repeat completed work.
   Record only facts, evidence, actions, decisions, dependencies, and next steps—not hidden
   reasoning.
9. **No inferred workaround.** If a required document, action, destination, or rule is unavailable,
   stop the affected work and report it. Do not infer, simulate, or claim a workaround. A blocker
   stops only work that depends on it; continue safe independent work.

## Protected fields

- `Cats.Cat Name`: Never change an existing value or add an alternate name in parentheses. Handle
  source-name differences through identity matching and comments.
- `Cats.Age`: Never change a populated value. For a blank or **Unknown** value, follow the
  applicable source-weight and age rule.
- `Cats.Gender`: Never change a populated value. For a blank or **Unknown** value, follow the
  applicable gender rule.
- `Cats.Microchip`: Never overwrite a non-empty value. For a blank value, follow the applicable
  microchip rule.
- `Cats.Color`: Never change or populate it. Use it only as identity evidence and in comments.
- `Cats.Status`: When `Appointments.Type` is **Pet** or **TNR** and the existing status is
  **Needs Trapping**, change it to **Service**. This is the only permitted `Cats.Status` change;
  never change or populate it otherwise.

Appointments, cats, and vouchers have separate status fields. Set `Appointments.Status` to
**Completed** only under the appointment-completion rules. Change the protected `Cats.Status` only
under its rule, and update `Vouchers.Status` only under the voucher rules.

## Hard stops and prohibited substitutions

- If the correct Airtable option does not exist, do not select a similar, partial, or unrelated
  option. Leave the correct value unrepresented in that structured field and report the missing
  option to the Processing Operator.
- When identity is unresolved, leave identity-dependent fields and `Appointments.Status`
  unchanged. Do not calculate a numeric identity score or assign an identity from supporting
  evidence alone outside a valid bounded cohort.
- When grouping, services, or allocation is uncertain, leave the appointment structure,
  `Appointments.Cats`, `Appointments.Services`, and affected `Appointments.Cost` unchanged. Never
  invent or evenly divide a cost allocation without source support.
- For an exact zero-dollar recheck, do not create or duplicate an appointment, do not expand the
  Processing Scope, and do not add **Recheck** to `Appointments.Additional Services`.

## Comment rules

- Every Airtable comment MUST begin `AI/[INITIALS]:`.
- Every processed cat MUST receive one `standard_comment`.
- Add a separate `detail_comment` only when a rule requires a cat-specific finding or decision to
  be retained. Otherwise, do not add one.
- Exact medical wording in a `detail_comment` MUST match the source and MUST NOT be redirected to
  `Appointments.Notes` or `Cats.Notes`.

## Voucher-driven scope expansion

After redeeming a voucher, rerun `Needs_Invoice` to discover related additions. Add only newly
surfaced target appointments demonstrably related to that voucher, plus their linked cats. Record
the voucher origin and authorization for every addition. If the rerun does not surface the expected
records, returns ambiguous candidates, or does not establish which records belong to the voucher,
do not add them; leave the affected work unchanged and report the exact discrepancy to the
Processing Operator.

## Upload and attachment rules

- Uploads require explicit Processing Operator approval.
- Attach the complete invoice only to eligible `Appointments` records represented on it whose
  `Appointments.Status` is **Completed**, including authorized completed recheck and matching
  voucher appointments.
- Do not attach the invoice to an appointment whose `Appointments.Status` is **Cancelled** or
  **No Show**.
- Upload each treatment sheet or recheck file only to the matched cat's `Cats.Paperwork` field.
- Verify every attempted upload. Verify that every eligible completed appointment has the invoice
  and every treated cat has the correct treatment sheet or recheck file.
- If approval is missing or a destination, upload, or verification result is unclear, stop the
  affected upload, leave other records and files unchanged, and report the exact problem to the
  Processing Operator.

## Compliance check

Before writing an artifact, changing Airtable, uploading a file, or handing off work, verify every
applicable invariant and instruction. If any requirement is not met, leave affected source data and
Airtable records unchanged, preserve completed safe work, and follow the applicable Processing
Operator resolution path. Do not infer a workaround.
