from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "infrastructure" / "docker" / "compose.yml"


def test_vllm_has_an_executable_runtime_cache_on_a_read_only_root() -> None:
    compose = COMPOSE_FILE.read_text()

    assert "read_only: true" in compose
    assert "HOME: /home/monkeyocr" in compose
    assert "USER: monkeyocr" in compose
    assert "TMPDIR: /home/monkeyocr" in compose
    assert "TORCHINDUCTOR_CACHE_DIR: /home/monkeyocr/.cache/torch_inductor" in compose
    assert (
        "/home/monkeyocr:rw,exec,nosuid,nodev,size=2g,"
        "uid=${MONKEYOCR_UID:-1000},gid=${MONKEYOCR_GID:-1000},mode=0700"
    ) in compose


def test_api_host_binding_is_private_and_configurable() -> None:
    compose = COMPOSE_FILE.read_text()

    assert "host_ip: ${MONKEYOCR_API_BIND_ADDRESS:-127.0.0.1}" in compose
    assert 'published: "${MONKEYOCR_API_HOST_PORT:-8000}"' in compose
    assert "target: 8000" in compose


def test_vllm_is_not_published_on_the_host() -> None:
    compose = COMPOSE_FILE.read_text()
    vllm_service = compose.split("  api:\n", maxsplit=1)[0]

    assert 'expose:\n      - "8888"' in vllm_service
    assert "ports:" not in vllm_service
