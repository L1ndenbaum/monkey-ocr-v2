"""Framework-free MonkeyOCR domain model."""

from monkeyocr.domain.models import ParsedDocument, RecognizedContent
from monkeyocr.domain.tasks import OcrTask

__all__ = ["OcrTask", "ParsedDocument", "RecognizedContent"]
