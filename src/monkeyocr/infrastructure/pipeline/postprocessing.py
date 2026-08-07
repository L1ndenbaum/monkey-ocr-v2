"""Layout parsing, recognition retry, and output formatting."""

import ast
import os
import re
from html import escape
from pathlib import Path

from PIL import Image, ImageDraw

from monkeyocr.infrastructure.pipeline.constants import ALL_PROMPT
from monkeyocr.infrastructure.pipeline.media import image_to_png_data_uri, save_picture_block
from monkeyocr.infrastructure.storage.artifacts import make_artifact_filename


def _extract_balanced_blocks(text: str, left: str, right: str):
    blocks = []
    depth = 0
    start = -1
    for index, char in enumerate(text):
        if char == left:
            if depth == 0:
                start = index
            depth += 1
        elif char == right and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                blocks.append(text[start : index + 1])
                start = -1
    return blocks


def _deduplicate_strings(values):
    return list(dict.fromkeys(values))


def _extract_tolerant_list_blocks(text: str):
    blocks = _extract_balanced_blocks(text, "[", "]")
    first = text.find("[")
    if first != -1:
        tail = text[first:].strip()
        missing = tail.count("[") - tail.count("]")
        if tail and missing > 0:
            blocks.append(tail + ("]" * missing))
    return _deduplicate_strings(blocks)


def _extract_tolerant_dict_blocks(text: str):
    blocks = _extract_balanced_blocks(text, "{", "}")
    for index, char in enumerate(text):
        if char != "{":
            continue
        depth = 0
        end = None
        for cursor in range(index, len(text)):
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
                if depth == 0:
                    end = cursor + 1
                    break
        blocks.append(text[index:end] if end is not None else text[index:] + ("}" * max(depth, 1)))
    return _deduplicate_strings(blocks)


def _parse_tolerant_items(text: str, normalize_item):
    text = (text or "").strip()
    if not text:
        return []

    def normalize_list(value):
        if not isinstance(value, list):
            return []
        return [item for raw in value if (item := normalize_item(raw)) is not None]

    try:
        complete = normalize_list(ast.literal_eval(text))
        if complete:
            return complete
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        pass

    best = []
    for block in _extract_tolerant_list_blocks(text):
        try:
            current = normalize_list(ast.literal_eval(block))
            if len(current) > len(best):
                best = current
        except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
            continue

    dict_items = []
    for block in _extract_tolerant_dict_blocks(text):
        try:
            item = normalize_item(ast.literal_eval(block))
            if item is not None:
                dict_items.append(item)
        except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
            continue
    return dict_items if len(dict_items) > len(best) else best


def _map_bbox_to_image(bbox, width: int, height: int):
    x1, y1, x2, y2 = bbox
    x1, x2 = x1 / 1000.0 * width, x2 / 1000.0 * width
    y1, y2 = y1 / 1000.0 * height, y2 / 1000.0 * height
    if x1 > x2:
        x1, x2 = x2, x1
    if y1 > y2:
        y1, y2 = y2, y1
    x1 = max(0, min(int(round(x1)), width - 1 if width > 0 else 0))
    y1 = max(0, min(int(round(y1)), height - 1 if height > 0 else 0))
    x2 = max(x1 + 1, min(int(round(x2)), width))
    y2 = max(y1 + 1, min(int(round(y2)), height))
    return [x1, y1, x2, y2]


def _normalize_model_item(item, include_content: bool):
    if not isinstance(item, dict) or "bbox" not in item or "label" not in item:
        return None
    bbox = item["bbox"]
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return None
    try:
        bbox = [float(value) for value in bbox]
    except (TypeError, ValueError):
        return None
    normalized = {
        "bbox": bbox,
        "label": item["label"] if isinstance(item["label"], str) else str(item["label"]),
    }
    if include_content:
        content = item.get("content", "")
        normalized["content"] = content if isinstance(content, str) else str(content or "")
    return normalized


def get_layout(model, images: list[Image.Image]):
    outputs = model.batch_inference(
        images,
        [ALL_PROMPT["LAYOUT"]] * len(images),
        min_pixels=1003520,
        max_tokens=4096,
    )
    page_layouts = []
    for image, output in zip(images, outputs):
        width, height = image.size
        items = _parse_tolerant_items(
            output,
            lambda item: _normalize_model_item(item, include_content=False),
        )
        page_layouts.append(
            [
                {
                    "bbox": _map_bbox_to_image(item["bbox"], width, height),
                    "label": item["label"],
                }
                for item in items
            ]
        )
    return page_layouts


