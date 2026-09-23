#!/usr/bin/env python3
"""Select production image targets from the pushed source changes."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

TARGETS = ("api", "vllm")

# Keep build metadata in one place; the workflow consumes these rows unchanged.
MATRIX_ROWS: dict[str, dict[str, Any]] = {
    "api": {
        "target": "api",
        "tag_prefix": "api-standard",
        "title": "MonkeyOCRv2 authenticated API",
        "description": "Authenticated MonkeyOCRv2 API and preprocessing runtime",
        "cache_primary": "monkeyocr-api-standard",
        "cache_secondary": "monkeyocr-vllm-standard",
        "max_total_gib": 8,
        "base_reference": (
            "nvidia/cuda:12.8.1-runtime-ubuntu22.04@sha256:"
            "fcbbd60a5ad3db3a1c7375bf14546b369b54064c513224310b2026df50c7a9bd"
        ),
        "legacy_standard": False,
    },
    "vllm": {
        "target": "vllm",
        "tag_prefix": "vllm-standard",
        "title": "MonkeyOCRv2 vLLM runtime",
        "description": "MonkeyOCRv2 Standard vLLM inference runtime",
        "cache_primary": "monkeyocr-vllm-standard",
        "cache_secondary": "monkeyocr-api-standard",
        "max_total_gib": 12,
        "base_reference": (
            "nvidia/cuda:12.8.1-devel-ubuntu22.04@sha256:"
            "6617a625f4090c76c545a0e7d63f2e441718ef9af7f4efe7dd1242a29e289fd7"
        ),
        "legacy_standard": True,
    },
}

SHARED_BUILD_INPUTS = {
    ".dockerignore",
    ".github/workflows/publish-image.yml",
    "infrastructure/docker/Dockerfile",
    "LICENSE",
    "pyproject.toml",
    "scripts/check_image_size.py",
    "scripts/select_image_targets.py",
    "uv.lock",
}

# The Web image has its own workflow. Research and demo code is excluded from
# the standard Docker build context; README changes alone do not affect runtime.
IGNORED_FILES = {
    "README.md",
    "src/monkeyocr/application/web_jobs.py",
    "src/monkeyocr/infrastructure/config/web_settings.py",
    "src/monkeyocr/infrastructure/storage/web_jobs.py",
    "src/monkeyocr/interface/cli/web.py",
    "src/monkeyocr/interface/cli/model.py",
    "src/monkeyocr/interface/cli/understanding.py",
    "src/monkeyocr/interface/cli/vision.py",
    "src/monkeyocr/interface/cli/vision_vitae.py",
}
IGNORED_PREFIXES = (
    "src/monkeyocr/infrastructure/http/",
    "src/monkeyocr/infrastructure/training/",
    "src/monkeyocr/interface/demo/",
    "src/monkeyocr/interface/http/internal_web/",
)

VLLM_FILES = {
    "src/monkeyocr/interface/cli/vllm.py",
    "src/monkeyocr/infrastructure/modeling/monkeyocr_vllm.py",
    "src/monkeyocr/infrastructure/modeling/monkeyocr_dflash_vllm.py",
    "src/monkeyocr/infrastructure/modeling/vllm_compat.py",
}

API_FILES = {
    "src/monkeyocr/interface/cli/api.py",
    "src/monkeyocr/interface/cli/parse.py",
    "src/monkeyocr/infrastructure/config/settings.py",
    "src/monkeyocr/infrastructure/modeling/preprocessor.py",
    "src/monkeyocr/infrastructure/storage/artifacts.py",
}
API_PREFIXES = (
    "src/monkeyocr/application/",
    "src/monkeyocr/domain/",
    "src/monkeyocr/infrastructure/modeling/preprocessing/",
    "src/monkeyocr/infrastructure/pipeline/",
    "src/monkeyocr/interface/http/",
)

COMMIT_SHA = re.compile(r"[0-9a-fA-F]{40,64}\Z")


def targets_for_path(path: str) -> set[str]:
    """Return runtime consumers; unknown source is shared to avoid missed images."""
    if path in SHARED_BUILD_INPUTS:
        return set(TARGETS)
    if path in IGNORED_FILES or path.startswith(IGNORED_PREFIXES):
        return set()
    if path in VLLM_FILES:
        return {"vllm"}
    if path in API_FILES or path.startswith(API_PREFIXES):
        return {"api"}
    if path.startswith("src/"):
        return set(TARGETS)
    return set()


def changed_paths(before: str, after: str) -> tuple[str, ...] | None:
    """Return a complete two-commit diff, or None when it cannot be trusted."""
    if not COMMIT_SHA.fullmatch(before) or not COMMIT_SHA.fullmatch(after) or set(before) == {"0"}:
        return None
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--no-renames", "-z", before, after],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return tuple(os.fsdecode(path) for path in result.stdout.split(b"\0") if path)


def select_targets(
    event_name: str,
    ref: str,
    requested_target: str,
    paths: Iterable[str] | None,
) -> tuple[str, ...]:
    if ref.startswith("refs/tags/v"):
        return TARGETS
    if event_name == "workflow_dispatch":
        if requested_target == "all":
            return TARGETS
        if requested_target in TARGETS:
            return (requested_target,)
        raise ValueError(f"Unsupported manual image target: {requested_target!r}")
    if event_name != "push" or ref not in {"refs/heads/main", "refs/heads/dev"}:
        raise ValueError(f"Unsupported image publish event: {event_name} {ref}")
    if paths is None:
        return TARGETS
    selected: set[str] = set()
    for path in paths:
        selected.update(targets_for_path(path))
    return tuple(target for target in TARGETS if target in selected)


def matrix_for_targets(targets: Iterable[str]) -> dict[str, list[dict[str, Any]]]:
    return {"include": [MATRIX_ROWS[target] for target in targets]}


def main() -> None:
    event_name = os.environ["GITHUB_EVENT_NAME"]
    ref = os.environ["GITHUB_REF"]
    paths = None
    if event_name == "push" and ref.startswith("refs/heads/"):
        paths = changed_paths(os.getenv("BEFORE_SHA", ""), os.environ["GITHUB_SHA"])
    targets = select_targets(event_name, ref, os.getenv("REQUESTED_TARGET", ""), paths)
    matrix = json.dumps(matrix_for_targets(targets), separators=(",", ":"))
    output = Path(os.environ["GITHUB_OUTPUT"])
    with output.open("a", encoding="utf-8") as stream:
        stream.write(f"matrix={matrix}\n")
        stream.write(f"has_targets={str(bool(targets)).lower()}\n")
    print(f"Selected image targets: {', '.join(targets) or 'none'}")


if __name__ == "__main__":
    main()
