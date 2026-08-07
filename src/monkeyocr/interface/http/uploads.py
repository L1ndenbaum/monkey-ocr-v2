"""Bounded upload streaming and PDF page validation."""

import re
from pathlib import Path

import aiofiles
import anyio
from fastapi import UploadFile
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from monkeyocr.domain.errors import (
    InvalidDocumentError,
    PageLimitExceededError,
    UploadTooLargeError,
)

CHUNK_BYTES = 1024 * 1024
UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_upload_name(filename: str | None) -> str:
    name = (filename or "upload.bin").replace("\\", "/").rsplit("/", 1)[-1]
    suffix = Path(name).suffix.lower()
    stem = UNSAFE_FILENAME.sub("_", Path(name).stem).strip("._") or "upload"
    suffix = suffix if 1 < len(suffix) <= 16 else ".bin"
    return f"{stem[:128]}{suffix}"


async def save_bounded_upload(upload: UploadFile, workspace: Path, limit: int) -> Path:
    destination = workspace / safe_upload_name(upload.filename)
    received = 0
    try:
        async with aiofiles.open(destination, "xb") as stream:
            while chunk := await upload.read(CHUNK_BYTES):
                received += len(chunk)
                if received > limit:
                    raise UploadTooLargeError(f"Upload exceeds the {limit}-byte limit.")
                await stream.write(chunk)
    finally:
        await upload.close()
    return destination


def _count_pdf_pages(path: Path) -> int:
    try:
        return len(PdfReader(path, strict=False).pages)
    except (OSError, PdfReadError, ValueError) as exc:
        raise InvalidDocumentError("Uploaded PDF cannot be decoded.") from exc


async def validate_pdf_page_limit(path: Path, limit: int) -> None:
    if path.suffix.lower() != ".pdf":
        return
    pages = await anyio.to_thread.run_sync(_count_pdf_pages, path)
    if pages > limit:
        raise PageLimitExceededError(f"PDF contains {pages} pages; the limit is {limit}.")
