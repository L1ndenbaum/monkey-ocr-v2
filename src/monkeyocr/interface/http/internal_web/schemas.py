"""Transport schemas for internal Web jobs."""

from datetime import datetime

from pydantic import BaseModel


class JobFailureData(BaseModel):
    code: str
    message: str


class JobSummaryData(BaseModel):
    id: str
    mode: str
    filename: str
    media_type: str
    status: str
    stage: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    failure: JobFailureData | None


class MarkdownData(BaseModel):
    name: str
    content: str


class JobResultData(BaseModel):
    id: str
    markdowns: tuple[MarkdownData, ...]
    json_files: tuple[str, ...]
    public_files: tuple[str, ...]
    source_url: str
    artifact_url: str


class BackendStatusData(BaseModel):
    available: bool
