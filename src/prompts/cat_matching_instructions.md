# Cat Matching Instructions for Codex

Read and follow [`invariants.md`](invariants.md) before applying these instructions. If these
instructions conflict with an invariant, the invariant controls.

## Purpose

Create a complete, deterministic map from every extraction cat ID to its matching Airtable cat,
plus a review artifact identifying every cat on either side that could not be confidently matched.
Use only the supplied `extraction.json` and `needs_invoice.json`. This task is read-only: do not
change Airtable, either input file, or any source PDF.

## Inputs

### Extraction cats

Read `extraction.json`. For each item in `cats`, use:

- `cat_id` as the output object's top-level key;
- `cat_name` as the manifest-verified source name;
- `display_name` as the complete name printed on the treatment sheet;
- `owner_name`; and
- the appointment for the requested date, including `gender`, `microchip_number`, `color`,
  `weight`, `services`, and `total_cost`.

Do not treat the extraction `display_name` as an Airtable display name.

### Airtable cats

Read `needs_invoice.json`. For each item in `cats`, use:

- `airtable_cat_id` as the Airtable identity written to `airtable_id`;
- `cat_name` as the value written to `airtable_display_name`; and
- all available identity evidence, including owner/trapper, address or location, microchip,
  voucher number, appointment date and type, gender, color, age, ear-tip status, services, and
  appointment context.

Some snapshots may omit optional identity fields. Treat an omitted or `null` field as unavailable,
not as agreement or contradiction. Never invent a value.

## Matching rules

1. Process the extraction cats in their existing array order. Build one complete result in memory
   and write it only after all entries and invariants have been validated.
2. Do not calculate a numeric match score. Evaluate strong evidence first, then supporting
   evidence. A material contradiction takes precedence over an otherwise plausible match.
3. Strong evidence is:
   - an exact cat-name match or an obvious variation, such as differences in spacing,
     punctuation, capitalization, or an unambiguous minor spelling variation;
   - consistent owner/trapper evidence;
   - consistent address or location evidence;
   - an exact microchip number; or
   - an exact voucher number.
4. Supporting evidence is gender, color, age, weight, ear-tip status, appointment type, services,
   appointment date, and treatment context. Supporting evidence alone does not establish an
   otherwise unbounded identity. Color, gender, age, weight, ear-tip status, and appointment type
   are never conclusive by themselves.
5. Accept a direct match when strong evidence supports it and no material evidence conflicts.
6. Accept a remaining-pair match only when exactly one extraction cat and one Airtable cat remain,
   all available evidence is consistent, and no identifier conflicts. State in `match_reason` that
   it is a remaining-pair inference.
7. A bounded cohort may be matched when all of the following are true:
   - the extraction cats and unmatched Airtable cats share a clear owner/trapper, appointment,
     location, or equivalent group boundary;
   - appointment types are consistent when available;
   - the two groups have equal counts;
   - every cat is used exactly once; and
   - no microchip or other strong identifier contradicts the group or a proposed pair.

   Within a valid cohort, prefer exact or obvious name variations, then aligned distinguishing
   tokens, then the remaining supporting evidence. If multiple assignments remain equally
   plausible, pair the remaining extraction order with the remaining Airtable array order so a
   repeated run produces the same result. State that bounded-cohort inference in each affected
   `match_reason`.
8. Do not use equal counts alone to justify a match. Do not use bounded-cohort matching when the
   group boundary is unclear, a cat could belong to another cohort, or strong evidence conflicts.
9. Harmless color variations, such as `Grey` versus `Grey Tabby`, do not conflict. Materially
   incompatible descriptions may be identity conflicts. Name similarity never overrides an exact
   microchip or voucher conflict.
10. If strong evidence is absent, multiple Airtable cats remain plausible outside a valid bounded
    cohort, or material evidence conflicts, leave the extraction cat unresolved. Do not guess.
11. After matching, identify every unresolved extraction cat, every match accepted only through
    the bounded-cohort array-order tie-breaker, and every Airtable cat that was not assigned to an
    extraction cat. Include all of them in `cat_match_review.json`. A deterministic tie-breaker is
    not the same as a confident identity match, and an unassigned Airtable cat must not be silently
    discarded even when no extraction cat appears plausible.

## Output contract

Produce valid JSON containing exactly one top-level entry for every confidently matched extraction
cat and no entry for an unresolved extraction cat. Use the extraction `cat_id` as the top-level key
and use this exact shape and these exact field names:

```json
{
  "26SEP02-NLF-1": {
    "extraction_cat_display_name": "(F) Example Cat Owner",
    "airtable_id": "recExample123",
    "airtable_display_name": "Example Cat",
    "match_reason": "Exact cat-name match; owner and appointment context are consistent, with no conflicting identifier."
  }
}
```

Do not put an unresolved extraction cat in `cat_mapping.json`. Put it in `cat_match_review.json`
instead:

```json
{
  "26SEP02-NLF-2": {
    "extraction_cat_display_name": "(F) Source Cat Owner",
    "airtable_id": null,
    "airtable_display_name": null,
    "match_reason": "Unresolved: two Airtable cats remain plausible and no strong evidence distinguishes them.",
    "resolution": ""
  }
}
```

