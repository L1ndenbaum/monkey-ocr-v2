"""FastAPI application factory and service lifecycle."""

import asyncio
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from monkeyocr.infrastructure.config.settings import ServiceSettings
from monkeyocr.infrastructure.storage.artifacts import ArtifactStore
from monkeyocr.interface.http.admission import RequestAdmission
from monkeyocr.interface.http.auth import (
    BearerAuthMiddleware,
    BearerTokenVerifier,
)
from monkeyocr.interface.http.errors import HANDLERS
from monkeyocr.interface.http.runtime import HttpRuntime, ManagedOcrPipeline
from monkeyocr.interface.http.v1.routes import router as v1_router

PipelineFactory = Callable[[ServiceSettings], ManagedOcrPipeline]


def _default_pipeline_factory(settings: ServiceSettings) -> ManagedOcrPipeline:
    from monkeyocr.infrastructure.pipeline.adapter import MonkeyOcrPipelineAdapter
    from monkeyocr.infrastructure.pipeline.config import BackendConfig

    return MonkeyOcrPipelineAdapter(
        BackendConfig(
            model_path=str(settings.model_path),
            server_url=settings.server_url,
            served_model_name=settings.served_model_name,
            tp=settings.tensor_parallel_size,
            max_pixels=settings.max_pixels,
            request_timeout=settings.request_timeout_seconds,
            http_max_retries=settings.http_max_retries,
            http_retry_backoff=settings.http_retry_backoff,
            server_max_inflight=settings.server_max_inflight,
            preprocess_batch_size=settings.preprocess_batch_size,
            skip_preprocess=settings.skip_preprocess,
        ),
        page_max_inflight=settings.page_max_inflight,
        preprocess_wait_seconds=settings.preprocess_wait_seconds,
        end2end=settings.end2end,
        retry_repeat=settings.retry_repeat,
        keep_header_footer=settings.keep_header_footer,
        use_base64=settings.use_base64,
        debug=settings.debug,
    )


async def _cleanup_loop(store: ArtifactStore, interval: int) -> None:
    while True:
        await asyncio.sleep(interval)
        await asyncio.to_thread(store.cleanup_expired)


def create_app(
    settings: ServiceSettings | None = None,
    *,
    pipeline_factory: PipelineFactory | None = None,
    token_verifier: BearerTokenVerifier | None = None,
) -> FastAPI:
    config = settings or ServiceSettings.from_env()
    verifier = token_verifier or BearerTokenVerifier.from_file(config.token_file)
    factory = pipeline_factory or _default_pipeline_factory
    artifacts = ArtifactStore(config.output_dir, config.result_ttl_seconds)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        pipeline = await asyncio.to_thread(factory, config)
        executor = ThreadPoolExecutor(
            max_workers=config.max_concurrency,
            thread_name_prefix="monkeyocr-api",
        )
        app.state.runtime = HttpRuntime(
            settings=config,
            artifacts=artifacts,
            admission=RequestAdmission(config.max_concurrency),
            executor=executor,
            pipeline=pipeline,
        )
        cleanup = asyncio.create_task(
            _cleanup_loop(artifacts, config.cleanup_interval_seconds),
            name="monkeyocr-artifact-cleanup",
        )
        try:
            yield
        finally:
            cleanup.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup
            executor.shutdown(wait=True, cancel_futures=True)
            await asyncio.to_thread(pipeline.close)

    app = FastAPI(
        title="MonkeyOCRv2 API",
        version="2.0.0",
        debug=config.debug,
        docs_url="/docs" if config.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if config.docs_enabled else None,
        lifespan=lifespan,
    )
    app.add_middleware(BearerAuthMiddleware, verifier=verifier)
    for exception_type, handler in HANDLERS.items():
        app.add_exception_handler(exception_type, handler)
    app.include_router(v1_router)

    @app.get("/internal/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/internal/health/ready", include_in_schema=False)
    async def ready() -> dict[str, str]:
        return {"status": "ready"}

    return app
