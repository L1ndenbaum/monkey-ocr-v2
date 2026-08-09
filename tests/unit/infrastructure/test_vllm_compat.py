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


def test_load_vision_api_initializes_attention_before_vision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attention_backend_enum = object()
    get_vit_attn_backend = object()
    run_dp_sharded_mrope_vision_model = object()
    imported: list[str] = []

    def import_module(name: str) -> SimpleNamespace:
        imported.append(name)
        if name == "vllm.v1.attention.backends.registry":
            return SimpleNamespace(AttentionBackendEnum=attention_backend_enum)
        assert name == "vllm.model_executor.models.vision"
        return SimpleNamespace(
            get_vit_attn_backend=get_vit_attn_backend,
            run_dp_sharded_mrope_vision_model=run_dp_sharded_mrope_vision_model,
        )

    monkeypatch.setattr(vllm_compat, "import_module", import_module)

    assert vllm_compat.load_vision_api() == (
        attention_backend_enum,
        get_vit_attn_backend,
        run_dp_sharded_mrope_vision_model,
    )
    assert imported == [
        "vllm.v1.attention.backends.registry",
        "vllm.model_executor.models.vision",
    ]


def test_load_multimodal_data_dict_falls_back_to_vllm_0_11_module(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = object()
    imported: list[str] = []

    def import_module(name: str) -> SimpleNamespace:
        imported.append(name)
        if name == "vllm.inputs":
            return SimpleNamespace()
        assert name == "vllm.multimodal.inputs"
        return SimpleNamespace(MultiModalDataDict=expected)

    monkeypatch.setattr(vllm_compat, "import_module", import_module)

    assert vllm_compat.load_multimodal_data_dict() is expected
    assert imported == ["vllm.inputs", "vllm.multimodal.inputs"]