The `match_reason` must be concise but specific. Name the decisive evidence, the matching rule
(direct, remaining-pair, or bounded cohort), and any relevant harmless variation. For unresolved
entries, name the missing or conflicting evidence. Do not include hidden reasoning, a numeric
score, or unsupported certainty.

Before writing `cat_mapping.json`, verify all of the following:

- every top-level key is an extraction `cat_id` from `extraction.json`;
- every confidently matched extraction `cat_id` appears exactly once;
- no unresolved extraction `cat_id` appears;
- every `extraction_cat_display_name` exactly equals that extraction record's `display_name`;
- every `airtable_id` and `airtable_display_name` is non-null;
- every `airtable_id` exists in `needs_invoice.json`;
- no `airtable_id` is assigned more than once;
- every `airtable_display_name` exactly equals the matched Airtable record's `cat_name`;
- each matched pair has no unresolved material contradiction; and
- the output has no fields beyond `extraction_cat_display_name`, `airtable_id`,
  `airtable_display_name`, and `match_reason`.

Also produce `cat_match_review.json` for records requiring human review. Use the same top-level
mapping format as `cat_mapping.json`, plus an empty `resolution` string for operator instructions:

- For an unresolved extraction cat, use its extraction `cat_id` as the top-level key, set both
  Airtable fields to `null`, populate `extraction_cat_display_name` from `extraction.json`, and
  state its specific unresolved `match_reason`.
- For a match accepted only through the bounded-cohort array-order tie-breaker, use its extraction
  `cat_id` as the top-level key and copy the complete populated entry from `cat_mapping.json`. Its
  `match_reason` must make clear that the pair needs human review because the available evidence
  did not distinguish the equally plausible assignments.
- For an unassigned Airtable cat, use its `airtable_cat_id` as the top-level key, populate
  `airtable_id` and `airtable_display_name` from that record, set
  `extraction_cat_display_name` to
  `null`, and explain in `match_reason` why it was not confidently assigned. The reason must
  distinguish no plausible extraction cat, ambiguity among extraction cats, and conflicting
  evidence when applicable.
- If an unresolved extraction cat has one or more plausible Airtable candidates, include the
  extraction entry and include each still-unassigned candidate as its own Airtable entry. Explain
  the ambiguity from the perspective of each entry without asserting a tentative match.
- If every extraction cat is confidently matched without an array-order tie-breaker and every
  Airtable cat is assigned, write an empty JSON object (`{}`).
- Set `resolution` to an empty string in every entry. Do not propose choices or fill this field;
  it is reserved for the operator's instructions to the
  [`cat_match_resolution_instructions.md`](cat_match_resolution_instructions.md) workflow.

Example review artifact:

```json
{
  "26SEP02-NLF-2": {
    "extraction_cat_display_name": "(F) Source Cat Owner",
    "airtable_id": null,
    "airtable_display_name": null,
    "match_reason": "Unresolved: two Airtable cats remain plausible and no strong evidence distinguishes them.",
    "resolution": ""
  },
  "recExample456": {
    "extraction_cat_display_name": null,
    "airtable_id": "recExample456",
    "airtable_display_name": "Possible Cat",
    "match_reason": "Unassigned Airtable cat: it remains one of two plausible candidates for extraction cat 26SEP02-NLF-2.",
    "resolution": ""
  }
}
```

Before writing either artifact, verify the complete in-memory result. In addition to the
`cat_mapping.json` checks above, verify that:

- `cat_match_review.json` contains every and only unresolved extraction cats, extraction matches
  accepted through the bounded-cohort array-order tie-breaker, and unassigned Airtable cats;
- the four mapping fields in every extraction-keyed bounded-cohort tie-breaker review entry exactly
  match its entry in `cat_mapping.json`;
- every unresolved extraction-keyed review entry is absent from `cat_mapping.json` and has null
  `airtable_id` and `airtable_display_name`;
- every extraction-keyed `extraction_cat_display_name` exactly equals that extraction record's
  `display_name`, and every Airtable-keyed `extraction_cat_display_name` is `null`;
- every Airtable-keyed entry's key and `airtable_id` are identical and exist in
  `needs_invoice.json`;
- every Airtable-keyed `airtable_display_name` exactly equals that record's `cat_name`; and
- `cat_mapping.json` contains only `extraction_cat_display_name`, `airtable_id`,
  `airtable_display_name`, and `match_reason`, while every non-empty review entry contains exactly
  those fields plus `resolution: ""`.

Finally, verify completeness across both artifacts: every extraction cat is either present as a
matched entry in `cat_mapping.json` or present as an unresolved entry in `cat_match_review.json`.
A bounded-cohort tie-breaker match may intentionally appear in both. Every Airtable cat is either
assigned once in `cat_mapping.json` or represented as an unassigned Airtable entry in
`cat_match_review.json`.

Save `cat_mapping.json` and `cat_match_review.json` together under the sibling
`../bac-outputs/` directory, in the applicable run directory or another output path explicitly
supplied by the operator. Do not write either file unless both artifacts pass validation. Never
overwrite an existing artifact unless the operator explicitly requests it.
