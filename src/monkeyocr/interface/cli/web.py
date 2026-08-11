"""Run the internal React BFF service."""

import uvicorn

from monkeyocr.infrastructure.config.web_settings import WebSettings


def main() -> None:
    settings = WebSettings.from_env()
    uvicorn.run(
        "monkeyocr.interface.http.internal_web.app:create_internal_web_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_config=None,
    )


if __name__ == "__main__":
    main()
