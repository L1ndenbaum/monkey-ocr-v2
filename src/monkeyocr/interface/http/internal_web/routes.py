"""Session-scoped HTTP routes for the internal React application."""

import asyncio
import mimetypes
from pathlib import Path
from typing import Annotated, cast

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from monkeyocr.application.web_jobs import (
    WebJob,
    WebJobConflictError,
    WebJobMode,
    WebJobNotFoundError,
    WebJobStatus,
    WebQueueFullError,
    new_job_id,
)
from monkeyocr.domain.media import classify_media
from monkeyocr.infrastructure.storage.web_jobs import WebJobWorkspaceStore
from monkeyocr.interface.http.internal_web.runtime import InternalWebRuntime
from monkeyocr.interface.http.internal_web.schemas import (
    BackendStatusData,
    JobFailureData,
    JobResultData,
    JobSummaryData,
    MarkdownData,
)
from monkeyocr.interface.http.schemas import ApiEnvelope, InternalCode
from monkeyocr.interface.http.uploads import (
    safe_upload_name,
    save_bounded_upload,
    validate_pdf_page_limit,
)

router = APIRouter(prefix="/api")


def _runtime(request: Request) -> InternalWebRuntime:
    return cast(InternalWebRuntime, request.app.state.runtime)


def _session(request: Request) -> str:
    return cast(str, request.state.web_session_id)


def _summary(job: WebJob) -> JobSummaryData:
    failure = None
    if job.failure is not None:
        failure = JobFailureData(code=job.failure.code, message=job.failure.message)
    return JobSummaryData(
        id=job.id,
        mode=job.mode.value,
        filename=job.filename,
        media_type=job.media_type,
        status=job.status.value,
        stage=job.stage,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        failure=failure,
    )


def _error(status: int, code: InternalCode, message: str) -> JSONResponse:
    envelope = ApiEnvelope[None](internal_code=code, message=message, data=None)
    headers = {"Retry-After": "1"} if status == 429 else None
    return JSONResponse(
        status_code=status,
        content=envelope.model_dump(mode="json"),
        headers=headers,
    )


@router.post("/jobs", response_model=ApiEnvelope[JobSummaryData], status_code=202)
async def submit_job(
    request: Request,
    file: Annotated[UploadFile, File()],
    mode: Annotated[WebJobMode, Form()] = WebJobMode.PARSE,
) -> ApiEnvelope[JobSummaryData] | JSONResponse:
    runtime = _runtime(request)
    job_id = new_job_id()
    workspace = await asyncio.to_thread(runtime.store.create, job_id)
    try:
        input_path = await save_bounded_upload(
            file,
            workspace,
            runtime.settings.max_upload_bytes,
        )
        classify_media(input_path.suffix, images_only=mode is not WebJobMode.PARSE)
        await validate_pdf_page_limit(input_path, runtime.settings.max_pdf_pages)
        try:
            job = runtime.jobs.submit(
                job_id=job_id,
                session_id=_session(request),
                mode=mode,
                filename=safe_upload_name(file.filename),
                media_type=file.content_type or _media_type(input_path),
                workspace=workspace,
                input_path=input_path,
            )
        except WebQueueFullError as exc:
            await asyncio.to_thread(runtime.store.remove, job_id)
            return _error(429, InternalCode.CAPACITY_EXCEEDED, str(exc))
    except Exception:
        await asyncio.to_thread(runtime.store.remove, job_id)
        raise
    return ApiEnvelope(
        internal_code=InternalCode.SUCCESS,
        message="OCR job queued.",
        data=_summary(job),
    )


@router.get("/jobs", response_model=ApiEnvelope[tuple[JobSummaryData, ...]])
async def list_jobs(request: Request) -> ApiEnvelope[tuple[JobSummaryData, ...]]:
    jobs = tuple(
        _summary(job) for job in _runtime(request).jobs.list_for_session(_session(request))
    )
    return ApiEnvelope(
        internal_code=InternalCode.SUCCESS,
        message="OCR jobs loaded.",
        data=jobs,
    )


@router.get("/jobs/{job_id}", response_model=ApiEnvelope[JobSummaryData])
async def get_job(request: Request, job_id: str) -> ApiEnvelope[JobSummaryData] | JSONResponse:
    try:
        job = _runtime(request).jobs.get(_session(request), job_id)
    except WebJobNotFoundError as exc:
        return _error(404, InternalCode.ARTIFACT_NOT_FOUND, str(exc))
    return ApiEnvelope(
        internal_code=InternalCode.SUCCESS,
        message="OCR job loaded.",
        data=_summary(job),
    )


