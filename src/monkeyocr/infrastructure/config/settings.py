"""Environment-backed service settings."""

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class ServiceSettings:
    model_path: Path = Path("/models/MonkeyOCRv2-B-Parsing")
    server_url: str = "http://vllm:8888"
    served_model_name: str = "MonkeyOCRv2"
    output_dir: Path = Path("/var/lib/monkeyocr/results")
    token_file: Path = Path("/run/secrets/monkeyocr_api_token")
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    max_upload_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 50
    max_concurrency: int = 4
    page_max_inflight: int = 4
    server_max_inflight: int = 4
    result_ttl_seconds: int = 6 * 60 * 60
    cleanup_interval_seconds: int = 15 * 60
    request_timeout_seconds: int = 300
    http_max_retries: int = 5
    http_retry_backoff: float = 1.0
    preprocess_batch_size: int = 32
    preprocess_wait_seconds: float = 1.0
    max_pixels: int = 1_003_520
    tensor_parallel_size: int = 1
    skip_preprocess: bool = False
    end2end: bool = False
    retry_repeat: bool = False
    keep_header_footer: bool = False
    use_base64: bool = False
    debug: bool = False
    docs_enabled: bool = False

    @classmethod
    def from_env(cls) -> "ServiceSettings":
        defaults = cls()
        return cls(
            model_path=Path(os.getenv("MONKEYOCR_MODEL_PATH", defaults.model_path)),
            server_url=os.getenv("MONKEYOCR_SERVER_URL", defaults.server_url),
            served_model_name=os.getenv(
                "MONKEYOCR_SERVED_MODEL_NAME",
                defaults.served_model_name,
            ),
            output_dir=Path(os.getenv("MONKEYOCR_OUTPUT_DIR", defaults.output_dir)),
            token_file=Path(os.getenv("MONKEYOCR_TOKEN_FILE", defaults.token_file)),
            api_host=os.getenv("MONKEYOCR_API_HOST", defaults.api_host),
            api_port=int(os.getenv("MONKEYOCR_API_PORT", defaults.api_port)),
            max_upload_bytes=int(
                os.getenv("MONKEYOCR_MAX_UPLOAD_BYTES", defaults.max_upload_bytes)
            ),
            max_pdf_pages=int(os.getenv("MONKEYOCR_MAX_PDF_PAGES", defaults.max_pdf_pages)),
            max_concurrency=int(os.getenv("MONKEYOCR_MAX_CONCURRENCY", defaults.max_concurrency)),
            page_max_inflight=int(
                os.getenv("MONKEYOCR_PAGE_MAX_INFLIGHT", defaults.page_max_inflight)
            ),
            server_max_inflight=int(
                os.getenv("MONKEYOCR_SERVER_MAX_INFLIGHT", defaults.server_max_inflight)
            ),
            result_ttl_seconds=int(
                os.getenv("MONKEYOCR_RESULT_TTL_SECONDS", defaults.result_ttl_seconds)
            ),
            cleanup_interval_seconds=int(
                os.getenv(
                    "MONKEYOCR_CLEANUP_INTERVAL_SECONDS",
                    defaults.cleanup_interval_seconds,
                )
            ),
            request_timeout_seconds=int(
                os.getenv(
                    "MONKEYOCR_REQUEST_TIMEOUT_SECONDS",
                    defaults.request_timeout_seconds,
                )
            ),
            http_max_retries=int(
                os.getenv("MONKEYOCR_HTTP_MAX_RETRIES", defaults.http_max_retries)
            ),
            http_retry_backoff=float(
                os.getenv("MONKEYOCR_HTTP_RETRY_BACKOFF", defaults.http_retry_backoff)
            ),
            preprocess_batch_size=int(
                os.getenv(
                    "MONKEYOCR_PREPROCESS_BATCH_SIZE",
                    defaults.preprocess_batch_size,
                )
            ),
            preprocess_wait_seconds=float(
                os.getenv(
                    "MONKEYOCR_PREPROCESS_WAIT_SECONDS",
                    defaults.preprocess_wait_seconds,
                )
            ),
            max_pixels=int(os.getenv("MONKEYOCR_MAX_PIXELS", defaults.max_pixels)),
            tensor_parallel_size=int(
                os.getenv("MONKEYOCR_TENSOR_PARALLEL_SIZE", defaults.tensor_parallel_size)
            ),
            skip_preprocess=_env_bool("MONKEYOCR_SKIP_PREPROCESS", defaults.skip_preprocess),
            end2end=_env_bool("MONKEYOCR_END2END", defaults.end2end),
            retry_repeat=_env_bool("MONKEYOCR_RETRY_REPEAT", defaults.retry_repeat),
            keep_header_footer=_env_bool(
                "MONKEYOCR_KEEP_HEADER_FOOTER",
                defaults.keep_header_footer,
            ),
            use_base64=_env_bool("MONKEYOCR_USE_BASE64", defaults.use_base64),
            debug=_env_bool("MONKEYOCR_DEBUG", defaults.debug),
            docs_enabled=_env_bool("MONKEYOCR_DOCS_ENABLED", defaults.docs_enabled),
        )
