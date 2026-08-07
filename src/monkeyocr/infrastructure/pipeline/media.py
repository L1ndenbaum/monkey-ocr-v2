"""PDF/image loading and bounded document iteration."""

import base64
import os
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Union

import requests
from PIL import Image, ImageFile, ImageOps

from monkeyocr.infrastructure.pipeline.constants import INPUT_EXTS, PDFIUM_LOCK
from monkeyocr.infrastructure.storage.artifacts import make_artifact_filename


def image_to_png_data_uri(image: Image.Image) -> str:
    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def save_picture_block(image: Image.Image, image_dir: Path, doc_name: str, sub_idx: int) -> str:
    image_dir.mkdir(parents=True, exist_ok=True)
    image_name = make_artifact_filename(doc_name, f"_sub{sub_idx}.jpg")
    image.convert("RGB").save(image_dir / image_name, format="JPEG", quality=95)
    return f"../images/{image_name}"


def save_preprocessed_page(
    image: Image.Image, preprocessed_dir: Path, doc_name: str, page_idx: int
) -> str:
    path = get_preprocessed_page_path(preprocessed_dir, doc_name, page_idx)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, format="PNG", compress_level=1)
    return str(path)


def get_preprocessed_page_path(preprocessed_dir: Path, doc_name: str, page_idx: int) -> Path:
    return preprocessed_dir / doc_name / f"page_{page_idx + 1:03}.png"


def _render_pdf_page(pdf, page_idx: int) -> Image.Image:
    with PDFIUM_LOCK:
        page = pdf[page_idx]
        try:
            bitmap = page.render(scale=200 / 72)
            try:
                return bitmap.to_pil().convert("RGB")
            finally:
                close_bitmap = getattr(bitmap, "close", None)
                if callable(close_bitmap):
                    close_bitmap()
        finally:
            close_page = getattr(page, "close", None)
            if callable(close_page):
                close_page()


def load_pdf_images(pdf_path: str):
    try:
        import pypdfium2 as pdfium
    except Exception as e:
        raise ImportError("Reading PDF files requires pypdfium2") from e

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(pdf_path)
        try:
            return [_render_pdf_page(pdf, page_idx) for page_idx in range(len(pdf))]
        finally:
            close_pdf = getattr(pdf, "close", None)
            if callable(close_pdf):
                close_pdf()


class _PdfRenderer:
    def __init__(self, max_open_documents: int = 16):
        self.max_open_documents = max(1, int(max_open_documents))
        self._cache = OrderedDict()

    @staticmethod
    def _close_handle(pdf):
        close_pdf = getattr(pdf, "close", None)
        if callable(close_pdf):
            close_pdf()

    def render(self, pdf_path: str | Path, page_idx: int) -> Image.Image:
        try:
            import pypdfium2 as pdfium
        except Exception as exc:
            raise ImportError("Reading PDF files requires pypdfium2") from exc

        with PDFIUM_LOCK:
            key = str(Path(pdf_path).resolve())
            pdf = self._cache.pop(key, None)
            if pdf is None:
                pdf = pdfium.PdfDocument(key)
            self._cache[key] = pdf
            while len(self._cache) > self.max_open_documents:
                _, stale_pdf = self._cache.popitem(last=False)
                self._close_handle(stale_pdf)
            return _render_pdf_page(pdf, page_idx)

    def close(self):
        with PDFIUM_LOCK:
            handles = list(self._cache.values())
            self._cache.clear()
            for pdf in handles:
                try:
                    self._close_handle(pdf)
                except Exception:
                    pass


def _is_jpeg_source(source) -> bool:
    if source is None:
        return False
    source = str(source).lower()
    return source.endswith((".jpg", ".jpeg")) or ".jpg?" in source or ".jpeg?" in source


def _apply_jpeg_orientation(img: Image.Image, source=None) -> Image.Image:
    if _is_jpeg_source(source) or (getattr(img, "format", None) or "").upper() == "JPEG":
        return ImageOps.exif_transpose(img)
    return img


def open_oriented_image(image_path: Union[str, Path]) -> Image.Image:
    img = Image.open(image_path)
    return _apply_jpeg_orientation(img, image_path)


def load_image_from_base64(image: Union[bytes, str]) -> Image.Image:
    """load image from base64 format."""
    return Image.open(BytesIO(base64.b64decode(image)))


def load_image(
    image_url: Union[str, Path, Image.Image],
    max_pixels: int = None,
    min_pixels: int = None,
    max_size: int = None,
    min_size: int = None,
    resize: int = None,
) -> Image.Image:
    """load image from url, local path or openai GPT4V."""
    FETCH_TIMEOUT = int(os.environ.get("LMDEPLOY_FETCH_TIMEOUT", 10))
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"
    }
    try:
        ImageFile.LOAD_TRUNCATED_IMAGES = True
        if isinstance(image_url, Image.Image):
            img = _apply_jpeg_orientation(image_url)
        else:
            image_source = str(image_url)
        if isinstance(image_url, Image.Image):
            pass
        elif image_source.startswith("http"):
            response = requests.get(image_source, headers=headers, timeout=FETCH_TIMEOUT)
            response.raise_for_status()
            img = Image.open(BytesIO(response.content))
            img = _apply_jpeg_orientation(img, image_source)
        elif image_source.startswith("data:image"):
            img = load_image_from_base64(image_source.split(",")[1])
            img = _apply_jpeg_orientation(img, image_source)
        else:
            # Load image from local path
            img = open_oriented_image(image_source)

        # check image valid
        img = img.convert("RGB")
        if resize:
            img = img.resize([img.size[0] * 2, img.size[1] * 2], Image.LANCZOS)

        # resize image if too small
        if min_pixels and img.size[0] * img.size[1] < min_pixels:
            scale = (min_pixels / (img.size[0] * img.size[1])) ** 0.5
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = img.resize(new_size, Image.LANCZOS)
        if min_size and min(img.size) < min_size:
            scale = min_size / min(img.size)
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = img.resize(new_size, Image.LANCZOS)

        # resize image if too large
        if max_pixels and img.size[0] * img.size[1] > max_pixels:
            scale = (max_pixels / (img.size[0] * img.size[1])) ** 0.5
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = img.resize(new_size, Image.LANCZOS)
        elif max_size and max(img.size) > max_size:
            scale = max_size / max(img.size)
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = img.resize(new_size, Image.LANCZOS)

        if max(img.size[0], img.size[1]) / min(img.size[0], img.size[1]) > 200:
            img = Image.new("RGB", (32, 32))
    except Exception as error:
        if isinstance(image_url, str) and len(image_url) > 100:
            image_url = image_url[:100] + " ..."
        print(f"--------{error}, image_url={image_url}")
        # use dummy image
        img = Image.new("RGB", (32, 32))

    return img