def parse_end2end_output(text: str, image_size: tuple[int, int]) -> tuple[list[dict], list[dict]]:
    width, height = image_size
    records = []
    layouts = []
    items = _parse_tolerant_items(
        text,
        lambda item: _normalize_model_item(item, include_content=True),
    )
    for block_idx, item in enumerate(items):
        bbox = _map_bbox_to_image(item["bbox"], width, height)
        label = item["label"]
        records.append(
            {
                "bbox": bbox,
                "label": label,
                "content": (item.get("content") or "").strip(),
                "_block_idx": block_idx,
            }
        )
        layouts.append({"bbox": bbox, "label": label})
    return records, layouts


def otsl_to_html(otsl_str):
    if not otsl_str or not otsl_str.strip():
        return "<table></table>"

    rows_tokens = otsl_str.split("<nl>")
    if rows_tokens and rows_tokens[-1] == "":
        rows_tokens.pop()

    grid = []

    for r_idx, row_str in enumerate(rows_tokens):
        if not row_str.strip():
            if r_idx >= len(grid):
                grid.append([])
            continue

        parts = re.findall(r"<([a-z]+)>(.*?)(?=<[a-z]+>|$)", row_str)

        if r_idx >= len(grid):
            grid.append([])

        col_idx = 0

        for tag, content in parts:
            while True:
                while len(grid[r_idx]) <= col_idx:
                    grid[r_idx].append(None)

                if grid[r_idx][col_idx] is not None:
                    col_idx += 1
                else:
                    break

            if tag == "fcel" or tag == "ecel":
                text = content.strip() if tag == "fcel" else ""
                grid[r_idx][col_idx] = {"text": text, "rowspan": 1, "colspan": 1, "valid": True}
                col_idx += 1

            elif tag == "lcel":
                search_c = col_idx - 1
                found = False
                while search_c >= 0:
                    if len(grid[r_idx]) > search_c:
                        cell = grid[r_idx][search_c]
                        if cell and cell.get("valid"):
                            cell["colspan"] += 1
                            found = True
                            break
                    search_c -= 1

                if found:
                    grid[r_idx][col_idx] = {"valid": False, "type": "lcel"}
                else:
                    grid[r_idx][col_idx] = {"text": "", "rowspan": 1, "colspan": 1, "valid": True}
                col_idx += 1

            elif tag == "ucel":
                search_r = r_idx - 1
                found = False
                while search_r >= 0:
                    if len(grid[search_r]) > col_idx:
                        cell = grid[search_r][col_idx]
                        if cell and cell.get("valid"):
                            cell["rowspan"] += 1
                            found = True
                            break
                    search_r -= 1

                if found:
                    grid[r_idx][col_idx] = {"valid": False, "type": "ucel"}
                else:
                    grid[r_idx][col_idx] = {"text": "", "rowspan": 1, "colspan": 1, "valid": True}
                col_idx += 1

            elif tag == "xcel":
                grid[r_idx][col_idx] = {"valid": False, "type": "xcel"}
                col_idx += 1
            else:
                col_idx += 1

    html_parts = ["<table>"]

    for row in grid:
        html_parts.append("<tr>")
        for cell in row:
            if cell is None:
                continue
            elif cell.get("valid"):
                attrs = []
                if cell["rowspan"] > 1:
                    attrs.append(f'rowspan="{cell["rowspan"]}"')
                if cell["colspan"] > 1:
                    attrs.append(f'colspan="{cell["colspan"]}"')

                attr_str = " " + " ".join(attrs) if attrs else ""
                text = escape(cell["text"])
                html_parts.append(f"<td{attr_str}>{text}</td>")
        html_parts.append("</tr>")

    html_parts.append("</table>")
    return "".join(html_parts)


