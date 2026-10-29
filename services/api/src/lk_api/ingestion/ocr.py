"""Replaceable OCR adapters."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Protocol

import pymupdf


@dataclass(frozen=True)
class OcrResult:
    text: str
    confidence: float | None


class OcrUnavailable(Exception):
    pass


class OcrEngine(Protocol):
    name: str

    def available(self) -> bool: ...

    def ocr_page(self, pdf_bytes: bytes, page_number: int) -> OcrResult: ...


class NoOcr:
    name = "none"

    def available(self) -> bool:
        return False

    def ocr_page(self, pdf_bytes: bytes, page_number: int) -> OcrResult:
        raise OcrUnavailable("OCR is disabled (LK_OCR_ENGINE=none)")


class TesseractOcr:
    """Uses PyMuPDF's Tesseract integration. Requires the tesseract binary and tessdata."""

    name = "tesseract"

    def __init__(self, language: str = "eng", dpi: int = 300) -> None:
        self.language, self.dpi = language, dpi

    def available(self) -> bool:
        return shutil.which("tesseract") is not None

    def ocr_page(self, pdf_bytes: bytes, page_number: int) -> OcrResult:
        if not self.available():
            raise OcrUnavailable("tesseract binary not found")
        with pymupdf.open(stream=pdf_bytes, filetype="pdf") as doc:
            page = doc[page_number - 1]
            textpage = page.get_textpage_ocr(language=self.language, dpi=self.dpi, full=True)
            text = page.get_text("text", textpage=textpage)
        # PyMuPDF does not expose a page-level confidence; record None rather than invent one.
        return OcrResult(text=text, confidence=None)


class StaticOcr:
    """Test double: returns pre-recorded text for specific page numbers."""

    name = "static-test"

    def __init__(self, pages: dict[int, str]) -> None:
        self.pages = pages

    def available(self) -> bool:
        return True

    def ocr_page(self, pdf_bytes: bytes, page_number: int) -> OcrResult:
        return OcrResult(text=self.pages.get(page_number, ""), confidence=None)


def build_ocr(engine: str) -> OcrEngine:
    return TesseractOcr() if engine == "tesseract" else NoOcr()
