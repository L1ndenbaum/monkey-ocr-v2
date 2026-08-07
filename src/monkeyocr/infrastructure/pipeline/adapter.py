"""Application-port adapter for the existing MonkeyOCR inference pipeline."""

from pathlib import Path
from typing import Any

from monkeyocr.domain.tasks import OcrTask
from monkeyocr.infrastructure.pipeline.config import BackendConfig, PipelineConfig
from monkeyocr.infrastructure.pipeline.runner import BackendManager, ServicePipelinePool
from monkeyocr.infrastructure.storage.artifacts import make_artifact_filename, zip_dir


class MonkeyOcrPipelineAdapter:
    """Own the heavyweight model lifecycle behind the application port."""

    def __init__(
        self,
        backend_config: BackendConfig,
        *,
        page_max_inflight: int = 4,
        preprocess_wait_seconds: float = 1.0,
        end2end: bool = False,
        retry_repeat: bool = False,
        keep_header_footer: bool = False,
        use_base64: bool = False,
        debug: bool = False,
    ) -> None:
        self._backend_config = backend_config
        self._page_max_inflight = max(1, page_max_inflight)
        self._end2end = end2end
        self._retry_repeat = retry_repeat
        self._keep_header_footer = keep_header_footer
        self._use_base64 = use_base64
        self._manager = BackendManager()
        self._manager.get(backend_config)
        self._pool = ServicePipelinePool(
            backend_config,
            self._page_max_inflight,
            backend_manager=self._manager,
            batch_wait_seconds=max(0.0, preprocess_wait_seconds),
            debug=debug,
        )

    def parse(self, input_path: Path, output_dir: Path) -> tuple[str, tuple[str, ...]]:
        self._pool.run(
            PipelineConfig(
                input_path=str(input_path),
                output_path=str(output_dir),
                backend=self._backend_config,
                page_max_inflight=self._page_max_inflight,
                end2end=self._end2end,
                retry_repeat=self._retry_repeat,
                keep_header_footer=self._keep_header_footer,
                use_base64=self._use_base64,
                verbose=False,
            )
        )
        artifact_name = make_artifact_filename(input_path.stem, "_results.zip")
        zip_dir(output_dir, output_dir / artifact_name)
        files = tuple(
            sorted(
                str(path.relative_to(output_dir))
                for path in output_dir.rglob("*")
                if path.is_file()
            )
        )
        return artifact_name, files

    def recognize(self, input_path: Path, output_dir: Path, task: OcrTask) -> str:
        result: dict[str, Any] = self._pool.run_single_task(
            str(input_path),
            str(output_dir),
            task.value,
        )
        pages = result.get("results", [])
        outputs = pages[0].get("outputs", []) if pages else []
        return "\n\n".join(str(output).strip() for output in outputs if output is not None)

    def close(self) -> None:
        self._pool.close()
        self._manager.close()