def process_formula(content: str):
    content = content.strip("$").strip()
    # Collapse repeated \quad sequences (>=5).
    content = re.sub(r"(?:\\quad\s*){5,}", r"\\quad ", content)

    # Collapse repeated \qquad sequences (>=5).
    content = re.sub(r"(?:\\qquad\s*){5,}", r"\\qquad ", content).strip()

    # Extract trailing (xxx). TODO: remove tag{}.
    match = re.search(
        r"(?:\\quad|\\qquad|\\eqno)\s*\(([^()]*)\)\s*$"
        r"|\\tag\{([^{}]*)\}\s*$",
        content,
    )

    extracted = None
    if match:
        extracted = match.group(1)
        content = content[: match.start()].rstrip()

    begin_env = None
    # Detect leading \begin{xx}.
    begin_match = re.match(r"^\\begin\{([^\}]+)\}", content)
    if begin_match:
        begin_env = begin_match.group(1)
        # Remove leading \begin{xx}.
        content = content[begin_match.end() :].lstrip()

        # Detect whether the matching \end{xx} is at the end.
        end_pattern = rf"\\end\{{{re.escape(begin_env)}\}}\s*$"
        end_match = re.search(end_pattern, content)
        if end_match:
            # Remove trailing \end{xx}.
            content = content[: end_match.start()].rstrip()

    # Extract trailing (xxx).
    match = re.search(
        r"(?:\\quad|\\qquad|\\eqno)\s*\(([^()]*)\)\s*$"
        r"|\\tag\{([^{}]*)\}\s*$",
        content,
    )

    if match:
        extracted = match.group(1)
        content = content[: match.start()].rstrip()

    # ===== Restore begin/end =====

    if begin_env:
        content = f"\\begin{{{begin_env}}}\n{content}\n\\end{{{begin_env}}}"

    return content, extracted


