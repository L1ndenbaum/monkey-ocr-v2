import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import httpx
import pytest

from monkeyocr.application.web_jobs import WebUpstreamError
from monkeyocr.infrastructure.http.ocr_api import OcrApiGateway
from monkeyocr.infrastructure.storage.web_jobs import WebJobWorkspaceStore

TOKEN = "test-token-abcdefghijklmnopqrstuvwxyz"


def _store(tmp_path: Path) -> WebJobWorkspaceStore:
    return WebJobWorkspaceStore(
        tmp_path / "jobs",
        ttl_seconds=60,
        max_extracted_bytes=1024 * 1024,
        max_archive_files=100,
    )


def _archive() -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED) as archive:
        archive.writestr("markdowns/document.md", "# parsed")
        archive.writestr("all_results.json", "[]")
    return buffer.getvalue()


@pytest.mark.asyncio
async def test_parse_injects_bearer_and_materializes_result(tmp_path: Path) -> None:
    seen_authorization: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers["authorization"])
        if request.url.path == "/api/v1/parse":
            return httpx.Response(
                200,
                json={
                    "internal_code": "SUCCESS",
                    "message": "ok",
                    "data": {
                        "request_id": "request",
                        "files": [],
                        "artifact_download_url": "/api/v1/artifacts/request/download",
                    },
                },
            )
        return httpx.Response(200, content=_archive(), headers={"content-type": "application/zip"})

    store = _store(tmp_path)
    workspace = store.create("00000000-0000-0000-0000-000000000001")
    source = workspace / "document.png"
    source.write_bytes(b"image")
    gateway = OcrApiGateway(
        base_url="http://ocr.test",
        token=TOKEN,
        workspace_store=store,
        timeout_seconds=60,
        transport=httpx.MockTransport(handler),
    )
    stages: list[str] = []

    try:
        result = await gateway.execute("parse", source, workspace, stages.append)
    finally:
        await gateway.close()

    assert seen_authorization == [f"Bearer {TOKEN}", f"Bearer {TOKEN}"]
    assert stages == ["extracting"]
    assert result.markdowns[0].content == "# parsed"
    assert result.json_files == ("all_results.json",)
    assert TOKEN not in json.dumps(result.public_files)


@pytest.mark.asyncio
async def test_upstream_error_is_preserved_without_response_details(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "internal_code": "CAPACITY_EXCEEDED",
                "message": "OCR capacity is full; retry later.",
                "data": None,
            },
        )

    store = _store(tmp_path)
    workspace = store.create("00000000-0000-0000-0000-000000000001")
    source = workspace / "document.png"
    source.write_bytes(b"image")
    gateway = OcrApiGateway(
        base_url="http://ocr.test",
        token=TOKEN,
        workspace_store=store,
        timeout_seconds=60,
        transport=httpx.MockTransport(handler),
    )

    try:
        with pytest.raises(WebUpstreamError) as caught:
            await gateway.execute("text", source, workspace, lambda _stage: None)
    finally:
        await gateway.close()

    assert caught.value.code == "CAPACITY_EXCEEDED"
    assert str(caught.value) == "OCR capacity is full; retry later."


@pytest.mark.asyncio
async def test_parse_caps_artifact_download_size(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v1/parse":
            return httpx.Response(
                200,
                json={
                    "internal_code": "SUCCESS",
                    "message": "ok",
                    "data": {"artifact_download_url": "/api/v1/artifacts/request/download"},
                },
            )
        return httpx.Response(200, content=b"x" * 32)

    store = WebJobWorkspaceStore(
        tmp_path / "jobs",
        ttl_seconds=60,
        max_extracted_bytes=16,
        max_archive_files=100,
    )
    workspace = store.create("00000000-0000-0000-0000-000000000001")
    source = workspace / "document.png"
    source.write_bytes(b"image")
    gateway = OcrApiGateway(
        base_url="http://ocr.test",
        token=TOKEN,
        workspace_store=store,
        timeout_seconds=60,
        transport=httpx.MockTransport(handler),
    )

    try:
        with pytest.raises(WebUpstreamError, match="download limit"):
            await gateway.execute("parse", source, workspace, lambda _stage: None)
    finally:
        await gateway.close()

    assert not (workspace / "result.zip").exists()
