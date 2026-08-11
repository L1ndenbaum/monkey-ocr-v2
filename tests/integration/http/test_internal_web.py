import asyncio
from collections.abc import Callable
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi.testclient import TestClient

from monkeyocr.application.web_jobs import MarkdownResult, WebJobResult
from monkeyocr.infrastructure.config.web_settings import WebSettings
from monkeyocr.interface.http.internal_web.app import create_internal_web_app


class FakeGateway:
    def __init__(self) -> None:
        self.closed = False

    async def execute(
        self,
        mode: str,
        input_path: Path,
        workspace: Path,
        report_stage: Callable[[str], None],
    ) -> WebJobResult:
        assert input_path.is_file()
        report_stage("extracting")
        extracted = workspace / "extracted"
        extracted.mkdir()
        markdown = extracted / "result.md"
        json_file = extracted / "result.json"
        markdown.write_text(f"# {mode} result", encoding="utf-8")
        json_file.write_text("{}", encoding="utf-8")
        artifact = workspace / "result.zip"
        with ZipFile(artifact, "w", ZIP_DEFLATED) as archive:
            archive.write(markdown, markdown.name)
            archive.write(json_file, json_file.name)
        await asyncio.sleep(0)
        return WebJobResult(
            markdowns=(MarkdownResult(markdown.name, markdown.read_text()),),
            json_files=(json_file.name,),
            public_files=(json_file.name, markdown.name),
            artifact_name=artifact.name,
        )

    async def ready(self) -> bool:
        return True

    async def close(self) -> None:
        self.closed = True


def _app(tmp_path: Path):  # type: ignore[no-untyped-def]
    settings = WebSettings(
        result_dir=tmp_path / "jobs",
        static_dir=tmp_path / "missing-static",
        cookie_path="/",
        cookie_secure=False,
        cleanup_interval_seconds=3600,
    )
    gateway = FakeGateway()
    app = create_internal_web_app(
        settings,
        gateway_factory=lambda _settings, _store: gateway,
    )
    return app, gateway


def _wait_for_terminal(client: TestClient, job_id: str) -> dict[str, object]:
    for _ in range(100):
        response = client.get(f"/api/jobs/{job_id}")
        data = response.json()["data"]
        if data["status"] in {"succeeded", "failed"}:
            return data
    raise AssertionError("job did not finish")


def test_job_flow_is_session_scoped_and_downloadable(tmp_path: Path) -> None:
    app, gateway = _app(tmp_path)
    with TestClient(app) as client:
        submit = client.post(
            "/api/jobs",
            data={"mode": "parse"},
            files={"file": ("document.png", b"image", "image/png")},
        )
        assert submit.status_code == 202
        job_id = submit.json()["data"]["id"]
        terminal = _wait_for_terminal(client, job_id)
        result = client.get(f"/api/jobs/{job_id}/result")
        result_file = client.get(f"/api/jobs/{job_id}/files/result.json")
        artifact = client.get(f"/api/jobs/{job_id}/artifact")
        source = client.get(f"/api/jobs/{job_id}/source")

        assert terminal["status"] == "succeeded"
        assert result.json()["data"]["markdowns"][0]["content"] == "# parse result"
        assert result.json()["data"]["source_url"].startswith("api/jobs/")
        assert result_file.json() == {}
        assert result_file.headers["content-disposition"].startswith("inline")
        assert artifact.content.startswith(b"PK")
        assert source.content == b"image"

        client.cookies.set("monkeyocr_ui_session", "x" * 43, path="/")
        forbidden = client.get(f"/api/jobs/{job_id}")
        assert forbidden.status_code == 404

    assert gateway.closed


def test_upload_and_mode_validation_use_stable_envelopes(tmp_path: Path) -> None:
    app, _gateway = _app(tmp_path)
    with TestClient(app) as client:
        unsupported = client.post(
            "/api/jobs",
            data={"mode": "parse"},
            files={"file": ("document.txt", b"text", "text/plain")},
        )
        pdf_single_task = client.post(
            "/api/jobs",
            data={"mode": "formula"},
            files={"file": ("document.pdf", b"%PDF", "application/pdf")},
        )

    assert unsupported.status_code == 415
    assert unsupported.json()["internal_code"] == "UNSUPPORTED_MEDIA_TYPE"
    assert pdf_single_task.status_code == 415
    assert pdf_single_task.json()["internal_code"] == "UNSUPPORTED_MEDIA_TYPE"


def test_status_health_and_missing_assets(tmp_path: Path) -> None:
    app, _gateway = _app(tmp_path)
    with TestClient(app) as client:
        status = client.get("/api/status")
        health = client.get("/internal/health/ready")
        assets = client.get("/")
        unknown_api = client.get("/api/unknown")

    assert status.json()["data"] == {"available": True}
    assert health.status_code == 200
    assert "set-cookie" not in health.headers
    assert assets.status_code == 503
    assert unknown_api.status_code == 404
    assert "script-src 'self'" in assets.headers["content-security-policy"]


def test_hashed_assets_are_cacheable_but_index_is_not(tmp_path: Path) -> None:
    app, _gateway = _app(tmp_path)
    static_root = tmp_path / "missing-static"
    assets = static_root / "assets"
    assets.mkdir(parents=True)
    (static_root / "index.html").write_text("<html>workspace</html>", encoding="utf-8")
    (assets / "app-hash.js").write_text("export {}", encoding="utf-8")

    with TestClient(app) as client:
        index = client.get("/")
        script = client.get("/assets/app-hash.js")

    assert index.headers["cache-control"] == "no-store"
    assert script.headers["cache-control"] == "public, max-age=31536000, immutable"
