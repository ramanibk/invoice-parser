"""Report invalid inputs to the cat-mapping prompt workflow."""


class CatMappingPromptError(ValueError):
    """Raised when prompt inputs cannot identify one valid paired run."""
