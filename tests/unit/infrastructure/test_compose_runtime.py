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
