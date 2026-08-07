"""Domain and application errors exposed through inbound adapters."""


class MonkeyOCRError(Exception):
    """Base class for expected MonkeyOCR failures."""


class UnsupportedMediaTypeError(MonkeyOCRError):
    """Raised when a requested operation cannot process the input suffix."""


class UploadTooLargeError(MonkeyOCRError):
    """Raised when a streamed upload crosses the configured byte limit."""


class PageLimitExceededError(MonkeyOCRError):
    """Raised when a PDF contains more pages than the service allows."""


class CapacityExceededError(MonkeyOCRError):
    """Raised when all public OCR request slots are occupied."""


class ArtifactNotFoundError(MonkeyOCRError):
    """Raised when a result artifact is absent or expired."""


class BackendUnavailableError(MonkeyOCRError):
    """Raised when the OCR inference backend is not ready."""
