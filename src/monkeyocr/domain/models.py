"""Framework-independent OCR result values."""

from dataclasses import dataclass

from monkeyocr.domain.tasks import OcrTask


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    request_id: str
    artifact_name: str
    files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RecognizedContent:
    request_id: str
    task: OcrTask
    content: str
