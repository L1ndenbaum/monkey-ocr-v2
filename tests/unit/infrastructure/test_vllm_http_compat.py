from fastapi import APIRouter, FastAPI


def test_fastapi_routes_remain_compatible_with_vllm_metrics_middleware() -> None:
    app = FastAPI()
    router = APIRouter()

    @router.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(router)

    assert all(hasattr(route, "path") for route in app.routes)
