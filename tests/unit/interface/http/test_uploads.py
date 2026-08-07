from pathlib import Path

import pytest
from pypdf import PdfWriter

from monkeyocr.domain.errors import PageLimitExceededError
from monkeyocr.interface.http.uploads import validate_pdf_page_limit


@pytest.mark.asyncio
async def test_pdf_page_limit_uses_actual_page_count(tmp_path: Path) -> None:
    pdf = tmp_path / "two-pages.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.add_blank_page(width=100, height=100)
    with pdf.open("wb") as stream:
        writer.write(stream)

    with pytest.raises(PageLimitExceededError, match="2 pages"):
        await validate_pdf_page_limit(pdf, limit=1)
