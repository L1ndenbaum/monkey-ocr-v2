"""Non-blocking admission control for expensive OCR requests."""

import asyncio


class RequestAdmission:
    def __init__(self, limit: int) -> None:
        if limit < 1:
            raise ValueError("OCR concurrency limit must be positive.")
        self._limit = limit
        self._active = 0
        self._lock = asyncio.Lock()

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self._active >= self._limit:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active < 1:
                raise RuntimeError("OCR admission slot released without an acquisition.")
            self._active -= 1
