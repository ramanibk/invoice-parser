"""Define the shared failure type for the rewritten invoice pipeline."""


# A single domain error lets the CLI report expected validation and I/O failures
# cleanly while leaving unexpected programming errors visible during development.
class PipelineError(ValueError):
    """Raised when pipeline input or state prevents a valid complete run."""
