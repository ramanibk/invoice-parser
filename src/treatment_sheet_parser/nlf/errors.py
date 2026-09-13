"""Report failures from the NLF extraction workflow."""


class TreatmentSheetError(ValueError):
    """Raised when an NLF treatment-sheet PDF cannot be parsed."""


class InvoiceError(ValueError):
    """Raised when an NLF invoice is unreadable or inconsistent."""


class ExtractionError(ValueError):
    """Raised when an NLF extraction input, match, or output is invalid."""
