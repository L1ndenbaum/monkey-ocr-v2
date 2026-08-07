"""Map expected failures to stable HTTP envelopes."""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from monkeyocr.domain.errors import (
    ArtifactNotFoundError,
    BackendUnavailableError,
    CapacityExceededError,
    InvalidDocumentError,
    MonkeyOCRError,
    PageLimitExceededError,
    UnsupportedMediaTypeError,
    UploadTooLargeError,
)
from monkeyocr.interface.http.schemas import ApiEnvelope, InternalCode

ERROR_MAP: dict[type[MonkeyOCRError], tuple[int, InternalCode]] = {
    UnsupportedMediaTypeError: (415, InternalCode.UNSUPPORTED_MEDIA_TYPE),
    InvalidDocumentError: (400, InternalCode.INVALID_DOCUMENT),
    UploadTooLargeError: (413, InternalCode.UPLOAD_TOO_LARGE),
    PageLimitExceededError: (422, InternalCode.PAGE_LIMIT_EXCEEDED),
    CapacityExceededError: (429, InternalCode.CAPACITY_EXCEEDED),
    ArtifactNotFoundError: (404, InternalCode.ARTIFACT_NOT_FOUND),
    BackendUnavailableError: (503, InternalCode.BACKEND_UNAVAILABLE),
}


def _response(status: int, code: InternalCode, message: str) -> JSONResponse:
    envelope = ApiEnvelope[None](internal_code=code, message=message, data=None)
    headers = {"Retry-After": "1"} if status == 429 else None
    return JSONResponse(
        status_code=status,
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )


async def monkeyocr_error_handler(_request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, MonkeyOCRError)
    status, code = ERROR_MAP.get(type(exc), (400, InternalCode.INVALID_REQUEST))
    return _response(status, code, str(exc))


async def validation_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
    return _response(422, InternalCode.INVALID_REQUEST, "Request validation failed.")


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    message = f"{type(exc).__name__}: {exc}" if request.app.debug else "Internal server error."
    return _response(500, InternalCode.INTERNAL_ERROR, message)


HANDLERS = {
    MonkeyOCRError: monkeyocr_error_handler,
    RequestValidationError: validation_error_handler,
    Exception: unexpected_error_handler,
}
