import json
import runpy
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).parents[3]
SCRIPT = runpy.run_path(REPO_ROOT / "scripts" / "select_image_targets.py")
TARGETS = ("api", "vllm")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/monkeyocr/application/use_cases.py", {"api"}),
        ("src/monkeyocr/domain/media.py", {"api"}),
        ("src/monkeyocr/interface/http/v1/routes.py", {"api"}),
        ("src/monkeyocr/interface/cli/api.py", {"api"}),
        ("src/monkeyocr/infrastructure/pipeline/runner.py", {"api"}),
        ("src/monkeyocr/infrastructure/modeling/preprocessor.py", {"api"}),
        ("src/monkeyocr/infrastructure/modeling/preprocessing/models/seg.py", {"api"}),
        ("src/monkeyocr/infrastructure/config/settings.py", {"api"}),
        ("src/monkeyocr/infrastructure/storage/artifacts.py", {"api"}),
        ("src/monkeyocr/interface/cli/vllm.py", {"vllm"}),
        ("src/monkeyocr/infrastructure/modeling/monkeyocr_vllm.py", {"vllm"}),
        ("src/monkeyocr/infrastructure/modeling/vllm_compat.py", {"vllm"}),
        ("pyproject.toml", set(TARGETS)),
        ("uv.lock", set(TARGETS)),
        ("infrastructure/docker/Dockerfile", set(TARGETS)),
        (".dockerignore", set(TARGETS)),
        ("LICENSE", set(TARGETS)),
        (".github/workflows/publish-image.yml", set(TARGETS)),
        ("src/monkeyocr/infrastructure/modeling/new_runtime.py", set(TARGETS)),
        ("README.md", set()),
        ("src/monkeyocr/application/web_jobs.py", set()),
        ("src/monkeyocr/infrastructure/http/ocr_api.py", set()),
        ("src/monkeyocr/interface/http/internal_web/routes.py", set()),
        ("src/monkeyocr/infrastructure/training/html2otsl.py", set()),
        ("src/monkeyocr/interface/demo/gradio.py", set()),
        ("src/monkeyocr/interface/cli/model.py", set()),
        ("frontend/src/App.tsx", set()),
    ],
)
def test_path_ownership(path: str, expected: set[str]) -> None:
    assert SCRIPT["targets_for_path"](path) == expected


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["src/monkeyocr/application/use_cases.py", "README.md"], ("api",)),
        (["src/monkeyocr/interface/cli/vllm.py", "frontend/src/App.tsx"], ("vllm",)),
        (
            ["src/monkeyocr/interface/cli/vllm.py", "src/monkeyocr/interface/http/app.py"],
            TARGETS,
        ),
        (["README.md", "src/monkeyocr/interface/cli/web.py"], ()),
        ([".github/workflows/publish-image.yml"], TARGETS),
    ],
)
def test_branch_push_selects_union_of_runtime_consumers(
    paths: list[str], expected: tuple[str, ...]
) -> None:
    assert SCRIPT["select_targets"]("push", "refs/heads/dev", "", paths) == expected


@pytest.mark.parametrize(
    ("choice", "expected"),
    [("api", ("api",)), ("vllm", ("vllm",)), ("all", TARGETS)],
)
def test_manual_choice_ignores_changed_paths(choice: str, expected: tuple[str, ...]) -> None:
    assert SCRIPT["select_targets"]("workflow_dispatch", "refs/heads/main", choice, []) == expected


def test_version_tag_always_builds_both_targets() -> None:
    assert SCRIPT["select_targets"]("push", "refs/tags/v2.0.0", "", []) == TARGETS


def test_unavailable_push_diff_builds_both_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    before = "a" * 40
    after = "b" * 40

    def missing_diff(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.CalledProcessError(128, ["git", "diff"])

    monkeypatch.setattr(SCRIPT["subprocess"], "run", missing_diff)
    assert SCRIPT["changed_paths"]("0" * 40, after) is None
    assert SCRIPT["changed_paths"](before, after) is None
    assert SCRIPT["select_targets"]("push", "refs/heads/main", "", None) == TARGETS


def test_changed_paths_reads_exact_push_diff(monkeypatch: pytest.MonkeyPatch) -> None:
    before = "a" * 40
    after = "b" * 40

    def diff(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        assert command == ["git", "diff", "--name-only", "--no-renames", "-z", before, after]
        assert kwargs == {"check": True, "capture_output": True}
        return subprocess.CompletedProcess(
            command, 0, b"README.md\0src/monkeyocr/application/use_cases.py\0"
        )

    monkeypatch.setattr(SCRIPT["subprocess"], "run", diff)
    paths = SCRIPT["changed_paths"](before, after)
    assert paths == ("README.md", "src/monkeyocr/application/use_cases.py")
    assert SCRIPT["select_targets"]("push", "refs/heads/dev", "", paths) == ("api",)


def test_selector_writes_complete_manual_matrix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "workflow_dispatch")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/dev")
    monkeypatch.setenv("REQUESTED_TARGET", "vllm")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    SCRIPT["main"]()

    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    matrix = json.loads(values["matrix"])
    assert values["has_targets"] == "true"
    assert matrix == {"include": [SCRIPT["MATRIX_ROWS"]["vllm"]]}


def test_selector_skips_build_for_unrelated_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "github-output"

    def diff(*_args: Any, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 0, b"README.md\0frontend/src/App.tsx\0")

    monkeypatch.setattr(SCRIPT["subprocess"], "run", diff)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/dev")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    monkeypatch.setenv("BEFORE_SHA", "a" * 40)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    SCRIPT["main"]()

    values = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert values["has_targets"] == "false"
    assert json.loads(values["matrix"]) == {"include": []}
