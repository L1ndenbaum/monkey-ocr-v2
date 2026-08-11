"""Internal React application BFF and static composition root."""

import asyncio
import re
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse

from monkeyocr.application.web_jobs import WebJobService
from monkeyocr.infrastructure.config.web_settings import WebSettings
from monkeyocr.infrastructure.http.ocr_api import OcrApiGateway
from monkeyocr.infrastructure.storage.web_jobs import WebJobWorkspaceStore
from monkeyocr.interface.http.errors import HANDLERS
from monkeyocr.interface.http.internal_web.routes import router
from monkeyocr.interface.http.internal_web.runtime import (
    InternalWebRuntime,
    ManagedWebGateway,
)

GatewayFactory = Callable[[WebSettings, WebJobWorkspaceStore], ManagedWebGateway]
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")


def _default_gateway_factory(
    settings: WebSettings,
    store: WebJobWorkspaceStore,
) -> ManagedWebGateway:
    return OcrApiGateway.from_token_file(
        base_url=settings.ocr_base_url,
        token_file=settings.token_file,
        workspace_store=store,
        timeout_seconds=settings.upstream_timeout_seconds,
    )


async def _cleanup_loop(runtime: InternalWebRuntime) -> None:
    while True:
        await asyncio.sleep(runtime.settings.cleanup_interval_seconds)
        removed = await asyncio.to_thread(
            runtime.store.cleanup_expired,
            exclude=runtime.jobs.active_job_ids(),
        )
        runtime.jobs.remove_expired(removed)


def create_internal_web_app(
    settings: WebSettings | None = None,
    *,
    gateway_factory: GatewayFactory | None = None,
) -> FastAPI:
    config = settings or WebSettings.from_env()
    store = WebJobWorkspaceStore(
        config.result_dir,
        ttl_seconds=config.result_ttl_seconds,
        max_extracted_bytes=config.max_extracted_bytes,
        max_archive_files=config.max_archive_files,
    )
    factory = gateway_factory or _default_gateway_factory

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        removed = await asyncio.to_thread(store.cleanup_expired)
        gateway = factory(config, store)
        jobs = WebJobService(
            gateway,
            queue_size=config.queue_size,
            workers=config.workers,
        )
        jobs.remove_expired(removed)
        await jobs.start()
        runtime = InternalWebRuntime(config, store, jobs, gateway)
        app.state.runtime = runtime
        cleanup = asyncio.create_task(
            _cleanup_loop(runtime),
            name="monkeyocr-web-cleanup",
        )
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            await jobs.close()
            await gateway.close()

    app = FastAPI(
        title="MonkeyOCR Internal Web",
        version="2.0.0",
        debug=config.debug,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    for exception_type, handler in HANDLERS.items():
        app.add_exception_handler(exception_type, handler)
    app.include_router(router)

    @app.middleware("http")
    async def session_and_security(request: Request, call_next):  # type: ignore[no-untyped-def]
        session_id = request.cookies.get(config.cookie_name)
        new_session = not session_id or not SESSION_PATTERN.fullmatch(session_id)
        if new_session:
            session_id = secrets.token_urlsafe(32)
        request.state.web_session_id = session_id
        response = await call_next(request)
        if request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; connect-src 'self'; "
            "img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; "
            "font-src 'self' data:; worker-src 'self' blob:; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if new_session and not request.url.path.startswith("/internal/"):
            response.set_cookie(
                config.cookie_name,
                session_id,
                max_age=config.result_ttl_seconds,
                secure=config.cookie_secure,
                httponly=True,
                samesite="strict",
                path=config.cookie_path,
            )
        return response

    @app.get("/internal/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/internal/health/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def spa(full_path: str) -> FileResponse | JSONResponse:
        if full_path == "api" or full_path.startswith(("api/", "internal/")):
            return JSONResponse(status_code=404, content={"message": "Not found."})
        static_root = config.static_dir.resolve()
        index = static_root / "index.html"
        requested = (static_root / full_path).resolve()
        if static_root in requested.parents and requested.is_file():
            return FileResponse(requested)
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(
            status_code=503,
            content={"message": "Internal Web assets are not installed."},
        )

    return app
