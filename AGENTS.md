# Simple parser instructions

- Keep this package robust and deliberately small. Prefer explicit validation and small,
  single-purpose functions over abstractions or cleverness.
- Keep all functions within the configured Ruff McCabe complexity limit of 5.
- Every Python source and test module must have a concise module docstring that explains its
  purpose. Every class and every function or method declared with `def` or `async def`, including
  private helpers, nested functions, fixtures, and tests, must have a clear, focused docstring.
  Use a one-line docstring when the contract is simple; document behavior, inputs, outputs, side
  effects, and failure modes when they are not obvious. This rule applies to existing and future
  code. Use straightforward comments to explain important reasoning, constraints, and invariants;
  add inline comments only where they improve understanding. Keep all documentation accurate and
  informative without restating the code or becoming verbose.
- Every AI instruction prompt must require the AI to read and follow `src/prompts/invariants.md`.
  A prompt may add stricter requirements, but it must not weaken an invariant.
- Add focused tests for success, malformed inputs, and identity mismatches.
- Never write partial extraction output. Validate every manifest entry and treatment sheet before
  creating a run directory.
- Treat source manifests and PDFs as read-only inputs. Generated files belong under the sibling
  `../bac-outputs/` directory.
- Keep the virtual environment at `.venv`, then run `uv run pytest`,
  `uv run ruff format --check`, and `uv run ruff check` before handoff.
