"""OCR task vocabulary and prompt policy."""

from enum import StrEnum


class OcrTask(StrEnum):
    TEXT = "text"
    FORMULA = "formula"
    TABLE = "table"


TASK_PROMPTS: dict[OcrTask, str] = {
    OcrTask.TEXT: "Please output the text in the image.",
    OcrTask.FORMULA: (
        "Please write out the expression of the formula in the image using LaTeX format."
    ),
    OcrTask.TABLE: "Please extract the table from the image and represent it in OTSL format.",
}
