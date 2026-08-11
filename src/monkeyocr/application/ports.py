"""Outbound ports required by OCR use cases."""

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from monkeyocr.domain.tasks import OcrTask

if TYPE_CHECKING:
    from monkeyocr.application.web_jobs import WebJobResult


class OcrPipeline(Protocol):
    """Adapter contract for the GPU-backed parsing pipeline."""

    def parse(self, input_path: Path, output_dir: Path) -> tuple[str, tuple[str, ...]]:
        """Parse one document and return artifact name plus relative output files."""
        ...

    def recognize(self, input_path: Path, output_dir: Path, task: OcrTask) -> str:
        """Recognize one image for a specialized OCR task."""
        ...


class InternalWebOcrGateway(Protocol):
    """Server-side adapter used by the internal Web application."""

    async def execute(
        self,
        mode: str,
        input_path: Path,
        workspace: Path,
        report_stage: Callable[[str], None],
    ) -> "WebJobResult":
        """Run one OCR request and materialize a safe local result."""
        ...
