"""Local and OpenAI-compatible vLLM inference backends."""

import asyncio
import os
import threading
import time
import uuid
import warnings
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import requests
import torch
from PIL import Image
from requests import exceptions as requests_exceptions
from vllm import SamplingParams

try:
    from vllm.engine.arg_utils import AsyncEngineArgs
    from vllm.engine.async_llm_engine import AsyncLLMEngine
except Exception:
    try:
        from vllm import AsyncEngineArgs, AsyncLLMEngine
    except Exception:
        AsyncLLMEngine = None
        AsyncEngineArgs = None

from monkeyocr.infrastructure.pipeline.media import image_to_png_data_uri, load_image


def build_vllm_prompt(question: str) -> str:
    return (
        "<|im_start|>system\nYou are a helpful assistant.<|im_end|>\n"
        f"<|im_start|>user\n<|vision_start|><|image_pad|><|vision_end|>"
        f"{question}<|im_end|>\n"
        "<|im_start|>assistant\n"
    )


def normalize_server_url(server_url: str) -> str:
    server_url = (server_url or "").strip().rstrip("/")
    if not server_url:
        return ""
    if "://" not in server_url:
        server_url = "http://" + server_url
    parsed = urlparse(server_url)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError(
            f"Unsupported server URL scheme: {parsed.scheme}. Use http:// or https://."
        )
    if parsed.scheme == "https" and parsed.hostname in {"127.0.0.1", "localhost", "0.0.0.0"}:
        warnings.warn(
            f"Server URL {server_url} uses HTTPS for a local vLLM endpoint. "
            "vLLM serve defaults to plain HTTP; using http:// instead.",
            RuntimeWarning,
        )
        parsed = parsed._replace(scheme="http")
        server_url = urlunparse(parsed)
    return server_url.rstrip("/")


