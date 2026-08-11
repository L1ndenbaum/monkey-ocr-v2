"""Runtime dependencies for the internal Web service."""

from dataclasses import dataclass
from typing import Protocol

from monkeyocr.application.ports import InternalWebOcrGateway
from monkeyocr.application.web_jobs import WebJobService
from monkeyocr.infrastructure.config.web_settings import WebSettings
from monkeyocr.infrastructure.storage.web_jobs import WebJobWorkspaceStore


class ManagedWebGateway(InternalWebOcrGateway, Protocol):
    async def ready(self) -> bool: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class InternalWebRuntime:
    settings: WebSettings
    store: WebJobWorkspaceStore
    jobs: WebJobService
    gateway: ManagedWebGateway
