"""Safe result workspace and ZIP artifact management."""

import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path

from monkeyocr.domain.errors import ArtifactNotFoundError

MAX_FILENAME_BYTES = 255


def make_artifact_filename(
    stem: str,
    suffix: str,
    max_bytes: int = MAX_FILENAME_BYTES,
) -> str:
    """Build a deterministic filesystem-safe name without changing fitting names."""
    candidate = str(stem) + str(suffix)
    if len(candidate.encode()) <= max_bytes:
        return candidate

    digest = hashlib.sha256(candidate.encode()).hexdigest()[:10]
    trailer = f"_{digest}{suffix}"
    budget = max_bytes - len(trailer.encode())
    if budget <= 0:
        raise ValueError(f"Artifact suffix is too long: {suffix!r}")
    shortened = str(stem).encode()[:budget].decode(errors="ignore").rstrip(" .")
    return f"{shortened or 'artifact'}{trailer}"


def load_all_results(out_dir: str | Path) -> object:
    path = Path(out_dir) / "all_results.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_markdowns(md_dir: str | Path) -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(Path(md_dir).glob("*.md"))]


def zip_dir(src_dir: str | Path, zip_path: str | Path) -> None:
    source = Path(src_dir)
    destination = Path(zip_path)
    already_compressed = {
        ".7z",
        ".avi",
        ".gif",
        ".gz",
        ".jpeg",
        ".jpg",
        ".mp3",
        ".mp4",
        ".pdf",
        ".png",
        ".rar",
        ".webp",
        ".zip",
    }
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        for path in source.rglob("*"):
            if not path.is_file() or path == destination:
                continue
            compression = (
                zipfile.ZIP_STORED
                if path.suffix.lower() in already_compressed
                else zipfile.ZIP_DEFLATED
            )
            archive.write(path, path.relative_to(source), compress_type=compression)


class ArtifactStore:
    """Own request workspaces and expire them after a configured TTL."""

    def __init__(self, root: Path, ttl_seconds: int = 21_600) -> None:
        self.root = root.expanduser().resolve()
        self.ttl_seconds = max(1, ttl_seconds)
        self.root.mkdir(parents=True, exist_ok=True)

    def create_workspace(self, request_id: str) -> Path:
        workspace = self._request_path(request_id)
        workspace.mkdir(mode=0o700, parents=False, exist_ok=False)
        return workspace

    def artifact_path(self, request_id: str) -> Path:
        workspace = self._request_path(request_id)
        candidates = list(workspace.glob("*_results.zip")) if workspace.is_dir() else []
        if len(candidates) != 1 or not candidates[0].is_file():
            raise ArtifactNotFoundError("Result artifact was not found or has expired.")
        return candidates[0]

    def cleanup_expired(self, *, now: float | None = None) -> int:
        cutoff = (time.time() if now is None else now) - self.ttl_seconds
        removed = 0
        for child in self.root.iterdir():
            if child.is_dir() and child.stat().st_mtime < cutoff:
                shutil.rmtree(child)
                removed += 1
        return removed

    def remove_workspace(self, request_id: str) -> None:
        workspace = self._request_path(request_id)
        if workspace.is_dir():
            shutil.rmtree(workspace)

    def _request_path(self, request_id: str) -> Path:
        if not request_id or any(char not in "0123456789abcdef-" for char in request_id):
            raise ArtifactNotFoundError("Invalid request identifier.")
        candidate = (self.root / request_id).resolve()
        if candidate.parent != self.root:
            raise ArtifactNotFoundError("Invalid request identifier.")
        return candidate
