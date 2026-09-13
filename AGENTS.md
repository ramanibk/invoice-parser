# Simple parser instructions

- Keep this package robust and deliberately small. Prefer explicit validation and small,
  single-purpose functions over abstractions or cleverness.
- Keep all functions within the configured Ruff McCabe complexity limit of 5.
- For every new source file, add a concise module docstring that explains its purpose. Add clear,
  focused docstrings to functions and classes where their behavior, inputs, outputs, side effects,
  or failure modes are not immediately obvious. Use straightforward comments to explain important
  reasoning, constraints, and invariants; add inline comments only where they improve understanding.
  Keep all documentation accurate and informative without restating the code or becoming verbose.
- Every AI instruction prompt must require the AI to read and follow `src/prompts/invariants.md`.
  A prompt may add stricter requirements, but it must not weaken an invariant.
- Add focused tests for success, malformed inputs, and identity mismatches.
- Never write partial extraction output. Validate every manifest entry and treatment sheet before
  creating a run directory.
- Treat source manifests and PDFs as read-only inputs. Generated files belong under the sibling
  `../bac-outputs/` directory.
- Keep the virtual environment at `.venv`, then run `uv run pytest`,
  `uv run ruff format --check`, and `uv run ruff check` before handoff.
