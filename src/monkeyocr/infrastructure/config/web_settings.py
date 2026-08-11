"""Environment-backed settings for the internal Web service."""

import os
from dataclasses import dataclass
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class WebSettings:
    host: str = "0.0.0.0"
    port: int = 8080
    ocr_base_url: str = "http://api:8000"
    token_file: Path = Path("/run/secrets/monkeyocr_api_token")
    result_dir: Path = Path("/var/lib/monkeyocr-web/jobs")
    static_dir: Path = Path("/app/frontend/dist")
    max_upload_bytes: int = 50 * 1024 * 1024
    max_pdf_pages: int = 50
    queue_size: int = 20
    workers: int = 1
    result_ttl_seconds: int = 6 * 60 * 60
    cleanup_interval_seconds: int = 15 * 60
    upstream_timeout_seconds: int = 60 * 60
    max_extracted_bytes: int = 1024 * 1024 * 1024
    max_archive_files: int = 10_000
    cookie_name: str = "monkeyocr_ui_session"
    cookie_path: str = "/ocr-ui"
    cookie_secure: bool = True
    debug: bool = False

    @classmethod
    def from_env(cls) -> "WebSettings":
        defaults = cls()
        return cls(
            host=os.getenv("MONKEYOCR_WEB_HOST", defaults.host),
            port=int(os.getenv("MONKEYOCR_WEB_PORT", defaults.port)),
            ocr_base_url=os.getenv(
                "MONKEYOCR_WEB_OCR_BASE_URL",
                defaults.ocr_base_url,
            ).rstrip("/"),
            token_file=Path(os.getenv("MONKEYOCR_WEB_TOKEN_FILE", defaults.token_file)),
            result_dir=Path(os.getenv("MONKEYOCR_WEB_RESULT_DIR", defaults.result_dir)),
            static_dir=Path(os.getenv("MONKEYOCR_WEB_STATIC_DIR", defaults.static_dir)),
            max_upload_bytes=int(
                os.getenv("MONKEYOCR_WEB_MAX_UPLOAD_BYTES", defaults.max_upload_bytes)
            ),
            max_pdf_pages=int(os.getenv("MONKEYOCR_WEB_MAX_PDF_PAGES", defaults.max_pdf_pages)),
            queue_size=int(os.getenv("MONKEYOCR_WEB_QUEUE_SIZE", defaults.queue_size)),
            workers=int(os.getenv("MONKEYOCR_WEB_WORKERS", defaults.workers)),
            result_ttl_seconds=int(
                os.getenv(
                    "MONKEYOCR_WEB_RESULT_TTL_SECONDS",
                    defaults.result_ttl_seconds,
                )
            ),
            cleanup_interval_seconds=int(
                os.getenv(
                    "MONKEYOCR_WEB_CLEANUP_INTERVAL_SECONDS",
                    defaults.cleanup_interval_seconds,
                )
            ),
            upstream_timeout_seconds=int(
                os.getenv(
                    "MONKEYOCR_WEB_UPSTREAM_TIMEOUT_SECONDS",
                    defaults.upstream_timeout_seconds,
                )
            ),
            max_extracted_bytes=int(
                os.getenv(
                    "MONKEYOCR_WEB_MAX_EXTRACTED_BYTES",
                    defaults.max_extracted_bytes,
                )
            ),
            max_archive_files=int(
                os.getenv(
                    "MONKEYOCR_WEB_MAX_ARCHIVE_FILES",
                    defaults.max_archive_files,
                )
            ),
            cookie_name=os.getenv("MONKEYOCR_WEB_COOKIE_NAME", defaults.cookie_name),
            cookie_path=os.getenv("MONKEYOCR_WEB_COOKIE_PATH", defaults.cookie_path),
            cookie_secure=_env_bool(
                "MONKEYOCR_WEB_COOKIE_SECURE",
                defaults.cookie_secure,
            ),
            debug=_env_bool("MONKEYOCR_WEB_DEBUG", defaults.debug),
        )