@router.get("/jobs/{job_id}/result", response_model=ApiEnvelope[JobResultData])
async def get_result(
    request: Request,
    job_id: str,
) -> ApiEnvelope[JobResultData] | JSONResponse:
    try:
        job = _runtime(request).jobs.get(_session(request), job_id)
    except WebJobNotFoundError as exc:
        return _error(404, InternalCode.ARTIFACT_NOT_FOUND, str(exc))
    if job.status is not WebJobStatus.SUCCEEDED or job.result is None:
        return _error(409, InternalCode.INVALID_REQUEST, "OCR result is not ready.")
    result = JobResultData(
        id=job.id,
        markdowns=tuple(
            MarkdownData(name=item.name, content=item.content) for item in job.result.markdowns
        ),
        json_files=job.result.json_files,
        public_files=job.result.public_files,
        source_url=f"api/jobs/{job.id}/source",
        artifact_url=f"api/jobs/{job.id}/artifact",
    )
    return ApiEnvelope(
        internal_code=InternalCode.SUCCESS,
        message="OCR result loaded.",
        data=result,
    )


@router.get(
    "/jobs/{job_id}/source",
    response_class=FileResponse,
    response_model=None,
)
async def get_source(request: Request, job_id: str) -> FileResponse | JSONResponse:
    try:
        job = _runtime(request).jobs.get(_session(request), job_id)
    except WebJobNotFoundError as exc:
        return _error(404, InternalCode.ARTIFACT_NOT_FOUND, str(exc))
    return FileResponse(
        job.input_path,
        media_type=job.media_type,
        filename=job.filename,
        content_disposition_type="inline",
    )


@router.get(
    "/jobs/{job_id}/files/{relative_path:path}",
    response_class=FileResponse,
    response_model=None,
)
async def get_result_file(
    request: Request,
    job_id: str,
    relative_path: str,
) -> FileResponse | JSONResponse:
    runtime = _runtime(request)
    try:
        job = runtime.jobs.get(_session(request), job_id)
    except WebJobNotFoundError as exc:
        return _error(404, InternalCode.ARTIFACT_NOT_FOUND, str(exc))
    if job.result is None:
        return _error(409, InternalCode.INVALID_REQUEST, "OCR result is not ready.")
    try:
        path = WebJobWorkspaceStore.resolve_manifest_file(
            job.workspace,
            relative_path,
            job.result.public_files,
        )
    except FileNotFoundError:
        return _error(404, InternalCode.ARTIFACT_NOT_FOUND, "Result file was not found.")
    return FileResponse(
        path,
        media_type=_media_type(path),
        filename=path.name,
        content_disposition_type="inline",
    )


@router.get(
    "/jobs/{job_id}/artifact",
    response_class=FileResponse,
    response_model=None,
)
async def get_artifact(request: Request, job_id: str) -> FileResponse | JSONResponse:
    try:
        job = _runtime(request).jobs.get(_session(request), job_id)
    except WebJobNotFoundError as exc:
        return _error(404, InternalCode.ARTIFACT_NOT_FOUND, str(exc))
    if job.result is None:
        return _error(409, InternalCode.INVALID_REQUEST, "OCR result is not ready.")
    path = job.workspace / job.result.artifact_name
    if not path.is_file():
        return _error(404, InternalCode.ARTIFACT_NOT_FOUND, "Result artifact was not found.")
    filename = f"{Path(job.filename).stem}_results.zip"
    return FileResponse(path, media_type="application/zip", filename=filename)


@router.delete("/jobs/{job_id}", response_model=ApiEnvelope[None])
async def delete_job(request: Request, job_id: str) -> ApiEnvelope[None] | JSONResponse:
    runtime = _runtime(request)
    try:
        runtime.jobs.delete(_session(request), job_id)
    except WebJobNotFoundError as exc:
        return _error(404, InternalCode.ARTIFACT_NOT_FOUND, str(exc))
    except WebJobConflictError as exc:
        return _error(409, InternalCode.INVALID_REQUEST, str(exc))
    await asyncio.to_thread(runtime.store.remove, job_id)
    return ApiEnvelope(
        internal_code=InternalCode.SUCCESS,
        message="OCR job deleted.",
        data=None,
    )


@router.get("/status", response_model=ApiEnvelope[BackendStatusData])
async def backend_status(request: Request) -> ApiEnvelope[BackendStatusData]:
    available = await _runtime(request).gateway.ready()
    return ApiEnvelope(
        internal_code=InternalCode.SUCCESS,
        message="OCR backend status loaded.",
        data=BackendStatusData(available=available),
    )


def _media_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "application/octet-stream"
