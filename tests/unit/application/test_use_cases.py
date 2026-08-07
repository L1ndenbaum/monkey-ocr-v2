from pathlib import Path

import pytest

from monkeyocr.application.commands import ParseDocumentCommand, RecognizeImageCommand
from monkeyocr.application.use_cases import ParseDocument, RecognizeImage
from monkeyocr.domain.errors import UnsupportedMediaTypeError
from monkeyocr.domain.tasks import OcrTask


class FakePipeline:
    def parse(self, input_path: Path, output_dir: Path) -> tuple[str, tuple[str, ...]]:
        assert input_path == Path("input.pdf")
        assert output_dir == Path("results/request-1")
        return "input_results.zip", ("input.md", "input.json")

    def recognize(self, input_path: Path, output_dir: Path, task: OcrTask) -> str:
        assert input_path == Path("formula.png")
        assert output_dir == Path("results/request-2")
        assert task is OcrTask.FORMULA
        return "x^2"


def test_parse_document_maps_pipeline_result() -> None:
    result = ParseDocument(FakePipeline()).execute(
        ParseDocumentCommand(
            request_id="request-1",
            input_path=Path("input.pdf"),
            output_dir=Path("results/request-1"),
        )
    )

    assert result.request_id == "request-1"
    assert result.artifact_name == "input_results.zip"
    assert result.files == ("input.md", "input.json")


def test_recognize_image_rejects_pdf() -> None:
    with pytest.raises(UnsupportedMediaTypeError):
        RecognizeImage(FakePipeline()).execute(
            RecognizeImageCommand(
                request_id="request-2",
                input_path=Path("formula.pdf"),
                output_dir=Path("results/request-2"),
                task=OcrTask.FORMULA,
            )
        )


def test_recognize_image_returns_content() -> None:
    result = RecognizeImage(FakePipeline()).execute(
        RecognizeImageCommand(
            request_id="request-2",
            input_path=Path("formula.png"),
            output_dir=Path("results/request-2"),
            task=OcrTask.FORMULA,
        )
    )

    assert result.content == "x^2"
    assert result.task is OcrTask.FORMULA
