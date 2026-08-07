from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient

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

    def recognize(self, input_path: Path, output_dir: Path, task: OcrTask) -> str:
        assert input_path.parent == output_dir
        return f"{task.value}:recognized"

    def close(self) -> None:
        self.closed = True


def _client(tmp_path: Path, *, max_upload_bytes: int = 1024) -> TestClient:
    settings = ServiceSettings(
        output_dir=tmp_path / "results",
        max_upload_bytes=max_upload_bytes,
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
