"""Filesystem workspace and safe archive operations for internal Web jobs."""

import shutil
import stat
import time
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

from monkeyocr.application.web_jobs import WebUpstreamError

PUBLIC_SUFFIXES = {
    ".gif",
    ".jpeg",
    ".jpg",
    ".json",
    ".md",
    ".png",
    ".webp",
}


class WebJobWorkspaceStore:
    def __init__(
        self,
        root: Path,
        *,
        ttl_seconds: int,
        max_extracted_bytes: int,
        max_archive_files: int,
    ) -> None:
        self.root = root.expanduser().resolve()
        self.ttl_seconds = max(1, ttl_seconds)
        self.max_extracted_bytes = max(1, max_extracted_bytes)
        self.max_archive_files = max(1, max_archive_files)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)

    def create(self, job_id: str) -> Path:
        workspace = self._job_path(job_id)
        workspace.mkdir(mode=0o700, exist_ok=False)
        return workspace

    def remove(self, job_id: str) -> None:
        workspace = self._job_path(job_id)
        if workspace.is_dir():
            shutil.rmtree(workspace)

    def cleanup_expired(
        self,
        *,
        now: float | None = None,
        exclude: Iterable[str] = (),
    ) -> tuple[str, ...]:
        cutoff = (time.time() if now is None else now) - self.ttl_seconds
        protected = frozenset(exclude)
        removed: list[str] = []
        for child in self.root.iterdir():
            if child.name not in protected and child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
                removed.append(child.name)
        return tuple(removed)

    def safe_extract(self, archive_path: Path, destination: Path) -> tuple[str, ...]:
        destination.mkdir(mode=0o700, exist_ok=False)
        extracted: list[str] = []
        total_size = 0
        try:
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                if len(members) > self.max_archive_files:
                    raise WebUpstreamError(
                        "INVALID_ARTIFACT",
                        "OCR artifact contains too many files.",
                    )
                for member in members:
                    relative = self._safe_member_path(member)
                    if relative is None:
                        continue
                    total_size += member.file_size
                    if total_size > self.max_extracted_bytes:
                        raise WebUpstreamError(
                            "INVALID_ARTIFACT",
                            "OCR artifact exceeds the extraction limit.",
                        )
                    target = (destination / relative).resolve()
                    if destination not in target.parents:
                        raise WebUpstreamError(
                            "INVALID_ARTIFACT",
                            "OCR artifact contains an unsafe path.",
                        )
                    if member.is_dir():
                        target.mkdir(mode=0o700, parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    with archive.open(member) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                    if target.suffix.lower() in PUBLIC_SUFFIXES:
                        extracted.append(relative.as_posix())
        except WebUpstreamError:
            shutil.rmtree(destination, ignore_errors=True)
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            shutil.rmtree(destination, ignore_errors=True)
            raise WebUpstreamError(
                "INVALID_ARTIFACT",
                "OCR artifact is not a valid ZIP archive.",
            ) from exc
        return tuple(sorted(extracted))

    @staticmethod
    def resolve_manifest_file(
        workspace: Path,
        relative_path: str,
        manifest: tuple[str, ...],
    ) -> Path:
        normalized = PurePosixPath(relative_path).as_posix()
        if normalized not in manifest:
            raise FileNotFoundError(relative_path)
        root = (workspace / "extracted").resolve()
        candidate = (root / normalized).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise FileNotFoundError(relative_path)
        return candidate

    @staticmethod
    def _safe_member_path(member: zipfile.ZipInfo) -> PurePosixPath | None:
        raw = member.filename.replace("\\", "/")
        path = PurePosixPath(raw)
        mode = member.external_attr >> 16
        if not raw or path.is_absolute() or ".." in path.parts or stat.S_ISLNK(mode):
            raise WebUpstreamError(
                "INVALID_ARTIFACT",
                "OCR artifact contains an unsafe entry.",
            )
        if member.is_dir():
            return path
        return path if path.suffix.lower() in PUBLIC_SUFFIXES else None

    def _job_path(self, job_id: str) -> Path:
        if not job_id or any(char not in "0123456789abcdef-" for char in job_id):
            raise ValueError("Invalid Web job identifier.")
        candidate = (self.root / job_id).resolve()
        if candidate.parent != self.root:
            raise ValueError("Invalid Web job identifier.")
        return candidate