def detect_repeat_token(
    predicted_tokens: str,
    base_max_repeats: int = 4,
    window_size: int = 500,
    cut_from_end: int = 0,
    scaling_factor: float = 3.0,
):
    if cut_from_end > 0:
        predicted_tokens = predicted_tokens[:-cut_from_end]

    for seq_len in range(1, window_size // 2 + 1):
        candidate_seq = predicted_tokens[-seq_len:]
        max_repeats = int(base_max_repeats * (1 + scaling_factor / seq_len))

        repeat_count = 0
        pos = len(predicted_tokens) - seq_len
        if pos < 0:
            continue

        while pos >= 0:
            if predicted_tokens[pos : pos + seq_len] == candidate_seq:
                repeat_count += 1
                pos -= seq_len
            else:
                break

        if repeat_count > max_repeats:
            return True

    return False


def _should_retry_repeat_output(raw: str) -> bool:
    raw = raw or ""
    return detect_repeat_token(raw) or (len(raw) > 50 and detect_repeat_token(raw, cut_from_end=50))


def batch_inference_with_repeat_retry(
    model,
    infer_images: list[Image.Image],
    infer_questions: list[str],
    max_tokens: int | None = 5000,
    max_retries: int | None = None,
) -> list[str]:
    if not infer_images:
        return []
    if max_retries is None:
        max_retries = int(os.getenv("MOCR2_REC_MAX_RETRIES", "3"))

    outputs = model.batch_inference(infer_images, infer_questions, max_tokens=max_tokens)
    retry_indices = [i for i, raw in enumerate(outputs) if _should_retry_repeat_output(raw)]

    retries = 0
    while retry_indices and retries < max_retries:
        retry_temperature = min(0.2 * (retries + 1), 0.8)
        print(
            f"Detected repeat token in {len(retry_indices)} outputs, "
            f"retrying batch (attempt {retries + 1})..."
        )
        retry_images = [infer_images[i] for i in retry_indices]
        retry_questions = [infer_questions[i] for i in retry_indices]
        retry_outputs = model.batch_inference(
            retry_images,
            retry_questions,
            max_tokens=max_tokens,
            temperature=retry_temperature,
            top_p=0.95,
        )

        next_retry_indices = []
        for src_idx, raw in zip(retry_indices, retry_outputs):
            outputs[src_idx] = raw
            if _should_retry_repeat_output(raw):
                next_retry_indices.append(src_idx)
        retry_indices = next_retry_indices
        retries += 1

    return outputs


def _format_block_content(
    task: dict,
    raw: str,
    doc_name: str | None,
    picture_count: list[int] | None,
    use_base64: bool,
    image_dir: Path | None,
) -> str:
    label = task["label"]
    content = (raw or "").strip()
    if label == "Formula":
        content, extracted = process_formula(content)
        content = "$$\n" + content + "\n$$\n"
        if extracted:
            content = content + extracted
    elif label == "Table":
        content = content if os.getenv("MOCR2_TABLE_HTML", "0") == "1" else otsl_to_html(content)
    elif label == "Picture":
        if use_base64:
            image_ref = image_to_png_data_uri(task["image"])
        else:
            if image_dir is None:
                raise ValueError("image_dir is required when use_base64 is False")
            if picture_count is None:
                raise ValueError("picture_count is required for Picture blocks")
            sub_idx = picture_count[0]
            picture_count[0] += 1
            image_ref = save_picture_block(task["image"], image_dir, doc_name, sub_idx)
        content = f"![image]({image_ref})"
    elif label == "Title":
        content = "# " + content.replace("\n", "\n# ")
    elif label == "Section-header":
        content = "## " + content.replace("\n", "\n## ")
    elif not task["need_infer"]:
        content = ""
    return content


def _build_page_tasks(page_idx, image, layouts, doc_id=None):
    width, height = image.size
    tasks = []
    for block_idx, item in enumerate(layouts):
        x1, y1, x2, y2 = item["bbox"]
        x1 = max(0, min(int(round(x1)), max(0, width - 1)))
        y1 = max(0, min(int(round(y1)), max(0, height - 1)))
        x2 = max(x1 + 1, min(int(round(x2)), width))
        y2 = max(y1 + 1, min(int(round(y2)), height))
        label = item["label"]
        task = {
            "image": image.crop((x1, y1, x2, y2)),
            "bbox": [x1, y1, x2, y2],
            "label": label,
            "question": ALL_PROMPT.get(label, ""),
            "need_infer": label in ALL_PROMPT,
            "page_idx": page_idx,
            "page_num": page_idx + 1,
            "block_idx": block_idx,
        }
        if doc_id is not None:
            task["doc_id"] = doc_id
        tasks.append(task)
    return tasks


def _recognize_one_block(
    model,
    task: dict,
    enable_repeat_retry: bool,
    repeat_retry_max_retries: int | None,
) -> str:
    if not task["need_infer"]:
        return ""
    raw = model.batch_inference(
        [task["image"]],
        [task["question"]],
        max_tokens=5000,
        concurrency=1,
    )[0]
    if not enable_repeat_retry:
        return raw

    max_retries = repeat_retry_max_retries
    if max_retries is None:
        max_retries = int(os.getenv("MOCR2_REC_MAX_RETRIES", "3"))
    retries = 0
    while _should_retry_repeat_output(raw) and retries < max_retries:
        retry_temperature = min(0.2 * (retries + 1), 0.8)
        raw = model.batch_inference(
            [task["image"]],
            [task["question"]],
            max_tokens=5000,
            temperature=retry_temperature,
            top_p=0.95,
            concurrency=1,
        )[0]
        retries += 1
    return raw


def draw_layout_pdf(
    images: list[Image.Image], layouts_per_page: list[list[dict]], save_pdf_path: str
):
    vis_pages = []
    for img, items in zip(images, layouts_per_page):
        canvas = img.convert("RGB").copy()
        draw = ImageDraw.Draw(canvas)
        for i, it in enumerate(items):
            x1, y1, x2, y2 = it["bbox"]
            label = it.get("label", "")
            draw.rectangle([x1, y1, x2, y2], outline=(255, 0, 0), width=2)
            ty = max(0, y1 - 12)
            draw.text((x1, ty), str(i) + ": " + label, fill=(255, 0, 0))
        vis_pages.append(canvas)

    if not vis_pages:
        return
    os.makedirs(os.path.dirname(save_pdf_path), exist_ok=True)
    vis_pages[0].save(
        save_pdf_path, "PDF", resolution=100.0, save_all=True, append_images=vis_pages[1:]
    )


def result2md(
    names: list[str],
    results: list[list[dict]],
    save_dir: str | None = None,
    keep_header_footer: bool = False,
):
    md_list = []
    out_dir = None
    if save_dir:
        out_dir = Path(save_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

    for i, pdf_items in enumerate(results):
        lines = []
        for item in pdf_items:
            if not keep_header_footer and item.get("label") in {"Page-header", "Page-footer"}:
                continue
            content = (item.get("content") or "").strip()
            if content:
                lines.append(content)

        md = "\n\n".join(lines).strip() + ("\n" if lines else "")
        md = md.replace("�", "")  # Remove invalid characters
        md_list.append(md)

        if out_dir is not None:
            (out_dir / make_artifact_filename(names[i], ".md")).write_text(md, encoding="utf-8")

    return md_list
