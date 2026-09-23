import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient
from pypdf import PdfWriter

from monkeyocr.domain.tasks import OcrTask
from monkeyocr.infrastructure.config.settings import ServiceSettings
from monkeyocr.interface.http.app import create_app
from monkeyocr.interface.http.auth import BearerTokenVerifier

TOKEN = "test-token-abcdefghijklmnopqrstuvwxyz"
AUTHORIZATION = {"Authorization": f"Bearer {TOKEN}"}


class FakePipeline:
    def __init__(self) -> None:
        self.closed = False

    def parse(self, input_path: Path, output_dir: Path) -> tuple[str, tuple[str, ...]]:
        markdown = output_dir / "document.md"
        markdown.write_text("parsed", encoding="utf-8")
        artifact = output_dir / "document_results.zip"
        with ZipFile(artifact, "w", ZIP_DEFLATED) as archive:
            archive.write(markdown, markdown.name)
        return artifact.name, (markdown.name, artifact.name)

    def parse_markdown(self, input_path: Path, output_dir: Path) -> str:
        assert input_path.parent == output_dir
        return "# Parsed text\n\nUseful paragraph"

    def recognize(self, input_path: Path, output_dir: Path, task: OcrTask) -> str:
        assert input_path.parent == output_dir
        return f"{task.value}:recognized"

    def close(self) -> None:
        self.closed = True


def _client(tmp_path: Path, *, max_upload_bytes: int = 1024, max_pdf_pages: int = 50) -> TestClient:
    settings = ServiceSettings(
        output_dir=tmp_path / "results",
        max_upload_bytes=max_upload_bytes,
        max_pdf_pages=max_pdf_pages,
        cleanup_interval_seconds=3600,
    )
    pipeline = FakePipeline()
    return TestClient(
        create_app(
            settings,
            pipeline_factory=lambda _settings: pipeline,
            token_verifier=BearerTokenVerifier(TOKEN),
        )
    )


def test_internal_health_does_not_require_bearer_token(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/internal/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_public_route_rejects_missing_token(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/parse",
            files={"file": ("document.png", b"image", "image/png")},
        )

    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")
    assert response.json() == {
        "internal_code": "AUTHENTICATION_FAILED",
        "message": "A valid Bearer token is required.",
        "data": None,
    }


def test_parse_returns_envelope_and_protected_zip(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/parse",
            headers=AUTHORIZATION,
            files={"file": ("document.png", b"image", "image/png")},
        )
        body = response.json()
        download = client.get(body["data"]["artifact_download_url"], headers=AUTHORIZATION)

    assert response.status_code == 200
    assert body["internal_code"] == "SUCCESS"
    assert body["data"]["request_id"]
    assert download.status_code == 200
    assert download.headers["content-type"] == "application/zip"
    assert download.content.startswith(b"PK")


def test_markdown_parse_returns_text_without_retained_artifacts(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/parse/markdown",
            headers=AUTHORIZATION,
            files={"file": ("document.png", b"image", "image/png")},
        )

    assert response.status_code == 200
    assert response.json() == {
        "internal_code": "SUCCESS",
        "message": "Document parsed successfully.",
        "data": {
            "request_id": response.json()["data"]["request_id"],
            "markdown": "# Parsed text\n\nUseful paragraph",
        },
    }
    assert list((tmp_path / "results").iterdir()) == []


def test_markdown_parse_requires_bearer_token(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/parse/markdown",
            files={"file": ("document.png", b"image", "image/png")},
        )

    assert response.status_code == 401


def test_markdown_parse_rejects_unsupported_media(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/parse/markdown",
            headers=AUTHORIZATION,
            files={"file": ("document.txt", b"text", "text/plain")},
        )

    assert response.status_code == 415
    assert response.json()["internal_code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_markdown_parse_enforces_upload_and_pdf_page_limits(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.add_blank_page(width=200, height=200)
    pdf = io.BytesIO()
    writer.write(pdf)

    with _client(tmp_path, max_upload_bytes=1024, max_pdf_pages=1) as client:
        too_large = client.post(
            "/api/v1/parse/markdown",
            headers=AUTHORIZATION,
            files={"file": ("document.pdf", b"x" * 1025, "application/pdf")},
        )
        too_many_pages = client.post(
            "/api/v1/parse/markdown",
            headers=AUTHORIZATION,
            files={"file": ("document.pdf", pdf.getvalue(), "application/pdf")},
        )

    assert too_large.status_code == 413
    assert too_large.json()["internal_code"] == "UPLOAD_TOO_LARGE"
    assert too_many_pages.status_code == 422
    assert too_many_pages.json()["internal_code"] == "PAGE_LIMIT_EXCEEDED"
    assert list((tmp_path / "results").iterdir()) == []


def test_recognition_endpoint_uses_task_enum(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/ocr/formula",
            headers=AUTHORIZATION,
            files={"file": ("formula.png", b"image", "image/png")},
        )

    assert response.status_code == 200
    assert response.json()["data"]["content"] == "formula:recognized"


def test_streamed_upload_limit_returns_413_envelope(tmp_path: Path) -> None:
    with _client(tmp_path, max_upload_bytes=4) as client:
        response = client.post(
            "/api/v1/parse",
            headers=AUTHORIZATION,
            files={"file": ("document.png", b"12345", "image/png")},
        )

    assert response.status_code == 413
    assert response.json()["internal_code"] == "UPLOAD_TOO_LARGE"


def test_unsupported_media_returns_415_envelope(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/parse",
            headers=AUTHORIZATION,
            files={"file": ("document.txt", b"text", "text/plain")},
        )

    assert response.status_code == 415
    assert response.json()["internal_code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_artifact_download_is_also_authenticated(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.get("/api/v1/artifacts/00000000-0000/download")

    assert response.status_code == 401
