"""Run the MonkeyOCR public HTTP service."""

import uvicorn

from monkeyocr.infrastructure.config.settings import ServiceSettings
from monkeyocr.interface.http.app import create_app


def main() -> None:
    settings = ServiceSettings.from_env()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
        log_level="debug" if settings.debug else "info",
        workers=1,
    )


if __name__ == "__main__":
    main()
