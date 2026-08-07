"""HTTP service runtime state."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Protocol

from monkeyocr.application.ports import OcrPipeline
from monkeyocr.infrastructure.config.settings import ServiceSettings
from monkeyocr.infrastructure.storage.artifacts import ArtifactStore
from monkeyocr.interface.http.admission import RequestAdmission


class ManagedOcrPipeline(OcrPipeline, Protocol):
    def close(self) -> None: ...


@dataclass(slots=True)
class HttpRuntime:
    settings: ServiceSettings
    artifacts: ArtifactStore
    admission: RequestAdmission
    executor: ThreadPoolExecutor
    pipeline: ManagedOcrPipeline
