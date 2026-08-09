import runpy
import tomllib
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).parents[3]
SCRIPT = runpy.run_path(
    REPOSITORY_ROOT / "infrastructure" / "docker" / "strip_sharded_packages.py",
)


def load_requirement_names(paths: list[Path]) -> set[str]:
    return set(SCRIPT["load_requirement_names"](paths))


def strip_distributions(site_packages: Path, names: set[str]) -> None:
    SCRIPT["strip_distributions"](site_packages, names)


def write_distribution(
    site_packages: Path,
    *,
    name: str,
    version: str,
    files: dict[str, str],
) -> None:
    dist_info = site_packages / f"{name.replace('-', '_')}-{version}.dist-info"
    dist_info.mkdir(parents=True)
    metadata_path = dist_info / "METADATA"
    metadata_path.write_text(
        f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        encoding="utf-8",
    )
    record_paths = [metadata_path.relative_to(site_packages).as_posix()]
    for relative_path, content in files.items():
        target = site_packages / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        record_paths.append(relative_path)
    record_path = dist_info / "RECORD"
    record_paths.append(record_path.relative_to(site_packages).as_posix())
    record_path.write_text(
        "".join(f"{relative_path},,\n" for relative_path in record_paths),
        encoding="utf-8",
    )


def test_strip_distributions_preserves_unlisted_nvidia_packages(tmp_path: Path) -> None:
    site_packages = tmp_path / "site-packages"
    site_packages.mkdir()
    write_distribution(
        site_packages,
        name="nvidia-cublas-cu12",
        version="12.8.4.1",
        files={
            "nvidia/cublas/lib/libcublas.so": "sharded",
            "nvidia/shared.py": "shared namespace file",
        },
    )
    write_distribution(
        site_packages,
        name="nvidia-cudnn-frontend",
        version="1.27.0",
        files={
            "nvidia/cudnn_frontend/__init__.py": "preserved",
            "nvidia/shared.py": "shared namespace file",
        },
    )

    strip_distributions(site_packages, {"nvidia-cublas-cu12"})

    assert not (site_packages / "nvidia/cublas/lib/libcublas.so").exists()
    assert (site_packages / "nvidia/cudnn_frontend/__init__.py").read_text() == "preserved"
    assert (site_packages / "nvidia/shared.py").read_text() == "shared namespace file"
    assert list(site_packages.glob("nvidia_cudnn_frontend-*.dist-info"))


def test_strip_distributions_rejects_missing_distribution(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not installed: torch"):
        strip_distributions(tmp_path, {"torch"})


def test_shard_requirements_are_unique_exact_pins_from_uv_lock() -> None:
    requirement_paths = sorted(
        (REPOSITORY_ROOT / "infrastructure" / "docker" / "requirements").glob("*.txt")
    )
    names = load_requirement_names(requirement_paths)
    requirement_lines = [
        line
        for path in requirement_paths
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(names) == len(requirement_lines)

    locked = tomllib.loads((REPOSITORY_ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked_packages = {
        (str(package["name"]), str(package["version"])) for package in locked["package"]
    }
    for requirement in requirement_lines:
        name, version = requirement.split("==", maxsplit=1)
        assert (name, version) in locked_packages


def test_requirement_parser_rejects_unpinned_dependency(tmp_path: Path) -> None:
    requirement_path = tmp_path / "requirements.txt"
    requirement_path.write_text("torch>=2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="exact name==version pin"):
        load_requirement_names([requirement_path])
