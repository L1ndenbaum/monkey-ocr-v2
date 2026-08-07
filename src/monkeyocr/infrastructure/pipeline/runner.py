from monkeyocr.infrastructure.modeling import monkeyocr_vllm  # noqa: F401

import ast
import os
import json
import re
import time
import torch
import base64
import requests
import warnings
import traceback
import threading
import queue
import asyncio
import uuid
import shutil
from collections import OrderedDict, deque
from requests import exceptions as requests_exceptions
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from io import BytesIO
from html import escape
from pathlib import Path
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Union
from urllib.parse import urlparse, urlunparse
from vllm import SamplingParams

try:
    from vllm.engine.async_llm_engine import AsyncLLMEngine
    from vllm.engine.arg_utils import AsyncEngineArgs
except Exception:
    try:
        from vllm import AsyncLLMEngine, AsyncEngineArgs
    except Exception:
        AsyncLLMEngine = None
        AsyncEngineArgs = None
from PIL import Image, ImageFile, ImageDraw, ImageOps

try:
    from tqdm import tqdm
except Exception:
    tqdm = None

from monkeyocr.infrastructure.modeling.preprocessor import Preprocessor
from monkeyocr.infrastructure.pipeline.config import BackendConfig, OutputDirs, PipelineConfig
from monkeyocr.infrastructure.storage.artifacts import (
    load_all_results,
    load_markdowns,
    make_artifact_filename,
    zip_dir,
)

from monkeyocr.infrastructure.pipeline.backends import (
    MonkeyOCRv2_AsyncParsing,
    MonkeyOCRv2_ServerParsing,
    build_vllm_prompt,
    normalize_server_url,
)
from monkeyocr.infrastructure.pipeline.constants import (
    ALL_PROMPT,
    IMAGE_EXTS,
    INPUT_EXTS,
    PDFIUM_LOCK,
)
from monkeyocr.infrastructure.pipeline.media import (
    _PdfRenderer,
    _count_pending_documents,
    _count_pending_pages,
    _iter_input_page_events,
    _render_pdf_page,
    build_result_record,
    get_preprocessed_page_path,
    image_to_png_data_uri,
    load_image,
    load_image_from_base64,
    load_pdf_images,
    open_oriented_image,
    save_picture_block,
    save_preprocessed_page,
)
from monkeyocr.infrastructure.pipeline.postprocessing import (
    _build_page_tasks,
    _format_block_content,
    _recognize_one_block,
    batch_inference_with_repeat_retry,
    detect_repeat_token,
    draw_layout_pdf,
    get_layout,
    otsl_to_html,
    parse_end2end_output,
    process_formula,
    result2md,
)


