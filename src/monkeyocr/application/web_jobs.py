"""Ephemeral internal-Web OCR job orchestration."""

import asyncio
import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from monkeyocr.application.ports import InternalWebOcrGateway

logger = logging.getLogger(__name__)


class WebJobMode(StrEnum):
    PARSE = "parse"
    TEXT = "text"
    FORMULA = "formula"
    TABLE = "table"


class WebJobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    EXTRACTING = "extracting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class MarkdownResult:
    name: str
    content: str


@dataclass(frozen=True, slots=True)
class WebJobResult:
    markdowns: tuple[MarkdownResult, ...]
    json_files: tuple[str, ...]
    public_files: tuple[str, ...]
    artifact_name: str


@dataclass(frozen=True, slots=True)
class WebJobFailure:
    code: str
    message: str


@dataclass(slots=True)
class WebJob:
    id: str
    session_id: str
    mode: WebJobMode
    filename: str
    media_type: str
    workspace: Path
    input_path: Path
    status: WebJobStatus = WebJobStatus.QUEUED
    stage: str = "queued"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    completed_at: datetime | None = None
    result: WebJobResult | None = None
    failure: WebJobFailure | None = None


class WebJobNotFoundError(LookupError):
    """The job does not exist or belongs to another browser session."""


class WebJobConflictError(RuntimeError):
    """The requested operation conflicts with the current job state."""


class WebQueueFullError(RuntimeError):
    """The internal Web queue has reached its configured capacity."""


class WebUpstreamError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WebJobService:
    """Own an in-process queue and expose session-scoped job operations."""

    def __init__(
        self,
        gateway: "InternalWebOcrGateway",
        *,
        queue_size: int = 20,
        workers: int = 1,
    ) -> None:
        self._gateway = gateway
        self._queue: asyncio.Queue[str] = asyncio.Queue(maxsize=max(1, queue_size))
        self._worker_count = max(1, workers)
        self._jobs: dict[str, WebJob] = {}
        self._workers: list[asyncio.Task[None]] = []

    async def start(self) -> None:
        if self._workers:
            return
        self._workers = [
            asyncio.create_task(self._worker(), name=f"monkeyocr-web-worker-{index}")
            for index in range(self._worker_count)
        ]

    async def close(self) -> None:
        for worker in self._workers:
            worker.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    def submit(
        self,
        *,
        job_id: str,
        session_id: str,
        mode: WebJobMode,
        filename: str,
        media_type: str,
        workspace: Path,
        input_path: Path,
    ) -> WebJob:
        if self._queue.full():
            raise WebQueueFullError("The internal OCR queue is full; retry later.")
        job = WebJob(
            id=job_id,
            session_id=session_id,
            mode=mode,
            filename=filename,
            media_type=media_type,
            workspace=workspace,
            input_path=input_path,
        )
        self._jobs[job_id] = job
        self._queue.put_nowait(job_id)
        return job

    def list_for_session(self, session_id: str) -> tuple[WebJob, ...]:
        jobs: Iterable[WebJob] = (
            job for job in self._jobs.values() if job.session_id == session_id
        )
        return tuple(sorted(jobs, key=lambda item: item.created_at, reverse=True))

    def get(self, session_id: str, job_id: str) -> WebJob:
        job = self._jobs.get(job_id)
        if job is None or job.session_id != session_id:
            raise WebJobNotFoundError("OCR job was not found or has expired.")
        return job

    def delete(self, session_id: str, job_id: str) -> WebJob:
        job = self.get(session_id, job_id)
        if job.status in {WebJobStatus.PROCESSING, WebJobStatus.EXTRACTING}:
            raise WebJobConflictError("A running OCR job cannot be deleted.")
        del self._jobs[job_id]
        return job

    def remove_expired(self, expired_ids: Iterable[str]) -> None:
        for job_id in expired_ids:
            self._jobs.pop(job_id, None)

    def active_job_ids(self) -> frozenset[str]:
        return frozenset(
            job.id
            for job in self._jobs.values()
            if job.status
            in {
                WebJobStatus.QUEUED,
                WebJobStatus.PROCESSING,
                WebJobStatus.EXTRACTING,
            }
        )

    async def _worker(self) -> None:
        while True:
            job_id = await self._queue.get()
            try:
                job = self._jobs.get(job_id)
                if job is None:
                    continue
                job.status = WebJobStatus.PROCESSING
                job.stage = "processing"
                job.started_at = datetime.now(UTC)
                try:
                    result = await self._gateway.execute(
                        job.mode.value,
                        job.input_path,
                        job.workspace,
                        partial(self._report_stage, job),
                    )
                except WebUpstreamError as exc:
                    job.status = WebJobStatus.FAILED
                    job.stage = "failed"
                    job.failure = WebJobFailure(exc.code, str(exc))
                except Exception:
                    logger.exception("Internal Web OCR job failed", extra={"job_id": job.id})
                    job.status = WebJobStatus.FAILED
                    job.stage = "failed"
                    job.failure = WebJobFailure(
                        "INTERNAL_ERROR",
                        "The OCR job failed unexpectedly.",
                    )
                else:
                    job.status = WebJobStatus.SUCCEEDED
                    job.stage = "succeeded"
                    job.result = result
                finally:
                    job.completed_at = datetime.now(UTC)
            finally:
                self._queue.task_done()

    @staticmethod
    def _report_stage(job: WebJob, stage: str) -> None:
        job.stage = stage
        if stage == "extracting":
            job.status = WebJobStatus.EXTRACTING


def new_job_id() -> str:
    return str(uuid.uuid4())
