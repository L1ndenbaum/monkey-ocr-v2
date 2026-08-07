"""OCR application use cases."""

from monkeyocr.application.commands import ParseDocumentCommand, RecognizeImageCommand
from monkeyocr.application.ports import OcrPipeline
from monkeyocr.domain.media import classify_media
from monkeyocr.domain.models import ParsedDocument, RecognizedContent


class ParseDocument:
    def __init__(self, pipeline: OcrPipeline) -> None:
        self._pipeline = pipeline

    def execute(self, command: ParseDocumentCommand) -> ParsedDocument:
        classify_media(command.input_path.suffix)
        artifact_name, files = self._pipeline.parse(command.input_path, command.output_dir)
        return ParsedDocument(
            request_id=command.request_id,
            artifact_name=artifact_name,
            files=files,
        )


class RecognizeImage:
    def __init__(self, pipeline: OcrPipeline) -> None:
        self._pipeline = pipeline

    def execute(self, command: RecognizeImageCommand) -> RecognizedContent:
        classify_media(command.input_path.suffix, images_only=True)
        content = self._pipeline.recognize(
            command.input_path,
            command.output_dir,
            command.task,
        )
        return RecognizedContent(
            request_id=command.request_id,
            task=command.task,
            content=content,
        )
