import runpy
from pathlib import Path
from typing import Any

import pytest

SCRIPT = runpy.run_path(
    Path(__file__).parents[3] / "scripts" / "check_image_size.py",
)


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
    assert measure_manifest({"layers": [{"size": 10}, {"size": 25}, {"size": 5}]}) == (
        40,
        25,
        3,
    )
