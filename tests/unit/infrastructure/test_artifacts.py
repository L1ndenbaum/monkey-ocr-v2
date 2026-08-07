import os
from pathlib import Path

import pytest

from monkeyocr.domain.errors import ArtifactNotFoundError
from monkeyocr.infrastructure.storage.artifacts import (
    ArtifactStore,
    make_artifact_filename,
    zip_dir,
)


def test_artifact_filename_respects_utf8_byte_limit() -> None:
    filename = make_artifact_filename("文" * 200, "_results.zip")

    assert len(filename.encode()) <= 255
    assert filename.endswith("_results.zip")


def test_artifact_store_rejects_path_traversal(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    with pytest.raises(ArtifactNotFoundError):
        store.artifact_path("../outside")


def test_artifact_store_resolves_one_zip(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    workspace = store.create_workspace("abcdef12-3456")
    artifact = workspace / "document_results.zip"
    zip_dir(workspace, artifact)

    assert store.artifact_path("abcdef12-3456") == artifact


def test_cleanup_removes_only_expired_workspaces(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path, ttl_seconds=10)
    expired = store.create_workspace("abcdef12-0001")
    current = store.create_workspace("abcdef12-0002")
    os.utime(expired, (80, 80))
    os.utime(current, (95, 95))

    assert store.cleanup_expired(now=100) == 1
    assert not expired.exists()
    assert current.exists()
