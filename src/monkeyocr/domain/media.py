"""Supported MonkeyOCR media types."""

from enum import StrEnum

from monkeyocr.domain.errors import UnsupportedMediaTypeError

IMAGE_SUFFIXES = frozenset({".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"})
DOCUMENT_SUFFIXES = IMAGE_SUFFIXES | {".pdf"}


class MediaKind(StrEnum):
    IMAGE = "image"
    PDF = "pdf"


def classify_media(suffix: str, *, images_only: bool = False) -> MediaKind:
    """Classify a normalized filename suffix without touching the filesystem."""
    normalized = suffix.strip().lower()
    if normalized in IMAGE_SUFFIXES:
        return MediaKind.IMAGE
    if normalized == ".pdf" and not images_only:
        return MediaKind.PDF
    expected = "an image" if images_only else "a PDF or image"
    raise UnsupportedMediaTypeError(f"Input must be {expected}.")
