# Cat Match Resolution Instructions for Codex

Read and follow [`invariants.md`](invariants.md) before applying these instructions. If these
instructions conflict with an invariant, the invariant controls.

## Purpose

Apply operator-written resolutions from the review artifact produced under
[`cat_matching_instructions.md`](cat_matching_instructions.md) to `cat_matches.json`. Resolve only
the records addressed by those instructions, preserve every unaffected accepted match, and
validate the complete result before replacing `cat_matches.json`.

This workflow may also split a shared Airtable appointment when a resolution establishes that its
linked cats received different service sets. Do not otherwise change Airtable,
`extraction.json`, `needs_invoice.json`, source PDFs, or `cat_match_review.json`.

## Inputs and authority

Read `extraction.json`, `needs_invoice.json`, `cat_matches.json`, and `cat_match_review.json` from
the same run directory. Stop without writing if an input is missing, malformed, belongs to a
different run, or fails the identity checks below.

Each review entry has this shape:

```json
[
  {
    "review_kind": "unresolved_paperwork",
    "paperwork_cat_id": "26SEP02-NLF-2",
    "paperwork_display_name": "(F) Source Cat Owner",
    "airtable_cat_id": null,
    "airtable_display_name": null,
    "match_reason": "Unresolved: two Airtable cats remain plausible.",
    "resolution": "Match this paperwork cat to Airtable cat recExample456. The paperwork name is its intake name."
  }
]
```

Only non-empty `resolution` strings are operator instructions. Treat a blank or whitespace-only
value as unresolved. Never invent, complete, or reinterpret a missing instruction. A resolution
may confirm an existing match, assign an extraction cat to an Airtable cat, state that no match
exists, direct that a record remain unresolved, or require a shared appointment row to be split.
Every non-empty review entry must contain exactly `review_kind`, `paperwork_cat_id`,
`paperwork_display_name`, `airtable_cat_id`, `airtable_display_name`, `match_reason`, and
`resolution`, and `resolution` must be a string.

The operator's resolution controls the decision it explicitly addresses, but it does not waive
identity, referential-integrity, uniqueness, service, cost, or output validation. If an instruction
is ambiguous, contradictory, references an unknown identity, or would violate an invariant, leave
all files and Airtable records unchanged and report the exact problem.

## Applying match resolutions

1. Copy the complete existing `cat_matches.json` result into memory. Do not rebuild unaffected
   matches or change their reasons.
2. Process non-empty resolutions in the existing `cat_match_review.json` order. Resolve all
   referenced extraction and Airtable identities against the two input snapshots.
3. For an operator-directed match, add or update the array entry whose `paperwork_cat_id` is the
   extraction `cat_id`. Use the exact `airtable_cat_id` and `cat_name` from `needs_invoice.json`.
   Never copy a display name from the resolution text. Preserve `paperwork_display_name` exactly
   from the extraction record's `display_name`.
4. Replace `match_reason` with a concise explanation of the applied operator resolution and its
   relevant identity evidence. Do not include hidden reasoning or claim that evidence existed when
   the resolution did not provide it.
5. A confirmation of an already populated match may improve `match_reason`, but must not otherwise
   change the entry. A direction to leave an extraction cat unresolved keeps that cat absent from
   `cat_matches.json`; its operator-directed outcome remains recorded in `cat_match_review.json`.
6. An instruction that an Airtable cat has no extraction counterpart does not create a
   `cat_matches.json` entry: that array contains accepted matches only. Preserve the
   instruction in `cat_match_review.json` as the audit record.
7. Do not infer additional matches merely because applying one resolution leaves a single pair.
   Apply another match only when its own resolution directs it or the operator explicitly asks for
   the standard matching rules to be rerun.
8. Treat all resolutions as one transaction. If any requested decision is invalid or any final
   invariant fails, do not apply a subset.

## Splitting a shared appointment row, if needed

Consider a split only when an operator resolution calls for it or when applying that resolution
confidently establishes that cats linked to one Airtable appointment received different service
sets. Identity must be resolved before any split.

- Duplicate the source appointment. Do not create an unrelated appointment manually.
- Keep every linked cat across the resulting appointment records, then unlink cats until each cat
  appears only on records whose services and costs apply to it.
- Set services and costs only when the source files or operator resolution support the exact
  per-cat values. Never divide a total evenly without itemized source support.
- When grouping, services, or cost allocation remains uncertain, leave the appointment structure,
  `Appointments.Cats`, `Appointments.Services`, and affected `Appointments.Cost` values unchanged
  and report the decision still required.
- Do not delete the source appointment or any cat record.

After a split, verify that every cat remains linked to at least one relevant appointment, no cat is
linked to a row with inapplicable services, each source-supported service and cost appears on the
correct row, and the resulting appointment costs reconcile exactly to the supported invoice total.
If live Airtable access or appointment duplication is unavailable, do not simulate or claim the
split; update no Airtable record and report the blocked action.

## Final validation and write

Before changing `cat_matches.json`, verify all of the following:

- every `paperwork_cat_id` is an extraction `cat_id`, and only matched extraction cats appear;
- every entry contains exactly `paperwork_cat_id`, `paperwork_display_name`, `airtable_cat_id`,
  `airtable_display_name`, and `match_reason`;
- every `paperwork_display_name` exactly equals that extraction record's `display_name`;
- every `airtable_cat_id` and `airtable_display_name` is non-null;
- every `airtable_cat_id` exists in `needs_invoice.json` and appears at most once;
- every populated `airtable_display_name` exactly equals the matched Airtable record's `cat_name`;
- every applied resolution is reflected exactly once and every blank resolution caused no change;
- every unaffected accepted match is byte-for-byte equivalent at the entry level; and
- any requested Airtable split completed and passed its post-split checks before the mapping file
  is written.

When all checks pass, atomically replace `cat_matches.json` in its existing run directory. This
workflow's explicit invocation authorizes that replacement; do not overwrite any other artifact.
Leave `cat_match_review.json` unchanged so the operator's resolutions remain available as an audit
record. If the resolved matches are already identical, make no write and report that no update was
needed.
