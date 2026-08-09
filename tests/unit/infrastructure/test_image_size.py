import runpy
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parents[3]
SCRIPT = runpy.run_path(REPO_ROOT / "scripts" / "check_image_size.py")


def image_repository(reference: str) -> str:
    return str(SCRIPT["image_repository"](reference))


def select_linux_amd64_digest(index: dict[str, Any]) -> str:
    return str(SCRIPT["select_linux_amd64_digest"](index))


def measure_manifest(manifest: dict[str, Any]) -> tuple[int, int, int]:
    result = SCRIPT["measure_manifest"](manifest)
    return tuple(result)


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        ("ghcr.io/acme/ocr:api-standard", "ghcr.io/acme/ocr"),
        ("ghcr.io/acme/ocr@sha256:index", "ghcr.io/acme/ocr"),
        ("registry.example:5000/acme/ocr:v1", "registry.example:5000/acme/ocr"),
    ],
)
def test_image_repository(reference: str, expected: str) -> None:
    assert image_repository(reference) == expected


def test_select_linux_amd64_digest_ignores_attestation_manifest() -> None:
    index = {
        "manifests": [
            {
                "digest": "sha256:image",
                "platform": {"os": "linux", "architecture": "amd64"},
            },
            {
                "digest": "sha256:attestation",
                "platform": {"os": "unknown", "architecture": "unknown"},
            },
        ]
    }

    assert select_linux_amd64_digest(index) == "sha256:image"


def test_select_linux_amd64_digest_rejects_ambiguous_index() -> None:
    with pytest.raises(ValueError, match="found 0"):
        select_linux_amd64_digest({"manifests": []})


def test_measure_manifest_uses_compressed_layer_sizes() -> None:
    assert measure_manifest(
        {
            "layers": [
                {"digest": "sha256:a", "size": 10},
                {"digest": "sha256:b", "size": 25},
                {"digest": "sha256:c", "size": 5},
            ]
        }
    ) == (
        40,
        25,
        3,
    )


def test_measure_manifest_excludes_upstream_layers_only_from_layer_budget() -> None:
    result = SCRIPT["measure_manifest"](
        {
            "layers": [
                {"digest": "sha256:base", "size": 100},
                {"digest": "sha256:managed", "size": 25},
            ]
        },
        {"sha256:base"},
    )

    assert tuple(result) == (125, 25, 2)


def test_inspect_raw_surfaces_docker_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    reference = "ghcr.io/Owner/image@sha256:digest"

    def run(*args: Any, **_kwargs: Any) -> None:
        raise subprocess.CalledProcessError(
            returncode=1,
            cmd=args[0],
            stderr="ERROR: repository name must be lowercase",
        )

    monkeypatch.setattr(SCRIPT["subprocess"], "run", run)

    with pytest.raises(RuntimeError, match="repository name must be lowercase"):
        SCRIPT["inspect_raw"](reference)


def test_publish_workflow_uses_metadata_normalized_image_tag() -> None:
    workflow = (REPO_ROOT / ".github/workflows/publish-image.yml").read_text(encoding="utf-8")

    assert "IMAGE_REFERENCE: ${{ fromJSON(steps.metadata.outputs.json).tags[0] }}@" in workflow
    assert "IMAGE_REFERENCE: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}@" not in workflow
