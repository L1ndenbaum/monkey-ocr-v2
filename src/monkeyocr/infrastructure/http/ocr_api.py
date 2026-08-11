"""Authenticated HTTP adapter for the existing MonkeyOCR API."""

import json
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NoReturn, cast

import httpx

from monkeyocr.application.web_jobs import (
    MarkdownResult,
    WebJobResult,
    WebUpstreamError,
)
from monkeyocr.infrastructure.storage.web_jobs import WebJobWorkspaceStore
from monkeyocr.interface.http.auth import read_bearer_token


class OcrApiGateway:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        workspace_store: WebJobWorkspaceStore,
        timeout_seconds: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        timeout = httpx.Timeout(
            connect=10.0,
            read=float(timeout_seconds),
            write=60.0,
            pool=10.0,
        )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {token}"},
        )
        self._store = workspace_store

    @classmethod
    def from_token_file(
        cls,
        *,
        base_url: str,
        token_file: Path,
        workspace_store: WebJobWorkspaceStore,
        timeout_seconds: int,
    ) -> "OcrApiGateway":
        return cls(
            base_url=base_url,
            token=read_bearer_token(token_file),
            workspace_store=workspace_store,
            timeout_seconds=timeout_seconds,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def ready(self) -> bool:
        try:
            response = await self._client.get("/internal/health/ready", timeout=5.0)
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def execute(
        self,
        mode: str,
        input_path: Path,
        workspace: Path,
        report_stage: Callable[[str], None],
    ) -> WebJobResult:
        try:
            if mode == "parse":
                return await self._parse(input_path, workspace, report_stage)
            return await self._recognize(mode, input_path, workspace)
        except httpx.HTTPError as exc:
            raise WebUpstreamError(
                "BACKEND_UNAVAILABLE",
                "OCR backend is unavailable; retry later.",
            ) from exc

    async def _parse(
        self,
        input_path: Path,
        workspace: Path,
        report_stage: Callable[[str], None],
    ) -> WebJobResult:
        with input_path.open("rb") as stream:
            response = await self._client.post(
                "/api/v1/parse",
                files={"file": (input_path.name, stream, _media_type(input_path))},
            )
        payload = _success_payload(response)
        artifact_url = payload.get("artifact_download_url")
        if not isinstance(artifact_url, str) or not artifact_url.startswith("/api/v1/"):
            raise WebUpstreamError("INVALID_ARTIFACT", "OCR response has no artifact URL.")

        archive_path = workspace / "result.zip"
        async with self._client.stream("GET", artifact_url) as download:
            if download.status_code != 200:
                _raise_upstream(download.status_code, None)
            downloaded_bytes = 0
            with archive_path.open("xb") as output:
                async for chunk in download.aiter_bytes():
                    downloaded_bytes += len(chunk)
                    if downloaded_bytes > self._store.max_extracted_bytes:
                        archive_path.unlink(missing_ok=True)
                        raise WebUpstreamError(
                            "INVALID_ARTIFACT",
                            "OCR artifact exceeds the download limit.",
                        )
                    output.write(chunk)

        report_stage("extracting")
        public_files = self._store.safe_extract(archive_path, workspace / "extracted")
        markdowns = _load_markdowns(workspace / "extracted", public_files)
        json_files = tuple(path for path in public_files if path.endswith(".json"))
        return WebJobResult(markdowns, json_files, public_files, archive_path.name)

    async def _recognize(
        self,
        mode: str,
        input_path: Path,
        workspace: Path,
    ) -> WebJobResult:
        with input_path.open("rb") as stream:
            response = await self._client.post(
                f"/api/v1/ocr/{mode}",
                files={"file": (input_path.name, stream, _media_type(input_path))},
            )
        payload = _success_payload(response)
        content = payload.get("content")
        if not isinstance(content, str):
            raise WebUpstreamError("INVALID_RESPONSE", "OCR response has no content.")

        extracted = workspace / "extracted"
        extracted.mkdir(mode=0o700)
        markdown_path = extracted / "result.md"
        json_path = extracted / "result.json"
        markdown_path.write_text(content, encoding="utf-8")
        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        archive_path = workspace / "result.zip"
        with zipfile.ZipFile(archive_path, "x", zipfile.ZIP_DEFLATED) as archive:
            archive.write(markdown_path, markdown_path.name)
            archive.write(json_path, json_path.name)
        return WebJobResult(
            markdowns=(MarkdownResult(markdown_path.name, content),),
            json_files=(json_path.name,),
            public_files=(json_path.name, markdown_path.name),
            artifact_name=archive_path.name,
        )


def _success_payload(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        _raise_upstream(response.status_code, None)
    if response.status_code != 200 or not isinstance(body, dict):
        _raise_upstream(response.status_code, body if isinstance(body, dict) else None)
    data = body.get("data")
    if body.get("internal_code") != "SUCCESS" or not isinstance(data, dict):
        _raise_upstream(response.status_code, body)
    return cast(dict[str, Any], data)


def _raise_upstream(status: int, body: dict[str, Any] | None) -> NoReturn:
    code = str((body or {}).get("internal_code") or "BACKEND_UNAVAILABLE")
    message = str((body or {}).get("message") or "OCR backend request failed.")
    if status >= 500:
        code = "BACKEND_UNAVAILABLE"
        message = "OCR backend is unavailable; retry later."
    raise WebUpstreamError(code, message)


def _load_markdowns(root: Path, manifest: tuple[str, ...]) -> tuple[MarkdownResult, ...]:
    results: list[MarkdownResult] = []
    for relative in manifest:
        if relative.endswith(".md"):
            path = root / relative
            results.append(MarkdownResult(relative, path.read_text(encoding="utf-8")))
    return tuple(results)


def _media_type(path: Path) -> str:
    return {
        ".gif": "image/gif",
        ".jpeg": "image/jpeg",
        ".jpg": "image/jpeg",
        ".pdf": "application/pdf",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix.lower(), "application/octet-stream")
