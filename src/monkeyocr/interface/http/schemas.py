"""Versioned HTTP response schemas."""

from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class InternalCode(StrEnum):
    SUCCESS = "SUCCESS"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    INVALID_REQUEST = "INVALID_REQUEST"
    INVALID_DOCUMENT = "INVALID_DOCUMENT"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    PAGE_LIMIT_EXCEEDED = "PAGE_LIMIT_EXCEEDED"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    BACKEND_UNAVAILABLE = "BACKEND_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ApiEnvelope(BaseModel, Generic[T]):
    internal_code: InternalCode
    message: str
    data: T | None


class ParseData(BaseModel):
    request_id: str
    files: tuple[str, ...]
    artifact_download_url: str


class RecognitionData(BaseModel):
    request_id: str
    task: str
    content: str
