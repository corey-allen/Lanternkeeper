"""PDF validation and per-page text extraction with block coordinates (PyMuPDF)."""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf


class PdfRejected(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class TextBlock:
    text: str
    bbox: tuple[float, float, float, float] | None


@dataclass
class PageExtraction:
    page_number: int
    width: float
    height: float
    blocks: list[TextBlock] = field(default_factory=list)
    image_count: int = 0

    @property
    def text(self) -> str:
        return "\n\n".join(b.text for b in self.blocks)


def inspect_pdf(data: bytes, *, max_pages: int) -> int:
    """Validate that bytes are a readable, unencrypted PDF within limits. Returns page count."""
    if not data.startswith(b"%PDF-"):
        raise PdfRejected("NOT_A_PDF", "File is not a PDF")
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - library raises various types
        raise PdfRejected("PDF_UNREADABLE", "PDF could not be parsed") from exc
    with doc:
        if doc.needs_pass or doc.is_encrypted:
            raise PdfRejected("PDF_ENCRYPTED", "Encrypted PDFs are not supported")
        if doc.page_count < 1:
            raise PdfRejected("PDF_EMPTY", "PDF has no pages")
        if doc.page_count > max_pages:
            raise PdfRejected("PDF_TOO_MANY_PAGES", f"PDF has more than {max_pages} pages")
        return doc.page_count


def extract_pages(data: bytes, *, max_pages: int) -> list[PageExtraction]:
    pages: list[PageExtraction] = []
    with pymupdf.open(stream=data, filetype="pdf") as doc:
        for index in range(min(doc.page_count, max_pages)):
            page = doc[index]
            extraction = PageExtraction(page_number=index + 1, width=page.rect.width,
                                        height=page.rect.height, image_count=len(page.get_images()))
            for block in page.get_text("blocks", sort=True):
                x0, y0, x1, y1, text, _no, block_type = block[:7]
                if block_type != 0:  # 1 = image block
                    continue
                cleaned = text.strip()
                if cleaned:
                    extraction.blocks.append(TextBlock(cleaned, (round(x0, 1), round(y0, 1),
                                                                 round(x1, 1), round(y1, 1))))
            pages.append(extraction)
    return pages
