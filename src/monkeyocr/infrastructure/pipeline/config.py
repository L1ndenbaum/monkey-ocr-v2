"""Configuration values shared by MonkeyOCR pipeline adapters."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class BackendConfig:
    model_path: str
    server_url: str = ""
    served_model_name: str = "MonkeyOCRv2"
    tp: int = 1
    max_pixels: int = 1_003_520
    request_timeout: int = 300
    http_max_retries: int = 5
    http_retry_backoff: float = 1.0
    server_max_inflight: int = 4
    preprocess_batch_size: int = 32
    skip_preprocess: bool = False


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    input_path: str
    output_path: str
    backend: BackendConfig
    page_max_inflight: int = 4
    draw_layout: bool = False
    end2end: bool = False
    skip_processed: bool = False
    retry_repeat: bool = False
    retry_repeat_max_retries: int = 3
    keep_header_footer: bool = False
    use_base64: bool = False
    show_progress_bar: bool = False
    verbose: bool = True


@dataclass(frozen=True, slots=True)
class OutputDirs:
    out_dir: Path
    json_dir: Path
    md_dir: Path
    image_dir: Path
    preprocessed_dir: Path
    layout_dir: Path | None
