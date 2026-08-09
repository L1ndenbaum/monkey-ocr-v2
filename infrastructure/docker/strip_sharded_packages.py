#!/usr/bin/env python3
"""Remove only distributions that are restored as dedicated image layers."""

from __future__ import annotations

import argparse
import os
import re
from contextlib import suppress
from importlib.metadata import Distribution, distributions
from pathlib import Path

NORMALIZE_NAME = re.compile(r"[-_.]+")


def canonicalize_name(name: str) -> str:
    return NORMALIZE_NAME.sub("-", name).lower()


def load_requirement_names(requirement_paths: list[Path]) -> set[str]:
    names: set[str] = set()
    for requirement_path in requirement_paths:
        for line_number, raw_line in enumerate(
            requirement_path.read_text(encoding="utf-8").splitlines(),
            start=1,
        ):
            line = raw_line.partition("#")[0].strip()
            if not line:
                continue
            name, separator, version = line.partition("==")
            if not separator or not name.strip() or not version.strip():
                raise ValueError(
                    f"{requirement_path}:{line_number} must use an exact name==version pin"
                )
            names.add(canonicalize_name(name.strip()))
    return names


def index_distributions(site_packages: Path) -> dict[str, Distribution]:
    return {
        canonicalize_name(str(distribution.metadata["Name"])): distribution
        for distribution in distributions(path=[str(site_packages)])
        if distribution.metadata["Name"]
    }


def distribution_files(distribution: Distribution, site_packages: Path) -> set[Path]:
    files = distribution.files
    if files is None:
        name = canonicalize_name(str(distribution.metadata["Name"]))
        raise ValueError(f"Installed distribution {name} does not expose a RECORD file")
    located_files: set[Path] = set()
    for package_path in files:
        located = Path(os.path.abspath(distribution.locate_file(package_path)))
        if located.is_relative_to(site_packages):
            located_files.add(located)
    return located_files


def strip_distributions(site_packages: Path, names: set[str]) -> None:
    site_packages = site_packages.resolve()
    installed = index_distributions(site_packages)
    missing = sorted(names - installed.keys())
    if missing:
        raise ValueError(f"Cannot shard distributions that are not installed: {', '.join(missing)}")

    selected_files = set().union(
        *(distribution_files(installed[name], site_packages) for name in names)
    )
    preserved_files = set().union(
        *(
            distribution_files(distribution, site_packages)
            for name, distribution in installed.items()
            if name not in names
        )
    )
    for located in sorted(selected_files - preserved_files):
        if located.is_file() or located.is_symlink():
            located.unlink()

    directories = sorted(
        (path for path in site_packages.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for directory in directories:
        with suppress(OSError):
            directory.rmdir()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site-packages", required=True, type=Path)
    parser.add_argument(
        "--requirement",
        required=True,
        action="append",
        dest="requirements",
        type=Path,
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    names = load_requirement_names(args.requirements)
    strip_distributions(args.site_packages, names)


if __name__ == "__main__":
    main()
