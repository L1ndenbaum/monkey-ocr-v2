"""Outbound ports required by OCR use cases."""

from pathlib import Path
from typing import Protocol

from monkeyocr.domain.tasks import OcrTask


class OcrPipeline(Protocol):
    """Adapter contract for the GPU-backed parsing pipeline."""

    def parse(self, input_path: Path, output_dir: Path) -> tuple[str, tuple[str, ...]]:
        """Parse one document and return artifact name plus relative output files."""
        ...

    def recognize(self, input_path: Path, output_dir: Path, task: OcrTask) -> str:
        """Recognize one image for a specialized OCR task."""
        ...
