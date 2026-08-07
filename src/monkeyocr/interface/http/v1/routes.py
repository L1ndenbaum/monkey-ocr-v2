"""Public OCR API v1 routes."""

import asyncio
import uuid
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse

from monkeyocr.application.commands import ParseDocumentCommand, RecognizeImageCommand
from monkeyocr.application.use_cases import ParseDocument, RecognizeImage
from monkeyocr.domain.errors import CapacityExceededError
from monkeyocr.domain.tasks import OcrTask
from monkeyocr.interface.http.runtime import HttpRuntime
from monkeyocr.interface.http.schemas import (
    ApiEnvelope,
    InternalCode,
    ParseData,
    RecognitionData,
)
from monkeyocr.interface.http.uploads import save_bounded_upload, validate_pdf_page_limit

router = APIRouter(prefix="/api/v1")


def _runtime(request: Request) -> HttpRuntime:
    return cast(HttpRuntime, request.app.state.runtime)


async def _start_request(runtime: HttpRuntime) -> tuple[str, Path]:
    if not await runtime.admission.try_acquire():
        raise CapacityExceededError("OCR capacity is full; retry later.")
    request_id = str(uuid.uuid4())
    try:
        return request_id, runtime.artifacts.create_workspace(request_id)
    except Exception:
        await runtime.admission.release()
        raise


@router.post("/parse", response_model=ApiEnvelope[ParseData])
async def parse_document(
    request: Request,
    file: Annotated[UploadFile, File()],
) -> ApiEnvelope[ParseData]:
    runtime = _runtime(request)
    request_id, workspace = await _start_request(runtime)
    keep_workspace = False
    try:
        input_path = await save_bounded_upload(
            file,
            workspace,
            runtime.settings.max_upload_bytes,
        )
        await validate_pdf_page_limit(input_path, runtime.settings.max_pdf_pages)
        command = ParseDocumentCommand(request_id, input_path, workspace)
        result = await asyncio.get_running_loop().run_in_executor(
            runtime.executor,
            ParseDocument(runtime.pipeline).execute,
            command,
        )
        keep_workspace = True
        return ApiEnvelope(
            internal_code=InternalCode.SUCCESS,
            message="Document parsed successfully.",
            data=ParseData(
                request_id=result.request_id,
                files=result.files,
                artifact_download_url=f"/api/v1/artifacts/{request_id}/download",
            ),
        )
    finally:
        if not keep_workspace:
            runtime.artifacts.remove_workspace(request_id)
        await runtime.admission.release()


@router.post("/ocr/{task}", response_model=ApiEnvelope[RecognitionData])
async def recognize_image(
    task: OcrTask,
    request: Request,
    file: Annotated[UploadFile, File()],
) -> ApiEnvelope[RecognitionData]:
    runtime = _runtime(request)
    request_id, workspace = await _start_request(runtime)
    try:
        input_path = await save_bounded_upload(
            file,
            workspace,
            runtime.settings.max_upload_bytes,
        )
        command = RecognizeImageCommand(request_id, input_path, workspace, task)
        result = await asyncio.get_running_loop().run_in_executor(
            runtime.executor,
            RecognizeImage(runtime.pipeline).execute,
            command,
        )
        return ApiEnvelope(
            internal_code=InternalCode.SUCCESS,
            message="Image recognized successfully.",
            data=RecognitionData(
                request_id=result.request_id,
                task=result.task.value,
                content=result.content,
            ),
        )
    finally:
        runtime.artifacts.remove_workspace(request_id)
        await runtime.admission.release()


@router.get("/artifacts/{request_id}/download", response_class=FileResponse)
async def download_artifact(request_id: str, request: Request) -> FileResponse:
    artifact = _runtime(request).artifacts.artifact_path(request_id)
    return FileResponse(artifact, media_type="application/zip", filename=artifact.name)