class MonkeyOCRv2_ServerParsing:
    def __init__(
        self,
        server_url: str,
        model_name: str = "MonkeyOCRv2",
        timeout: int = 300,
        http_max_retries: int = 5,
        http_retry_backoff: float = 1.0,
    ):
        self.server_url = normalize_server_url(server_url)
        if self.server_url.endswith("/v1"):
            self.api_base = self.server_url
        else:
            self.api_base = self.server_url + "/v1"
        self.model_name = model_name
        self.timeout = timeout
        self.http_max_retries = max(0, int(http_max_retries))
        self.http_retry_backoff = max(0.0, float(http_retry_backoff))
        self.max_inflight = max(1, int(os.getenv("MOCR2_SERVER_MAX_INFLIGHT", "1024")))
        default_workers = min(self.max_inflight, 256)
        self.worker_limit = max(
            1,
            min(int(os.getenv("MOCR2_HTTP_WORKERS", str(default_workers))), self.max_inflight),
        )
        self._inflight = threading.BoundedSemaphore(self.max_inflight)
        self._thread_local = threading.local()
        self._sessions = set()
        self._sessions_lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=self.worker_limit,
            thread_name_prefix="mocr2-http",
        )
        self._closed = False

    def _session(self):
        session = getattr(self._thread_local, "session", None)
        if session is None:
            session = requests.Session()
            self._thread_local.session = session
            with self._sessions_lock:
                self._sessions.add(session)
        return session

    def _reset_session(self):
        session = getattr(self._thread_local, "session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            with self._sessions_lock:
                self._sessions.discard(session)
        self._thread_local.session = requests.Session()
        with self._sessions_lock:
            self._sessions.add(self._thread_local.session)
        return self._thread_local.session

    def _chat_completion(
        self,
        image: Image.Image,
        question: str,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> str:
        payload = {
            "model": self.model_name,
            "temperature": 0 if temperature is None else temperature,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_to_png_data_uri(image)}},
                        {"type": "text", "text": question},
                    ],
                }
            ],
        }
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if top_p is not None:
            payload["top_p"] = top_p

        url = f"{self.api_base}/chat/completions"
        last_exc = None
        with self._inflight:
            for attempt in range(self.http_max_retries + 1):
                try:
                    resp = self._session().post(
                        url,
                        json=payload,
                        timeout=self.timeout,
                    )
                    if resp.status_code in {429, 500, 502, 503, 504}:
                        raise requests_exceptions.HTTPError(
                            f"retryable HTTP {resp.status_code}: {resp.text[:500]}",
                            response=resp,
                        )
                    resp.raise_for_status()
                    data = resp.json()
                    return data["choices"][0]["message"]["content"]
                except (
                    requests_exceptions.ConnectionError,
                    requests_exceptions.Timeout,
                    requests_exceptions.ChunkedEncodingError,
                    requests_exceptions.SSLError,
                    requests_exceptions.HTTPError,
                ) as exc:
                    last_exc = exc
                    if isinstance(
                        exc, requests_exceptions.SSLError
                    ) or "WRONG_VERSION_NUMBER" in str(exc):
                        raise RuntimeError(
                            f"SSL protocol error when connecting to {url}. "
                            "vLLM serve usually runs plain HTTP, so use "
                            f"{self.api_base.replace('https://', 'http://', 1)} "
                            "instead of an https:// URL unless you configured TLS explicitly."
                        ) from exc
                    response = getattr(exc, "response", None)
                    if response is not None and response.status_code not in {
                        429,
                        500,
                        502,
                        503,
                        504,
                    }:
                        raise
                    self._reset_session()
                    if attempt >= self.http_max_retries:
                        break
                    sleep_s = self.http_retry_backoff * (2**attempt)
                    if sleep_s > 0:
                        time.sleep(min(sleep_s, 30.0))
        raise last_exc

    def batch_inference(
        self,
        images,
        questions,
        min_pixels=None,
        max_tokens: int = None,
        temperature: float = None,
        top_p: float = None,
        concurrency: int | None = None,
    ):
        if not images:
            return []
        if len(images) != len(questions):
            raise ValueError("images and questions must contain the same number of items.")
        if self._closed:
            raise RuntimeError("vLLM server backend has already been closed.")
        max_pixels = int(os.getenv("MOCR2_MAX_PIXELS")) if os.getenv("MOCR2_MAX_PIXELS") else None
        prepared = [load_image(img, max_pixels=max_pixels, min_pixels=min_pixels) for img in images]
        if len(prepared) == 1:
            return [
                self._chat_completion(
                    prepared[0],
                    questions[0],
                    max_tokens,
                    temperature,
                    top_p,
                )
            ]
        concurrency = max(1, min(int(concurrency or len(prepared)), self.worker_limit))
        outputs = [None] * len(prepared)
        pending = {}
        next_idx = 0
        try:
            while next_idx < len(prepared) or pending:
                while next_idx < len(prepared) and len(pending) < concurrency:
                    future = self._executor.submit(
                        self._chat_completion,
                        prepared[next_idx],
                        questions[next_idx],
                        max_tokens,
                        temperature,
                        top_p,
                    )
                    pending[future] = next_idx
                    next_idx += 1
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    outputs[pending.pop(future)] = future.result()
        except Exception:
            for future in pending:
                future.cancel()
            raise
        return outputs

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._sessions_lock:
            sessions = list(self._sessions)
            self._sessions.clear()
        for session in sessions:
            session.close()


