from types import SimpleNamespace

import pytest

from monkeyocr.infrastructure.modeling import vllm_compat


def test_load_attention_backend_enum_prefers_new_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    expected = object()

    def import_module(name: str) -> SimpleNamespace:
        assert name == "vllm.v1.attention.backends.registry"
        return SimpleNamespace(AttentionBackendEnum=expected)

    monkeypatch.setattr(vllm_compat, "import_module", import_module)

    assert vllm_compat.load_attention_backend_enum() is expected


def test_load_attention_backend_enum_falls_back_to_vllm_0_11_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = object()
    imported: list[str] = []

    def import_module(name: str) -> SimpleNamespace:
        imported.append(name)
        if name == "vllm.v1.attention.backends.registry":
            error = ModuleNotFoundError(name=name)
            raise error
        return SimpleNamespace(AttentionBackendEnum=expected)

    monkeypatch.setattr(vllm_compat, "import_module", import_module)

    assert vllm_compat.load_attention_backend_enum() is expected
    assert imported == [
        "vllm.v1.attention.backends.registry",
        "vllm.attention.backends.registry",
    ]


def test_load_attention_backend_enum_does_not_hide_nested_import_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def import_module(name: str) -> SimpleNamespace:
        raise ModuleNotFoundError(name="flash_attn")

    monkeypatch.setattr(vllm_compat, "import_module", import_module)

    with pytest.raises(ModuleNotFoundError) as exc_info:
        vllm_compat.load_attention_backend_enum()

    assert exc_info.value.name == "flash_attn"