def _list_input_files(input_path: str):
    p = Path(input_path)
    return [p] if p.is_file() else sorted([x for x in p.iterdir() if x.is_file()])


def _count_pending_documents(input_path: str, md_dir: Path, skip_processed: bool) -> int:
    total = 0
    for f in _list_input_files(input_path):
        if f.suffix.lower() not in INPUT_EXTS:
            continue
        if skip_processed and (md_dir / make_artifact_filename(f.stem, ".md")).exists():
            continue
        total += 1
    return total


def _count_pending_pages(input_path: str, md_dir: Path, skip_processed: bool) -> int:
    total = 0
    for f in _list_input_files(input_path):
        ext = f.suffix.lower()
        if ext not in INPUT_EXTS:
            continue
        if skip_processed and (md_dir / make_artifact_filename(f.stem, ".md")).exists():
            continue
        if ext == ".pdf":
            try:
                import pypdfium2 as pdfium
            except ImportError:
                total += 1
                continue
            with PDFIUM_LOCK:
                pdf = pdfium.PdfDocument(str(f))
                try:
                    total += len(pdf)
                finally:
                    close = getattr(pdf, "close", None)
                    if close is not None:
                        close()
        else:
            total += 1
    return total


def _iter_input_page_events(
    input_path: str,
    md_dir: Path,
    skip_processed: bool,
    acquire_page_slot,
    release_page_slot,
    preprocessed_dir: Path | None = None,
):
    doc_id = 0
    for input_file in _list_input_files(input_path):
        ext = input_file.suffix.lower()
        if ext not in INPUT_EXTS:
            continue
        if skip_processed and (md_dir / make_artifact_filename(input_file.stem, ".md")).exists():
            yield ("skipped",)
            continue

        if ext == ".pdf":
            try:
                import pypdfium2 as pdfium
            except Exception as exc:
                raise ImportError("Reading PDF files requires pypdfium2") from exc
            with PDFIUM_LOCK:
                pdf = pdfium.PdfDocument(str(input_file))
            try:
                with PDFIUM_LOCK:
                    page_count = len(pdf)
                yield (
                    "doc",
                    doc_id,
                    {
                        "name": input_file.stem,
                        "image_name": input_file.name,
                        "image_path": str(input_file),
                        "pdf_pages": page_count,
                    },
                )
                for page_idx in range(page_count):
                    cached_path = None
                    if preprocessed_dir is not None:
                        cached_path = get_preprocessed_page_path(
                            preprocessed_dir, input_file.stem, page_idx
                        )
                    if cached_path is not None and skip_processed and cached_path.exists():
                        yield ("page", doc_id, page_idx, None, str(cached_path), False)
                        continue
                    if not acquire_page_slot():
                        return
                    try:
                        image = _render_pdf_page(pdf, page_idx)
                    except Exception:
                        release_page_slot()
                        raise
                    yield (
                        "page",
                        doc_id,
                        page_idx,
                        image,
                        str(cached_path) if cached_path is not None else None,
                        True,
                    )
            finally:
                with PDFIUM_LOCK:
                    close_pdf = getattr(pdf, "close", None)
                    if callable(close_pdf):
                        close_pdf()
        else:
            yield (
                "doc",
                doc_id,
                {
                    "name": input_file.stem,
                    "image_name": input_file.name,
                    "image_path": str(input_file),
                    "pdf_pages": 1,
                },
            )
            cached_path = None
            if preprocessed_dir is not None:
                cached_path = get_preprocessed_page_path(preprocessed_dir, input_file.stem, 0)
            if cached_path is not None and skip_processed and cached_path.exists():
                yield ("page", doc_id, 0, None, str(cached_path), False)
            else:
                if not acquire_page_slot():
                    return
                try:
                    image = load_image(str(input_file))
                except Exception:
                    release_page_slot()
                    raise
                yield (
                    "page",
                    doc_id,
                    0,
                    image,
                    str(cached_path) if cached_path is not None else None,
                    True,
                )
        doc_id += 1


def _doc_image_size(images: list[Image.Image]):
    sizes = [[int(img.size[0]), int(img.size[1])] for img in images]
    return sizes[0] if len(sizes) == 1 else sizes


def build_result_record(doc: dict, layouts: list[dict]):
    return {
        "image_name": doc.get("image_name") or f"{doc.get('name', '')}",
        "image_path": doc.get("image_path") or "",
        "image_size": doc.get("image_size") or _doc_image_size(doc.get("images", [])),
        "layouts": layouts,
    }