class MonkeyOCRv2_AsyncParsing:
    def __init__(self, model_path: str, tp: int = 1, max_inflight: int = 1024):
        if AsyncLLMEngine is None or AsyncEngineArgs is None:
            raise ImportError("AsyncLLMEngine is unavailable in this vLLM installation.")
        self.model_name = os.path.basename(model_path)
        self.max_inflight = max(1, int(max_inflight))
        self.gen_config = SamplingParams(max_tokens=10000, temperature=0)
        self._engine_kwargs = {
            "model": model_path,
            "tensor_parallel_size": tp,
            "trust_remote_code": True,
            "max_model_len": 16384,
            "gpu_memory_utilization": self._auto_gpu_mem_ratio(0.5),
        }
        self.engine = None
        self._async_inflight = None
        self._closed = False
        try:
            engine_kwargs = dict(self._engine_kwargs)
            engine_kwargs["mm_processor_kwargs"] = {"use_fast": True}
            AsyncEngineArgs(**engine_kwargs)
            self._engine_kwargs = engine_kwargs
        except TypeError:
            self._engine_kwargs.pop("mm_processor_kwargs", None)
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self._run_coro(self._init_engine())

    def _auto_gpu_mem_ratio(self, ratio):
        mem_free, mem_total = torch.cuda.mem_get_info()
        return ratio * mem_free / mem_total

    def _run_loop(self):
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run_coro(self, coro, timeout: float | None = None):
        if self._closed:
            raise RuntimeError("Async vLLM engine has already been closed.")
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _init_engine(self):
        engine_args = AsyncEngineArgs(**self._engine_kwargs)
        self.engine = AsyncLLMEngine.from_engine_args(engine_args)
        self._async_inflight = asyncio.Semaphore(self.max_inflight)

    async def _generate_one(
        self,
        image: Image.Image,
        question: str,
        min_pixels=None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        top_p: float | None = None,
    ) -> str:
        max_pixels = int(os.getenv("MOCR2_MAX_PIXELS")) if os.getenv("MOCR2_MAX_PIXELS") else None
        gen_config = self.gen_config.clone()
        if max_tokens is not None:
            gen_config.max_tokens = max_tokens
        if temperature is not None:
            gen_config.temperature = temperature
        if top_p is not None:
            gen_config.top_p = top_p
        inputs = {
            "prompt": build_vllm_prompt(question),
            "multi_modal_data": {
                "image": load_image(image, max_pixels=max_pixels, min_pixels=min_pixels),
            },
        }
        final_output = None
        if self.engine is None:
            raise RuntimeError("Async vLLM engine is not initialized.")
        async for output in self.engine.generate(inputs, gen_config, request_id=str(uuid.uuid4())):
            final_output = output
        return final_output.outputs[0].text if final_output is not None else ""

    async def _generate_many(
        self,
        images,
        questions,
        min_pixels,
        max_tokens,
        temperature,
        top_p,
        concurrency,
    ):
        if self._async_inflight is None:
            raise RuntimeError("Async vLLM engine is not initialized.")
        batch_limit = asyncio.Semaphore(concurrency)

        async def generate_one(index):
            async with batch_limit, self._async_inflight:
                return await self._generate_one(
                    images[index],
                    questions[index],
                    min_pixels,
                    max_tokens,
                    temperature,
                    top_p,
                )

        results = await asyncio.gather(
            *(generate_one(i) for i in range(len(images))),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result
        return results

    def batch_inference(
        self,
        images,
        questions,
        min_pixels=None,
        max_tokens: int = None,
        temperature: float = None,
        top_p: float = None,
        concurrency: int | None = None,
    ):
        if not images:
            return []
        if len(images) != len(questions):
            raise ValueError("images and questions must contain the same number of items.")
        concurrency = max(1, min(int(concurrency or len(images)), self.max_inflight))
        return self._run_coro(
            self._generate_many(
                images,
                questions,
                min_pixels,
                max_tokens,
                temperature,
                top_p,
                concurrency,
            )
        )

    async def _shutdown_engine(self):
        engine = self.engine
        self.engine = None
        if engine is None:
            return
        shutdown = getattr(engine, "shutdown", None)
        close = getattr(engine, "close", None)
        if callable(shutdown):
            result = shutdown()
            if asyncio.iscoroutine(result):
                await result
        elif callable(close):
            result = close()
            if asyncio.iscoroutine(result):
                await result
        else:
            engine_core = getattr(engine, "engine_core", None)
            engine_core_shutdown = getattr(engine_core, "shutdown", None)
            if callable(engine_core_shutdown):
                engine_core_shutdown()

    async def _cancel_loop_tasks(self):
        current = asyncio.current_task()
        tasks = [
            task
            for task in asyncio.all_tasks(self._loop)
            if task is not current and not task.done()
        ]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(self._shutdown_engine(), self._loop)
                future.result(timeout=30)
            except Exception as exc:
                warnings.warn(
                    f"Failed to shutdown Async vLLM engine cleanly: {exc}", RuntimeWarning
                )
            try:
                future = asyncio.run_coroutine_threadsafe(self._cancel_loop_tasks(), self._loop)
                future.result(timeout=10)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread.is_alive():
            self._thread.join(timeout=10)
        if not self._loop.is_closed():
            self._loop.close()
