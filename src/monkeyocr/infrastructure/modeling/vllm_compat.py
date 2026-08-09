"""Compatibility helpers for supported vLLM releases."""

from importlib import import_module
from typing import Any


_ATTENTION_REGISTRY_MODULES = (
    "vllm.v1.attention.backends.registry",
    "vllm.attention.backends.registry",
)
_MULTIMODAL_INPUT_MODULES = (
    "vllm.inputs",
    "vllm.multimodal.inputs",
)
_VISION_MODULE = "vllm.model_executor.models.vision"


def load_attention_backend_enum() -> Any:
    """Load ``AttentionBackendEnum`` from new or legacy vLLM layouts."""

    last_error: ModuleNotFoundError | None = None
    for module_name in _ATTENTION_REGISTRY_MODULES:
        try:
            module = import_module(module_name)
        except ModuleNotFoundError as error:
            missing_name = error.name
            if missing_name is None or (
                missing_name != module_name
                and not module_name.startswith(f"{missing_name}.")
            ):
                raise
            last_error = error
            continue

        try:
            return module.AttentionBackendEnum
        except AttributeError as error:
            raise ImportError(
                f"{module_name} does not expose AttentionBackendEnum"
            ) from error

    raise ModuleNotFoundError(
        "No supported vLLM attention backend registry module is installed"
    ) from last_error


def load_vision_api() -> tuple[Any, Any, Any]:
    """Load vLLM vision helpers after initializing its attention package.

    vLLM 0.11 imports the vision module from ``vllm.attention.layer``. Importing
    vision first therefore re-enters the partially initialized vision module.
    Resolving the attention registry first preserves vLLM's supported import
    order while still allowing newer releases to use the v1 registry path.
    """

    attention_backend_enum = load_attention_backend_enum()
    vision_module = import_module(_VISION_MODULE)
    try:
        return (
            attention_backend_enum,
            vision_module.get_vit_attn_backend,
            vision_module.run_dp_sharded_mrope_vision_model,
        )
    except AttributeError as error:
        raise ImportError(f"{_VISION_MODULE} does not expose the required API") from error


def load_multimodal_data_dict() -> Any:
    """Load ``MultiModalDataDict`` across supported vLLM layouts."""

    for module_name in _MULTIMODAL_INPUT_MODULES:
        module = import_module(module_name)
        multi_modal_data_dict = getattr(module, "MultiModalDataDict", None)
        if multi_modal_data_dict is not None:
            return multi_modal_data_dict

    raise ImportError("No supported vLLM module exposes MultiModalDataDict")
