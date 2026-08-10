from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPO_ROOT / "infrastructure" / "docker" / "compose.yml"


def test_read_only_services_have_a_writable_runtime_home() -> None:
    compose = COMPOSE_FILE.read_text()

    assert "read_only: true" in compose
    assert "HOME: /home/monkeyocr" in compose
    assert (
        "/home/monkeyocr:rw,nosuid,nodev,size=2g,"
        "uid=${MONKEYOCR_UID:-1000},gid=${MONKEYOCR_GID:-1000},mode=0700"
    ) in compose
