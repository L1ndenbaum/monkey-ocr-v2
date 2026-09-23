import os
from pathlib import Path

import pytest

from monkeyocr.domain.errors import ArtifactNotFoundError
from monkeyocr.infrastructure.storage.artifacts import (
    ArtifactStore,
    load_text_markdown,
    make_artifact_filename,
    zip_dir,
)


def test_artifact_filename_respects_utf8_byte_limit() -> None:
    filename = make_artifact_filename("文" * 200, "_results.zip")

    assert len(filename.encode()) <= 255
    assert filename.endswith("_results.zip")


def test_text_markdown_omits_parser_picture_references(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        "# Title\n\n![image](../images/a_sub0.jpg)\n\nUseful text\n",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text(
        "![image](data:image/png;base64,YWJj)\n\n## More text\n",
        encoding="utf-8",
    )

    result = load_text_markdown(tmp_path)

    assert "# Title" in result
    assert "Useful text" in result
    assert "## More text" in result
    assert "![image]" not in result
    assert "data:image" not in result


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
