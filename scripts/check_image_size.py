#!/usr/bin/env python3
"""Enforce compressed container image and layer size budgets."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Any

GIB = 1024**3


def image_repository(reference: str) -> str:
    """Return a repository reference without a tag or digest."""

    repository = reference.split("@", maxsplit=1)[0]
    prefix, separator, leaf = repository.rpartition("/")
    if ":" in leaf:
        leaf = leaf.split(":", maxsplit=1)[0]
    return f"{prefix}{separator}{leaf}"


def select_linux_amd64_digest(index: dict[str, Any]) -> str:
    """Select the runnable linux/amd64 manifest, excluding attestations."""

    candidates = [
        manifest["digest"]
        for manifest in index.get("manifests", [])
        if manifest.get("platform", {}).get("os") == "linux"
        and manifest.get("platform", {}).get("architecture") == "amd64"
    ]
    if len(candidates) != 1:
        raise ValueError(f"Expected one linux/amd64 manifest, found {len(candidates)}")
    return str(candidates[0])


def measure_manifest(manifest: dict[str, Any]) -> tuple[int, int, int]:
    """Return total compressed bytes, largest layer bytes, and layer count."""

    sizes = [int(layer["size"]) for layer in manifest.get("layers", [])]
    if not sizes:
        raise ValueError("Image manifest does not contain any layers")
    return sum(sizes), max(sizes), len(sizes)


def inspect_raw(reference: str) -> dict[str, Any]:
    result = subprocess.run(
        ["docker", "buildx", "imagetools", "inspect", "--raw", reference],
        check=True,
        capture_output=True,
        text=True,
    )
    return dict(json.loads(result.stdout))


def resolve_image_manifest(reference: str) -> dict[str, Any]:
    document = inspect_raw(reference)
    if "manifests" not in document:
        return document
    digest = select_linux_amd64_digest(document)
    return inspect_raw(f"{image_repository(reference)}@{digest}")


def append_summary(message: str) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(f"{message}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", help="Published tag or digest to inspect")
    parser.add_argument("--max-total-gib", required=True, type=float)
    parser.add_argument("--max-layer-gib", required=True, type=float)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = resolve_image_manifest(args.reference)
    total_bytes, largest_bytes, layer_count = measure_manifest(manifest)
    total_gib = total_bytes / GIB
    largest_gib = largest_bytes / GIB
    message = (
        f"- `{args.reference}`: {total_gib:.2f} GiB compressed, "
        f"largest layer {largest_gib:.2f} GiB, {layer_count} layers"
    )
    print(message)
    append_summary(message)

    failures: list[str] = []
    if total_gib > args.max_total_gib:
        failures.append(f"total {total_gib:.2f} GiB exceeds {args.max_total_gib:.2f} GiB")
    if largest_gib > args.max_layer_gib:
        failures.append(f"largest layer {largest_gib:.2f} GiB exceeds {args.max_layer_gib:.2f} GiB")
    if failures:
        raise SystemExit("; ".join(failures))


if __name__ == "__main__":
    main()
