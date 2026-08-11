import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import pytest

from monkeyocr.application.web_jobs import WebUpstreamError
from monkeyocr.infrastructure.storage.web_jobs import WebJobWorkspaceStore

JOB_ID = "00000000-0000-0000-0000-000000000001"


def _store(tmp_path: Path, *, max_bytes: int = 1024) -> WebJobWorkspaceStore:
    return WebJobWorkspaceStore(
        tmp_path / "jobs",
        ttl_seconds=60,
        max_extracted_bytes=max_bytes,
        max_archive_files=10,
    )


def test_safe_extract_builds_public_manifest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    workspace = store.create(JOB_ID)
    archive = workspace / "result.zip"
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        output.writestr("markdowns/result.md", "hello")
        output.writestr("images/result.jpg", b"image")
        output.writestr("private/source.pdf", b"pdf")

    manifest = store.safe_extract(archive, workspace / "extracted")

    assert manifest == ("images/result.jpg", "markdowns/result.md")
    assert not (workspace / "extracted/private/source.pdf").exists()


@pytest.mark.parametrize("member", ["../escape.md", "/absolute.md", "safe/../../escape.md"])
def test_safe_extract_rejects_path_traversal(tmp_path: Path, member: str) -> None:
    store = _store(tmp_path)
    workspace = store.create(JOB_ID)
    archive = workspace / "result.zip"
    with ZipFile(archive, "w") as output:
        output.writestr(member, "unsafe")

    with pytest.raises(WebUpstreamError, match="unsafe"):
        store.safe_extract(archive, workspace / "extracted")
    assert not (workspace / "extracted").exists()


def test_safe_extract_rejects_symlink(tmp_path: Path) -> None:
    store = _store(tmp_path)
    workspace = store.create(JOB_ID)
    archive = workspace / "result.zip"
    link = ZipInfo("link.md")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(archive, "w") as output:
        output.writestr(link, "target")

    with pytest.raises(WebUpstreamError, match="unsafe"):
        store.safe_extract(archive, workspace / "extracted")


def test_safe_extract_enforces_uncompressed_size(tmp_path: Path) -> None:
    store = _store(tmp_path, max_bytes=4)
    workspace = store.create(JOB_ID)
    archive = workspace / "result.zip"
    with ZipFile(archive, "w") as output:
        output.writestr("large.md", "12345")

    with pytest.raises(WebUpstreamError, match="extraction limit"):
        store.safe_extract(archive, workspace / "extracted")
    assert not (workspace / "extracted").exists()


def test_manifest_resolution_rejects_unlisted_files(tmp_path: Path) -> None:
    store = _store(tmp_path)
    workspace = store.create(JOB_ID)
    extracted = workspace / "extracted"
    extracted.mkdir()
    (extracted / "listed.md").write_text("ok", encoding="utf-8")
    (extracted / "hidden.md").write_text("secret", encoding="utf-8")

    assert store.resolve_manifest_file(workspace, "listed.md", ("listed.md",)).name == "listed.md"
    with pytest.raises(FileNotFoundError):
        store.resolve_manifest_file(workspace, "hidden.md", ("listed.md",))


def test_cleanup_expired_returns_removed_job_ids(tmp_path: Path) -> None:
    store = _store(tmp_path)
    workspace = store.create(JOB_ID)

    removed = store.cleanup_expired(now=workspace.stat().st_mtime + 61)

    assert removed == (JOB_ID,)
    assert not workspace.exists()


def test_cleanup_expired_preserves_excluded_active_job(tmp_path: Path) -> None:
    store = _store(tmp_path)
    workspace = store.create(JOB_ID)

    removed = store.cleanup_expired(
        now=workspace.stat().st_mtime + 61,
        exclude=(JOB_ID,),
    )

    assert removed == ()
    assert workspace.is_dir()
