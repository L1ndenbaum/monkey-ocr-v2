"""Compatibility helpers for supported vLLM releases."""

from importlib import import_module
from typing import Any


_ATTENTION_REGISTRY_MODULES = (
    "vllm.v1.attention.backends.registry",
    "vllm.attention.backends.registry",
)


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
