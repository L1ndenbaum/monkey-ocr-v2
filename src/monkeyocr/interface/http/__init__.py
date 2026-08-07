"""FastAPI composition root."""

from monkeyocr.interface.http.app import create_app

__all__ = ["create_app"]
