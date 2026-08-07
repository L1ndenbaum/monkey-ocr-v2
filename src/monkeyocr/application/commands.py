"""Application command DTOs."""

from dataclasses import dataclass
from pathlib import Path

from monkeyocr.domain.tasks import OcrTask


@dataclass(frozen=True, slots=True)
class ParseDocumentCommand:
    request_id: str
    input_path: Path
    output_dir: Path


@dataclass(frozen=True, slots=True)
class RecognizeImageCommand:
    request_id: str
    input_path: Path
    output_dir: Path
    task: OcrTask
