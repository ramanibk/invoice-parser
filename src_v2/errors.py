"""Define the shared failure type for the rewritten invoice pipeline."""


class PipelineError(ValueError):
    """Raised when pipeline input or state prevents a valid complete run."""