def run_streaming_pipeline(
    args,
    preprocessor,
    model,
    out_dir: Path,
    json_dir: Path,
    md_dir: Path,
    image_dir: Path,
    show_progress_bar: bool = False,
    verbose: bool = True,
):
    sentinel = object()
    page_window = max(1, int(args.page_max_inflight))
    server_window = max(1, int(args.server_max_inflight))
    layout_q = queue.Queue(maxsize=page_window)
    rec_q = queue.Queue(maxsize=max(page_window * 8, server_window * 2))
    layout_workers = max(1, min(page_window, server_window))
    rec_workers = max(1, min(server_window, max(32, page_window * 4), 256))
    done_q = queue.Queue()
    error_q = queue.Queue()
    stop_event = threading.Event()
    lock = threading.Lock()
    states = {}
    completed_records = []
    stats = {
        "submitted_docs": 0,
        "skipped_docs": 0,
        "submitted_pages": 0,
        "time_pre": 0.0,
        "time_pre_stage": 0.0,
        "time_parse_requests": 0.0,
        "parse_started_at": None,
        "parse_finished_at": None,
    }

    layout_dir = out_dir / "layouts" if args.draw_layout else None
    total_docs = _count_pending_documents(args.input_path, md_dir, args.skip_processed)
    pbar = None
    pre_pbar = None
    if show_progress_bar and tqdm is not None and total_docs > 0:
        total_pages = _count_pending_pages(args.input_path, md_dir, args.skip_processed)
        if not args.skip_preprocess:
            pre_pbar = tqdm(
                total=total_pages,
                dynamic_ncols=True,
                bar_format="{desc} |{bar}| {n_fmt}/{total_fmt}",
                position=0,
                leave=True,
            )

    def maybe_complete(state):
        if state["pending_pages"] == 0 and state["pending_recs"] == 0 and not state["done"]:
            state["done"] = True
            done_q.put(state["doc_id"])

    def add_page_result(state, page_idx, rec):
        state["page_results"][page_idx].append(rec)

    def mark_parse_started():
        if stats["parse_started_at"] is None:
            stats["parse_started_at"] = time.time()

    def raise_if_worker_failed():
        with error_q.mutex:
            if error_q.queue:
                raise error_q.queue[0]

    def put_checked(q, item):
        while not stop_event.is_set():
            raise_if_worker_failed()
            try:
                q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue
        raise_if_worker_failed()
        raise RuntimeError("Streaming pipeline stopped before item could be queued.")

    def put_sentinels(q, count):
        sent = 0
        while sent < count and not stop_event.is_set():
            raise_if_worker_failed()
            try:
                q.put(sentinel, timeout=0.2)
                sent += 1
            except queue.Full:
                continue

    def get_checked(q):
        while not stop_event.is_set():
            try:
                return q.get(timeout=0.2)
            except queue.Empty:
                raise_if_worker_failed()
                continue
        return sentinel

    def record_worker_error(exc):
        stop_event.set()
        error_q.put(exc)

    def release_input_page(page):
        if not isinstance(page, dict):
            return
        page.pop("image", None)
        release_slot = page.pop("_release_input_slot", None)
        if release_slot is not None:
            release_slot()

    def layout_worker():
        page = None
        try:
            while True:
                page = get_checked(layout_q)
                if page is sentinel:
                    break
                t0 = time.time()
                with lock:
                    mark_parse_started()
                img = page["image"]
                if args.end2end:
                    if args.retry_repeat:
                        raw = batch_inference_with_repeat_retry(
                            model,
                            [img],
                            [ALL_PROMPT["END2END"]],
                            max_tokens=None,
                            max_retries=args.retry_repeat_max_retries,
                        )[0]
                    else:
                        raw = model.batch_inference(
                            [img],
                            [ALL_PROMPT["END2END"]],
                            max_tokens=None,
                        )[0]
                    page_recs, page_layout = parse_end2end_output(raw, img.size)
                    for rec in page_recs:
                        rec["page_num"] = page["page_idx"] + 1
                    with lock:
                        stats["time_parse_requests"] += time.time() - t0
                        state = states[page["doc_id"]]
                        state["layouts"][page["page_idx"]] = page_layout
                        state["pending_pages"] -= 1
                        for rec in page_recs:
                            add_page_result(state, page["page_idx"], rec)
                        maybe_complete(state)
                    release_input_page(page)
                    continue

                items = get_layout(model, [img])[0]
                with lock:
                    stats["time_parse_requests"] += time.time() - t0
                    state = states[page["doc_id"]]
                    state["layouts"][page["page_idx"]] = items

                created_rec = 0
                no_infer_records = []
                rec_tasks = []
                for task in _build_page_tasks(page["page_idx"], img, items, doc_id=page["doc_id"]):
                    if task["need_infer"]:
                        created_rec += 1
                        rec_tasks.append(task)
                    else:
                        no_infer_records.append(task)

                with lock:
                    state = states[page["doc_id"]]
                    state["pending_pages"] -= 1
                    state["pending_recs"] += created_rec
                    for task in no_infer_records:
                        content = _format_block_content(
                            task,
                            "",
                            state["doc"]["name"],
                            state["picture_counts"],
                            args.use_base64,
                            image_dir,
                        )
                        add_page_result(
                            state,
                            task["page_idx"],
                            {
                                "bbox": task["bbox"],
                                "label": task["label"],
                                "content": content,
                                "page_num": task["page_num"],
                                "_block_idx": task["block_idx"],
                            },
                        )
                    maybe_complete(state)
                release_input_page(page)
                for task in rec_tasks:
                    put_checked(rec_q, task)
        except Exception as exc:
            release_input_page(page)
            record_worker_error(exc)

    def recognition_worker():
        try:
            while True:
                task = get_checked(rec_q)
                if task is sentinel:
                    break
                t0 = time.time()
                with lock:
                    mark_parse_started()
                raw = _recognize_one_block(
                    model,
                    task,
                    args.retry_repeat,
                    args.retry_repeat_max_retries,
                )
                elapsed = time.time() - t0
                with lock:
                    stats["time_parse_requests"] += elapsed
                    state = states[task["doc_id"]]
                    content = _format_block_content(
                        task,
                        raw,
                        state["doc"]["name"],
                        state["picture_counts"],
                        args.use_base64,
                        image_dir,
                    )
                    add_page_result(
                        state,
                        task["page_idx"],
                        {
                            "bbox": task["bbox"],
                            "label": task["label"],
                            "content": content,
                            "page_num": task["page_num"],
                            "_block_idx": task["block_idx"],
                        },
                    )
                    state["pending_recs"] -= 1
                    maybe_complete(state)
        except Exception as exc:
            record_worker_error(exc)

    def writer_worker():
        try:
            while True:
                doc_id = get_checked(done_q)
                if doc_id is sentinel:
                    break
                with lock:
                    state = states[doc_id]
                    doc_results = []
                    for recs in state["page_results"]:
                        recs = sorted(recs, key=lambda x: x.pop("_block_idx", 0))
                        doc_results.extend(recs)
                    record = build_result_record(state["doc"], doc_results)

                name = state["doc"]["name"]
                if pbar is not None:
                    pbar.set_description_str(f"Parsing {name}")
                if args.draw_layout and layout_dir is not None:
                    draw_layout_pdf(
                        state["doc"]["images"],
                        state["layouts"],
                        str(layout_dir / make_artifact_filename(name, "_layout.pdf")),
                    )
                (json_dir / make_artifact_filename(name, ".json")).write_text(
                    json.dumps(record, ensure_ascii=False, indent=1),
                    encoding="utf-8",
                )
                result2md(
                    [name],
                    [doc_results],
                    save_dir=str(md_dir),
                    keep_header_footer=args.keep_header_footer,
                )
                with lock:
                    completed_records.append((state["doc_idx"], record))
                    states.pop(doc_id, None)
                if pbar is not None:
                    pbar.update(1)
        except Exception as exc:
            record_worker_error(exc)

    t_start = time.time()
    writer = None
    reader_thread = None
    preprocess_save_threads = []
    layout_threads = []
    rec_threads = []

    def join_checked(th):
        while th.is_alive():
            th.join(timeout=0.2)
            raise_if_worker_failed()

    def join_best_effort(th, timeout=2.0):
        deadline = time.time() + timeout
        while th.is_alive() and time.time() < deadline:
            th.join(timeout=0.2)

    def start_input_reader(preprocessed_dir=None):
        input_q = queue.Queue(maxsize=page_window)
        raw_page_slots = threading.BoundedSemaphore(page_window)

        def acquire_page_slot():
            while not stop_event.is_set():
                if raw_page_slots.acquire(timeout=0.2):
                    return True
                raise_if_worker_failed()
            return False

        def reader_worker():
            try:
                events = _iter_input_page_events(
                    args.input_path,
                    md_dir,
                    args.skip_processed,
                    acquire_page_slot,
                    raw_page_slots.release,
                    preprocessed_dir,
                )
                for event in events:
                    try:
                        put_checked(input_q, event)
                    except Exception:
                        if event[0] == "page" and event[5]:
                            raw_page_slots.release()
                        raise
            except Exception as exc:
                record_worker_error(exc)
            finally:
                if not stop_event.is_set():
                    put_checked(input_q, sentinel)

        thread = threading.Thread(
            target=reader_worker,
            name="mocr2-input-reader",
            daemon=True,
        )
        thread.start()
        return input_q, raw_page_slots, thread

    pipeline_error = None
    try:
        prepared_docs = None
        if not args.skip_preprocess:
            preprocess_stage_started = time.time()
            prepared_docs = []
            prepared_docs_by_id = {}
            preprocess_batch = []
            preprocess_target = min(max(1, args.preprocess_batch_size), page_window)
            warm_pages = 0
            preprocess_save_q = queue.Queue(maxsize=page_window)
            preprocess_save_workers = max(1, min(4, page_window, os.cpu_count() or 1))
            progress_lock = threading.Lock()

            def update_preprocess_progress(doc_name):
                if pre_pbar is not None:
                    with progress_lock:
                        pre_pbar.set_description_str(f"Preprocessing {doc_name}")
                        pre_pbar.update(1)

            def preprocess_save_worker():
                try:
                    while True:
                        item = get_checked(preprocess_save_q)
                        if item is sentinel:
                            break
                        save_preprocessed_page(
                            item["image"],
                            out_dir / "preprocessed",
                            item["doc_name"],
                            item["page_idx"],
                        )
                        update_preprocess_progress(item["doc_name"])
                except Exception as exc:
                    record_worker_error(exc)

            def flush_preprocess_batch():
                nonlocal preprocess_batch, warm_pages
                if not preprocess_batch:
                    return
                current_batch = preprocess_batch
                preprocess_batch = []
                try:
                    t0 = time.time()
                    output_batch = preprocessor.preprocess_images(
                        [item["image"] for item in current_batch],
                        batch_size=args.preprocess_batch_size,
                    )
                    stats["time_pre"] += time.time() - t0
                    if len(output_batch) != len(current_batch):
                        raise RuntimeError(
                            "Preprocessor returned a different number of images than it received."
                        )
                    for item, image in zip(current_batch, output_batch):
                        if item["slot_acquired"]:
                            raw_page_slots.release()
                            item["slot_acquired"] = False
                        item.pop("image", None)
                        item["doc"]["_image_sizes"][item["page_idx"]] = [
                            int(image.size[0]),
                            int(image.size[1]),
                        ]
                        if warm_pages < page_window:
                            item["doc"]["preprocessed_paths"][item["page_idx"]] = image
                            warm_pages += 1
                        put_checked(
                            preprocess_save_q,
                            {
                                "image": image,
                                "doc_name": item["doc"]["name"],
                                "page_idx": item["page_idx"],
                            },
                        )
                finally:
                    for item in current_batch:
                        if item["slot_acquired"]:
                            raw_page_slots.release()

            preprocess_save_threads = [
                threading.Thread(
                    target=preprocess_save_worker,
                    name=f"mocr2-preprocess-writer-{i}",
                    daemon=True,
                )
                for i in range(preprocess_save_workers)
            ]
            for thread in preprocess_save_threads:
                thread.start()
            input_q, raw_page_slots, reader_thread = start_input_reader(out_dir / "preprocessed")
            while True:
                event = get_checked(input_q)
                if event is sentinel:
                    break
                if event[0] == "skipped":
                    stats["skipped_docs"] += 1
                    continue
                if event[0] == "doc":
                    _, doc_id, doc = event
                    prepared_doc = {
                        "name": doc["name"],
                        "image_name": doc["image_name"],
                        "image_path": doc["image_path"],
                        "images": [None] * doc["pdf_pages"],
                        "preprocessed_paths": [None] * doc["pdf_pages"],
                        "_image_sizes": [None] * doc["pdf_pages"],
                        "pdf_pages": doc["pdf_pages"],
                    }
                    prepared_docs.append(prepared_doc)
                    prepared_docs_by_id[doc_id] = prepared_doc
                    continue

                _, doc_id, page_idx, image, cached_path, slot_acquired = event
                prepared_doc = prepared_docs_by_id[doc_id]
                prepared_doc["preprocessed_paths"][page_idx] = cached_path
                if image is None:
                    cached_image = load_image(cached_path)
                    prepared_doc["_image_sizes"][page_idx] = [
                        int(cached_image.size[0]),
                        int(cached_image.size[1]),
                    ]
                    if warm_pages < page_window:
                        prepared_doc["preprocessed_paths"][page_idx] = cached_image
                        warm_pages += 1
                    update_preprocess_progress(prepared_doc["name"])
                    continue

                preprocess_batch.append(
                    {
                        "doc": prepared_doc,
                        "page_idx": page_idx,
                        "image": image,
                        "slot_acquired": slot_acquired,
                    }
                )
                if len(preprocess_batch) >= preprocess_target:
                    flush_preprocess_batch()

            flush_preprocess_batch()
            join_checked(reader_thread)
            reader_thread = None
            put_sentinels(preprocess_save_q, len(preprocess_save_threads))
            for thread in preprocess_save_threads:
                join_checked(thread)
            preprocess_save_threads = []
            stats["time_pre_stage"] = time.time() - preprocess_stage_started

            for doc in prepared_docs:
                image_sizes = doc.pop("_image_sizes")
                if any(size is None for size in image_sizes):
                    raise RuntimeError(
                        f"Preprocessing did not produce every page for {doc['name']}."
                    )
                doc["image_size"] = image_sizes[0] if len(image_sizes) == 1 else image_sizes

            if pre_pbar is not None:
                pre_pbar.set_description_str("Preprocessing complete")
                pre_pbar.close()
                pre_pbar = None
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

        if show_progress_bar and tqdm is not None and total_docs > 0:
            pbar = tqdm(
                total=total_docs,
                dynamic_ncols=True,
                bar_format="{desc} |{bar}| {n_fmt}/{total_fmt}",
                position=0,
                leave=True,
            )

        writer = threading.Thread(target=writer_worker, name="mocr2-writer", daemon=True)
        writer.start()
        layout_threads = [
            threading.Thread(target=layout_worker, name=f"mocr2-layout-{i}", daemon=True)
            for i in range(layout_workers)
        ]
        rec_threads = [
            threading.Thread(target=recognition_worker, name=f"mocr2-rec-{i}", daemon=True)
            for i in range(rec_workers)
        ]
        for th in layout_threads + rec_threads:
            th.start()

        def register_document(doc_id, doc, doc_idx):
            page_count = doc["pdf_pages"]
            if pbar is not None:
                pbar.set_description_str(f"Parsing {doc['name']}")
            elif verbose:
                print(f"Streaming document {doc_idx + 1}: {doc['name']} ({page_count} pages)")
            with lock:
                states[doc_id] = {
                    "doc_id": doc_id,
                    "doc_idx": doc_idx,
                    "doc": doc,
                    "layouts": [[] for _ in range(page_count)],
                    "page_results": [[] for _ in range(page_count)],
                    "picture_counts": [0],
                    "pending_pages": page_count,
                    "pending_recs": 0,
                    "done": False,
                }
                stats["submitted_docs"] += 1
                stats["submitted_pages"] += page_count

        if prepared_docs is not None:
            for doc_idx, doc in enumerate(prepared_docs):
                doc_id = doc_idx
                register_document(doc_id, doc, doc_idx)
                for page_idx, source in enumerate(doc["preprocessed_paths"]):
                    image = load_image(source) if isinstance(source, str) else source
                    if args.draw_layout:
                        with lock:
                            states[doc_id]["doc"]["images"][page_idx] = image
                    put_checked(
                        layout_q,
                        {
                            "doc_id": doc_id,
                            "page_idx": page_idx,
                            "image": image,
                        },
                    )
        else:
            input_q, raw_page_slots, reader_thread = start_input_reader()
            direct_docs = {}
            while True:
                event = get_checked(input_q)
                if event is sentinel:
                    break
                if event[0] == "skipped":
                    stats["skipped_docs"] += 1
                    continue
                if event[0] == "doc":
                    _, doc_id, doc_meta = event
                    page_count = doc_meta["pdf_pages"]
                    doc = {
                        **doc_meta,
                        "images": [None] * page_count,
                        "_image_sizes": [None] * page_count,
                    }
                    direct_docs[doc_id] = doc
                    register_document(doc_id, doc, len(direct_docs) - 1)
                    continue

                _, doc_id, page_idx, image, _, slot_acquired = event
                doc = direct_docs[doc_id]
                doc["_image_sizes"][page_idx] = [int(image.size[0]), int(image.size[1])]
                if args.draw_layout:
                    with lock:
                        states[doc_id]["doc"]["images"][page_idx] = image
                if all(size is not None for size in doc["_image_sizes"]):
                    image_sizes = doc.pop("_image_sizes")
                    doc["image_size"] = image_sizes[0] if len(image_sizes) == 1 else image_sizes
                page = {
                    "doc_id": doc_id,
                    "page_idx": page_idx,
                    "image": image,
                }
                if slot_acquired:
                    page["_release_input_slot"] = raw_page_slots.release
                try:
                    put_checked(layout_q, page)
                except Exception:
                    release_input_page(page)
                    raise
            join_checked(reader_thread)
            reader_thread = None

        put_sentinels(layout_q, len(layout_threads))
        for th in layout_threads:
            join_checked(th)
        put_sentinels(rec_q, len(rec_threads))
        for th in rec_threads:
            join_checked(th)
    except Exception as exc:
        pipeline_error = exc
        stop_event.set()
        for q in (layout_q, rec_q, done_q):
            for _ in range(max(1, layout_workers + rec_workers + 2)):
                try:
                    q.put_nowait(sentinel)
                except queue.Full:
                    break
    finally:
        if reader_thread is not None:
            join_best_effort(reader_thread)
        for thread in preprocess_save_threads:
            join_best_effort(thread)
        if pipeline_error is None and error_q.empty():
            for th in layout_threads:
                join_checked(th)
            for th in rec_threads:
                join_checked(th)
        else:
            for th in layout_threads:
                join_best_effort(th)
            for th in rec_threads:
                join_best_effort(th)
        with lock:
            if stats["parse_started_at"] is not None:
                stats["parse_finished_at"] = time.time()
        try:
            done_q.put_nowait(sentinel)
        except queue.Full:
            pass
        if writer is not None and pipeline_error is None and error_q.empty():
            join_checked(writer)
        elif writer is not None:
            join_best_effort(writer)
        if pbar is not None:
            pbar.close()
        if pre_pbar is not None:
            pre_pbar.close()

    if pipeline_error is not None:
        raise pipeline_error

    if not error_q.empty():
        raise error_q.get()

    all_results = [record for _, record in sorted(completed_records, key=lambda x: x[0])]
    (out_dir / "all_results.json").write_text(
        json.dumps(all_results, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    time_used = time.time() - t_start
    time_parse = 0.0
    if stats["parse_started_at"] is not None and stats["parse_finished_at"] is not None:
        time_parse = stats["parse_finished_at"] - stats["parse_started_at"]
    if verbose:
        print(f"Preprocess time: {stats['time_pre_stage']:.2f} s, parsing time: {time_parse:.2f} s")
    if verbose and stats["skipped_docs"]:
        print(f"--skip-processed: skipped {stats['skipped_docs']} already processed documents.")
    avg = time_used / max(1, stats["submitted_docs"])
    if verbose:
        print(
            f"Total time used: {time_used:.2f} s / {stats['submitted_docs']} docs, "
            f"{stats['submitted_pages']} pages, avg {avg:.2f} s/doc."
        )
        print(f"Processing completed. Results saved to {out_dir}")

    preprocessed_dir = out_dir / "preprocessed"
    if preprocessed_dir.exists():
        shutil.rmtree(preprocessed_dir, ignore_errors=True)


class BackendManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._cache = {}

    def _close_cached_unlocked(self):
        for preprocessor, model in self._cache.values():
            close_preprocessor = getattr(preprocessor, "close", None)
            if callable(close_preprocessor):
                try:
                    close_preprocessor()
                except Exception:
                    pass
            close_model = getattr(model, "close", None)
            if callable(close_model):
                try:
                    close_model()
                except Exception:
                    pass
        self._cache.clear()

    def close(self):
        with self._lock:
            self._close_cached_unlocked()

    def get(self, config: BackendConfig):
        key = (
            "server" if config.server_url else "async",
            config.server_url,
            config.served_model_name,
            str(Path(config.model_path).expanduser().resolve()),
            int(config.tp),
            int(config.max_pixels),
            int(config.request_timeout),
            int(config.http_max_retries),
            float(config.http_retry_backoff),
            int(config.server_max_inflight),
            int(config.preprocess_batch_size),
            bool(config.skip_preprocess),
        )
        with self._lock:
            if key not in self._cache:
                self._close_cached_unlocked()
                configure_runtime(config)
                if config.server_url:
                    model = MonkeyOCRv2_ServerParsing(
                        config.server_url,
                        model_name=config.served_model_name,
                        timeout=config.request_timeout,
                        http_max_retries=config.http_max_retries,
                        http_retry_backoff=config.http_retry_backoff,
                    )
                    print(
                        f"Using vLLM server backend: {config.server_url} model={config.served_model_name}"
                    )
                else:
                    warnings.warn(
                        "--server-url was not provided; using local vLLM AsyncLLMEngine as the "
                        f"fallback inference backend with model: {config.model_path}",
                        RuntimeWarning,
                    )
                    model = MonkeyOCRv2_AsyncParsing(
                        config.model_path,
                        tp=config.tp,
                        max_inflight=config.server_max_inflight,
                    )
                preprocessor = None
                if not config.skip_preprocess:
                    preprocessor = Preprocessor(
                        config.model_path, batch_size=config.preprocess_batch_size
                    )
                self._cache[key] = (preprocessor, model)
            return self._cache[key]


DEFAULT_BACKEND_MANAGER = BackendManager()
TASK_PROMPTS = {
    "text": ALL_PROMPT["Text"],
    "formula": ALL_PROMPT["Formula"],
    "table": ALL_PROMPT["Table"],
}


def configure_runtime(config: BackendConfig):
    os.environ["MOCR2_MAX_PIXELS"] = str(config.max_pixels)
    os.environ["MOCR2_SERVER_MAX_INFLIGHT"] = str(config.server_max_inflight)


def prepare_output_dirs(
    output_path: str | Path,
    *,
    skip_preprocess: bool,
    draw_layout: bool = False,
    use_base64: bool = False,
) -> OutputDirs:
    out_dir = Path(output_path).expanduser().resolve()
    json_dir = out_dir / "jsons"
    md_dir = out_dir / "markdowns"
    image_dir = out_dir / "images"
    preprocessed_dir = out_dir / "preprocessed"
    layout_dir = out_dir / "layouts" if draw_layout else None

    out_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)
    if not use_base64:
        image_dir.mkdir(parents=True, exist_ok=True)
    if not skip_preprocess:
        preprocessed_dir.mkdir(parents=True, exist_ok=True)
    if layout_dir is not None:
        layout_dir.mkdir(parents=True, exist_ok=True)

    return OutputDirs(out_dir, json_dir, md_dir, image_dir, preprocessed_dir, layout_dir)


def build_pipeline_args(config: PipelineConfig):
    backend = config.backend
    return SimpleNamespace(
        input_path=str(config.input_path),
        model_path=backend.model_path,
        tp=backend.tp,
        max_pixels=backend.max_pixels,
        server_url=backend.server_url,
        served_model_name=backend.served_model_name,
        request_timeout=backend.request_timeout,
        http_max_retries=backend.http_max_retries,
        http_retry_backoff=backend.http_retry_backoff,
        server_max_inflight=backend.server_max_inflight,
        page_max_inflight=config.page_max_inflight,
        preprocess_batch_size=backend.preprocess_batch_size,
        draw_layout=config.draw_layout,
        end2end=config.end2end,
        skip_processed=config.skip_processed,
        skip_preprocess=backend.skip_preprocess,
        retry_repeat=config.retry_repeat,
        retry_repeat_max_retries=config.retry_repeat_max_retries,
        keep_header_footer=config.keep_header_footer,
        use_base64=config.use_base64,
    )


def run_pipeline(
    config: PipelineConfig,
    *,
    backend_manager: BackendManager = DEFAULT_BACKEND_MANAGER,
):
    configure_runtime(config.backend)
    dirs = prepare_output_dirs(
        config.output_path,
        skip_preprocess=config.backend.skip_preprocess,
        draw_layout=config.draw_layout,
        use_base64=config.use_base64,
    )
    preprocessor, model = backend_manager.get(config.backend)
    args = build_pipeline_args(config)

    started = time.time()
    run_streaming_pipeline(
        args,
        preprocessor,
        model,
        dirs.out_dir,
        dirs.json_dir,
        dirs.md_dir,
        dirs.image_dir,
        show_progress_bar=config.show_progress_bar,
        verbose=config.verbose,
    )

    return {
        "out_dir": dirs.out_dir,
        "json_dir": dirs.json_dir,
        "md_dir": dirs.md_dir,
        "image_dir": dirs.image_dir,
        "elapsed": time.time() - started,
        "all_results_path": dirs.out_dir / "all_results.json",
    }


class _BatchCompletion:
    def __init__(self, size: int):
        self._remaining = size
        self._lock = threading.Lock()
        self._event = threading.Event()
        if size == 0:
            self._event.set()

    def done(self):
        with self._lock:
            self._remaining -= 1
            if self._remaining <= 0:
                self._event.set()

    def wait(self, stop_event: threading.Event):
        while not self._event.wait(0.2):
            if stop_event.is_set():
                raise RuntimeError("Service pipeline stopped while waiting for a parse batch.")


class _ServiceJob:
    def __init__(
        self,
        config: PipelineConfig,
        dirs: OutputDirs,
        single_task: str | None = None,
    ):
        self.config = config
        self.dirs = dirs
        self.single_task = single_task
        self.skip_preprocess = single_task is not None or config.backend.skip_preprocess
        self.future = Future()
        self.lock = threading.Lock()
        self.doc = None
        self.page_results = []
        self.single_outputs = []
        self.pending_pages = 0
        self.picture_counts = [0]
        self.failed = False
        self.started_at = time.time()

    def initialize(self, input_path: Path, page_count: int):
        with self.lock:
            self.doc = {
                "name": input_path.stem,
                "image_name": input_path.name,
                "image_path": input_path.name,
                "image_size": [None] * page_count,
                "pdf_pages": page_count,
            }
            self.page_results = [[] for _ in range(page_count)]
            self.single_outputs = [None] * page_count
            self.pending_pages = page_count

    def fail(self, exc: Exception):
        with self.lock:
            if self.failed or self.future.done():
                return False
            self.failed = True
            self.future.set_exception(exc)
            return True


class ServicePipelinePool:
    """Shared request scheduler used by the demo and API services."""

    def __init__(
        self,
        backend_config: BackendConfig,
        page_max_inflight: int,
        *,
        backend_manager: BackendManager = DEFAULT_BACKEND_MANAGER,
        batch_wait_seconds: float = 1.0,
        debug: bool = False,
    ):
        configure_runtime(backend_config)
        self.backend_config = backend_config
        self.page_window = max(1, int(page_max_inflight))
        self.batch_wait_seconds = max(0.0, float(batch_wait_seconds))
        self.debug = bool(debug)
        self.preprocessor, self.model = backend_manager.get(backend_config)
        self.stop_event = threading.Event()
        self._jobs_lock = threading.Lock()
        self._active_jobs = set()
        self._accepting_jobs = True
        self._closed = False
        self.request_q = queue.Queue()
        self.preprocess_q = queue.Queue(maxsize=self.page_window)
        self.parse_q = queue.Queue(maxsize=self.page_window)
        self.preprocess_slots = threading.BoundedSemaphore(self.page_window)
        self.parse_slots = threading.BoundedSemaphore(self.page_window)
        self.pdf_renderer = _PdfRenderer()
        self.page_executor = ThreadPoolExecutor(max_workers=self.page_window)
        self.output_executor = ThreadPoolExecutor(max_workers=max(1, min(4, self.page_window)))
        self._sentinel = object()
        self._threads = [
            threading.Thread(target=self._request_worker, name="mocr2-service-reader", daemon=True),
            threading.Thread(
                target=self._preprocess_worker, name="mocr2-service-preprocess", daemon=True
            ),
            threading.Thread(target=self._parse_worker, name="mocr2-service-parse", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def _report_error(self, stage: str, exc: Exception):
        if not self.debug:
            return
        print(
            f"[ServicePipelinePool:{stage}] {type(exc).__name__}: {exc}",
            flush=True,
        )
        traceback.print_exception(type(exc), exc, exc.__traceback__)

    def _fail_job(self, job: _ServiceJob, stage: str, exc: Exception):
        if job.fail(exc):
            self._report_error(stage, exc)

    def _remove_job(self, job: _ServiceJob):
        with self._jobs_lock:
            self._active_jobs.discard(job)

    def _register_job(self, job: _ServiceJob):
        with self._jobs_lock:
            if not self._accepting_jobs:
                raise RuntimeError("Service pipeline is shutting down.")
            self._active_jobs.add(job)
        job.future.add_done_callback(lambda _future: self._remove_job(job))

    def _put(self, target_q, item):
        while not self.stop_event.is_set():
            try:
                target_q.put(item, timeout=0.2)
                return
            except queue.Full:
                continue
        raise RuntimeError("Service pipeline is shutting down.")

    def _acquire_slot(self, slot):
        while not self.stop_event.is_set():
            if slot.acquire(timeout=0.2):
                return
        raise RuntimeError("Service pipeline is shutting down.")

    def _enqueue_parse(self, page, *, slot_reserved=False):
        if not slot_reserved:
            self._acquire_slot(self.parse_slots)
        try:
            self._put(self.parse_q, page)
        except Exception:
            self.parse_slots.release()
            raise

    def run(self, config: PipelineConfig):
        job = None
        try:
            if config.backend != self.backend_config:
                raise ValueError(
                    "ServicePipelinePool backend configuration cannot change per request."
                )
            if int(config.page_max_inflight) != self.page_window:
                raise ValueError(
                    "PipelineConfig.page_max_inflight must match the service pool page window: "
                    f"{config.page_max_inflight} != {self.page_window}"
                )
            dirs = prepare_output_dirs(
                config.output_path,
                # Service requests pass preprocessed PIL images directly to parsing.
                skip_preprocess=True,
                draw_layout=config.draw_layout,
                use_base64=config.use_base64,
            )
            job = _ServiceJob(config, dirs)
            self._register_job(job)
            self._put(self.request_q, job)
            return job.future.result()
        except Exception as exc:
            if job is not None and not job.future.done():
                job.fail(exc)
            if job is None or not job.failed:
                self._report_error("submit", exc)
            raise

    def run_single_task(self, input_path, output_path, task):
        task = task.lower()
        if task not in TASK_PROMPTS:
            raise ValueError(f"Unsupported task: {task}. Choose from: {', '.join(TASK_PROMPTS)}")
        config = PipelineConfig(
            input_path=str(input_path),
            output_path=str(output_path),
            backend=self.backend_config,
            page_max_inflight=self.page_window,
        )
        dirs = prepare_output_dirs(
            output_path,
            skip_preprocess=True,
            use_base64=True,
        )
        job = _ServiceJob(config, dirs, single_task=task)
        try:
            self._register_job(job)
            self._put(self.request_q, job)
            return job.future.result()
        except Exception as exc:
            if not job.future.done():
                job.fail(exc)
            raise

    def _create_read_state(self, job: _ServiceJob):
        input_path = Path(job.config.input_path)
        if not input_path.is_file() or input_path.suffix.lower() not in INPUT_EXTS:
            raise ValueError(f"Service parsing expects one PDF or image file: {input_path}")
        if input_path.suffix.lower() == ".pdf":
            try:
                import pypdfium2 as pdfium
            except Exception as exc:
                raise ImportError("Reading PDF files requires pypdfium2") from exc
            with PDFIUM_LOCK:
                pdf = pdfium.PdfDocument(str(input_path))
                try:
                    page_count = len(pdf)
                finally:
                    close_pdf = getattr(pdf, "close", None)
                    if callable(close_pdf):
                        close_pdf()
        else:
            page_count = 1
        if page_count == 0:
            raise ValueError(f"PDF contains no pages: {input_path}")
        job.initialize(input_path, page_count)
        return {
            "job": job,
            "input_path": input_path,
            "is_pdf": input_path.suffix.lower() == ".pdf",
            "page_count": page_count,
            "next_page": 0,
        }

    @staticmethod
    def _try_acquire_slot(slot) -> bool:
        return slot.acquire(blocking=False)

    def _submit_read(self, state) -> bool:
        if state["next_page"] >= state["page_count"]:
            return False
        job = state["job"]
        slot = self.parse_slots if job.skip_preprocess else self.preprocess_slots
        if not self._try_acquire_slot(slot):
            return False
        page_idx = state["next_page"]
        state["next_page"] += 1
        try:
            if state["is_pdf"]:
                image = self.pdf_renderer.render(state["input_path"], page_idx)
            else:
                image = load_image(str(state["input_path"]))
        except Exception:
            slot.release()
            raise

        page = {"job": job, "page_idx": page_idx, "image": image}
        if job.skip_preprocess:
            self._enqueue_parse(page, slot_reserved=True)
        else:
            try:
                self._put(self.preprocess_q, page)
            except Exception:
                self.preprocess_slots.release()
                raise
        return True

    def _request_worker(self):
        active = deque()
        while not self.stop_event.is_set():
            made_progress = False
            while True:
                try:
                    job = self.request_q.get_nowait()
                except queue.Empty:
                    break
                if job is self._sentinel:
                    continue
                try:
                    state = self._create_read_state(job)
                    active.append(state)
                    made_progress = True
                except Exception as exc:
                    self._fail_job(job, "request-reader", exc)

            for _ in range(len(active)):
                state = active.popleft()
                job = state["job"]
                try:
                    if job.failed:
                        continue
                    # One page per request per round keeps
                    # large PDFs from monopolizing the shared page window.
                    made_progress = self._submit_read(state) or made_progress
                    if state["next_page"] < state["page_count"]:
                        active.append(state)
                except Exception as exc:
                    self._fail_job(job, "request-reader", exc)

            if not made_progress:
                try:
                    job = self.request_q.get(timeout=0.02)
                except queue.Empty:
                    continue
                if job is not self._sentinel:
                    try:
                        state = self._create_read_state(job)
                        active.append(state)
                    except Exception as exc:
                        self._fail_job(job, "request-reader", exc)

    def _preprocess_worker(self):
        if self.preprocessor is None:
            return
        saw_sentinel = False
        while not self.stop_event.is_set() and not saw_sentinel:
            try:
                first = self.preprocess_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if first is self._sentinel:
                break
            batch = [first]
            deadline = time.monotonic() + self.batch_wait_seconds
            while len(batch) < self.page_window:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                try:
                    item = self.preprocess_q.get(timeout=timeout)
                except queue.Empty:
                    break
                if item is self._sentinel:
                    saw_sentinel = True
                    break
                batch.append(item)

            active = []
            for page in batch:
                if page["job"].failed:
                    self.preprocess_slots.release()
                else:
                    active.append(page)
            if not active:
                continue
            try:
                images = self.preprocessor.preprocess_images(
                    [page["image"] for page in active],
                    batch_size=self.backend_config.preprocess_batch_size,
                )
                if len(images) != len(active):
                    raise RuntimeError("Preprocessor returned an unexpected number of pages.")
                for page in active:
                    self.preprocess_slots.release()
                    page["preprocess_slot_released"] = True
                for page, image in zip(active, images):
                    page["image"] = image

                completion = _BatchCompletion(len(active))
                for page in active:
                    page["batch_completion"] = completion
                    self._enqueue_parse(page)
                completion.wait(self.stop_event)
            except Exception as exc:
                for page in active:
                    if not page.get("preprocess_slot_released"):
                        self.preprocess_slots.release()
                    self._fail_job(page["job"], "preprocess", exc)

    def _parse_worker(self):
        try:
            while not self.stop_event.is_set():
                try:
                    page = self.parse_q.get(timeout=0.2)
                except queue.Empty:
                    continue
                if page is self._sentinel:
                    break
                self.page_executor.submit(self._parse_page, page)
        except Exception as exc:
            self._report_error("parse-dispatcher", exc)
            self.stop_event.set()

    def _parse_page(self, page):
        job = page["job"]
        completion = page.get("batch_completion")
        try:
            if job.failed:
                return
            image = page["image"]
            page_idx = page["page_idx"]
            if job.single_task is not None:
                raw = self.model.batch_inference(
                    [image],
                    [TASK_PROMPTS[job.single_task]],
                    min_pixels=1003520,
                    max_tokens=4096 if job.single_task == "table" else None,
                )[0]
                output = _format_single_task_outputs(job.single_task, [raw])[0]
                self._complete_single_task_page(job, page_idx, output)
            elif job.config.end2end:
                if job.config.retry_repeat:
                    raw = batch_inference_with_repeat_retry(
                        self.model,
                        [image],
                        [ALL_PROMPT["END2END"]],
                        max_tokens=None,
                        max_retries=job.config.retry_repeat_max_retries,
                    )[0]
                else:
                    raw = self.model.batch_inference(
                        [image], [ALL_PROMPT["END2END"]], max_tokens=None
                    )[0]
                records, layouts = parse_end2end_output(raw, image.size)
                for record in records:
                    record["page_num"] = page_idx + 1
            else:
                layouts = get_layout(self.model, [image])[0]
                tasks = _build_page_tasks(page_idx, image, layouts)
                infer_tasks = [task for task in tasks if task["need_infer"]]
                if infer_tasks:
                    infer_images = [task["image"] for task in infer_tasks]
                    questions = [task["question"] for task in infer_tasks]
                    if job.config.retry_repeat:
                        outputs = batch_inference_with_repeat_retry(
                            self.model,
                            infer_images,
                            questions,
                            max_tokens=5000,
                            max_retries=job.config.retry_repeat_max_retries,
                        )
                    else:
                        outputs = self.model.batch_inference(
                            infer_images, questions, max_tokens=5000
                        )
                    raw_by_block = {
                        task["block_idx"]: raw for task, raw in zip(infer_tasks, outputs)
                    }
                else:
                    raw_by_block = {}
                with job.lock:
                    records = []
                    for task in tasks:
                        content = _format_block_content(
                            task,
                            raw_by_block.get(task["block_idx"], ""),
                            job.doc["name"],
                            job.picture_counts,
                            job.config.use_base64,
                            job.dirs.image_dir,
                        )
                        records.append(
                            {
                                "bbox": task["bbox"],
                                "label": task["label"],
                                "content": content,
                                "page_num": page_idx + 1,
                            }
                        )
            if job.single_task is None:
                self._complete_page(job, page_idx, image.size, records)
        except Exception as exc:
            self._fail_job(job, "parse-page", exc)
        finally:
            if completion is not None:
                completion.done()
            self.parse_slots.release()

    def _complete_page(self, job, page_idx, image_size, records):
        should_finalize = False
        with job.lock:
            if job.failed:
                return
            job.doc["image_size"][page_idx] = [int(image_size[0]), int(image_size[1])]
            job.page_results[page_idx] = records
            job.pending_pages -= 1
            should_finalize = job.pending_pages == 0
        if should_finalize:
            self.output_executor.submit(self._finalize_job, job)

    def _complete_single_task_page(self, job, page_idx, output):
        should_finalize = False
        with job.lock:
            if job.failed:
                return
            job.single_outputs[page_idx] = output
            job.pending_pages -= 1
            should_finalize = job.pending_pages == 0
        if should_finalize:
            self.output_executor.submit(self._finalize_single_task_job, job)

    def _finalize_single_task_job(self, job):
        if job.failed or job.future.done():
            return
        try:
            name = job.doc["name"]
            task = job.single_task
            outputs = list(job.single_outputs)
            md_path = job.dirs.md_dir / make_artifact_filename(name, f"_{task}_result.md")
            json_path = job.dirs.json_dir / make_artifact_filename(name, f"_{task}_result.json")
            md_path.write_text(_format_single_task_markdown(outputs), encoding="utf-8")
            json_path.write_text(
                json.dumps(
                    {
                        "image_name": job.doc["image_name"],
                        "image_path": job.doc["image_path"],
                        "task": task,
                        "outputs": outputs,
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            results = [
                {
                    "input_path": job.doc["image_path"],
                    "task": task,
                    "outputs": outputs,
                    "markdown_path": str(md_path),
                    "json_path": str(json_path),
                }
            ]
            all_results_path = job.dirs.out_dir / f"single_task_{task}_results.json"
            all_results_path.write_text(
                json.dumps(results, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
            job.future.set_result(
                {
                    "out_dir": job.dirs.out_dir,
                    "json_dir": job.dirs.json_dir,
                    "md_dir": job.dirs.md_dir,
                    "elapsed": time.time() - job.started_at,
                    "results": results,
                    "all_results_path": all_results_path,
                }
            )
        except Exception as exc:
            self._fail_job(job, "output-writer", exc)

    def _finalize_job(self, job):
        if job.failed or job.future.done():
            return
        try:
            with job.lock:
                results = [record for page in job.page_results for record in page]
                image_sizes = job.doc["image_size"]
                job.doc["image_size"] = image_sizes[0] if len(image_sizes) == 1 else image_sizes
                record = build_result_record(job.doc, results)
            name = job.doc["name"]
            (job.dirs.json_dir / make_artifact_filename(name, ".json")).write_text(
                json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8"
            )
            result2md(
                [name],
                [results],
                save_dir=str(job.dirs.md_dir),
                keep_header_footer=job.config.keep_header_footer,
            )
            all_results_path = job.dirs.out_dir / "all_results.json"
            all_results_path.write_text(
                json.dumps([record], ensure_ascii=False, indent=1), encoding="utf-8"
            )
            job.future.set_result(
                {
                    "out_dir": job.dirs.out_dir,
                    "json_dir": job.dirs.json_dir,
                    "md_dir": job.dirs.md_dir,
                    "image_dir": job.dirs.image_dir,
                    "elapsed": time.time() - job.started_at,
                    "all_results_path": all_results_path,
                }
            )
        except Exception as exc:
            self._fail_job(job, "output-writer", exc)

    def close(self):
        with self._jobs_lock:
            if self._closed:
                return
            self._closed = True
            self._accepting_jobs = False
            active_jobs = list(self._active_jobs)

        for job in active_jobs:
            self._fail_job(
                job,
                "shutdown",
                RuntimeError("Service pipeline is shutting down."),
            )

        self.stop_event.set()
        for target_q in (self.request_q, self.preprocess_q, self.parse_q):
            try:
                target_q.put_nowait(self._sentinel)
            except queue.Full:
                pass
        for thread in self._threads:
            thread.join(timeout=5)
        self.pdf_renderer.close()
        self.page_executor.shutdown(wait=True)
        self.output_executor.shutdown(wait=True)


def _list_single_task_inputs(input_path: str | Path):
    p = Path(input_path)
    files = [p] if p.is_file() else sorted([x for x in p.iterdir() if x.is_file()])
    return [x for x in files if x.suffix.lower() in INPUT_EXTS]


def _load_task_images(input_file: str | Path):
    input_file = Path(input_file)
    suffix = input_file.suffix.lower()
    if suffix == ".pdf":
        return load_pdf_images(str(input_file))
    if suffix in IMAGE_EXTS:
        return [load_image(str(input_file))]
    raise ValueError(f"Unsupported file type for single task recognition: {input_file}")


def _format_single_task_markdown(outputs: list[str]):
    if not outputs:
        return ""
    if len(outputs) == 1:
        content = (outputs[0] or "").strip()
    else:
        parts = []
        for idx, raw in enumerate(outputs, 1):
            parts.append(f"## Page {idx}\n\n{(raw or '').strip()}")
        content = "\n\n".join(parts).strip()
    return content + ("\n" if content else "")


def _format_single_task_outputs(task: str, outputs: list[str]) -> list[str]:
    label = {"text": "Text", "formula": "Formula", "table": "Table"}[task]
    formatted = []
    for page_idx, raw in enumerate(outputs):
        formatted.append(
            _format_block_content(
                {
                    "label": label,
                    "need_infer": True,
                    "page_idx": page_idx,
                    "image": None,
                },
                raw,
                "single_task",
                None,
                False,
                None,
            )
        )
    return formatted


def _run_single_task_with_model(input_path, output_path, task, model):
    task = task.lower()
    if task not in TASK_PROMPTS:
        raise ValueError(f"Unsupported task: {task}. Choose from: {', '.join(TASK_PROMPTS)}")

    out_dir = Path(output_path).expanduser().resolve()
    md_dir = out_dir / "markdowns"
    json_dir = out_dir / "jsons"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)
    json_dir.mkdir(parents=True, exist_ok=True)
    files = _list_single_task_inputs(input_path)
    if not files:
        raise ValueError(f"No supported input files found: {input_path}")

    started = time.time()
    results = []
    for file_path in files:
        images = _load_task_images(file_path)
        raw_outputs = model.batch_inference(
            images,
            [TASK_PROMPTS[task]] * len(images),
            min_pixels=1003520,
            max_tokens=4096 if task == "table" else None,
        )
        outputs = _format_single_task_outputs(task, raw_outputs)
        md_text = _format_single_task_markdown(outputs)
        md_path = md_dir / make_artifact_filename(file_path.stem, f"_{task}_result.md")
        json_path = json_dir / make_artifact_filename(file_path.stem, f"_{task}_result.json")
        md_path.write_text(md_text, encoding="utf-8")
        json_path.write_text(
            json.dumps(
                {
                    "image_name": file_path.name,
                    "image_path": str(file_path),
                    "task": task,
                    "outputs": outputs,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        results.append(
            {
                "input_path": str(file_path),
                "task": task,
                "outputs": outputs,
                "markdown_path": str(md_path),
                "json_path": str(json_path),
            }
        )

    all_results_path = out_dir / f"single_task_{task}_results.json"
    all_results_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return {
        "out_dir": out_dir,
        "json_dir": json_dir,
        "md_dir": md_dir,
        "elapsed": time.time() - started,
        "results": results,
        "all_results_path": all_results_path,
    }


def run_single_task_recognition(
    input_path: str | Path,
    output_path: str | Path,
    task: str,
    backend_config: BackendConfig,
    *,
    backend_manager: BackendManager = DEFAULT_BACKEND_MANAGER,
):
    configure_runtime(backend_config)
    _, model = backend_manager.get(backend_config)
    return _run_single_task_with_model(input_path, output_path, task, model)
