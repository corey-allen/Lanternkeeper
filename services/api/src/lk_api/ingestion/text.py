"""Text normalisation and passage splitting. Original text is always kept alongside."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .pdf import PageExtraction, TextBlock

_HYPHEN_BREAK = re.compile(r"(\w)-\n(\w)")
_SPACES = re.compile(r"[ \t ]+")
_AREA = re.compile(r"^AREA\s+([A-Z0-9]+)\s*[:.\-]\s*(.+)$", re.IGNORECASE)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _HYPHEN_BREAK.sub(r"\1\2", text)
    lines = [_SPACES.sub(" ", line).strip() for line in text.splitlines()]
    out, blank = [], False
    for line in lines:
        if not line:
            if not blank and out:
                out.append("")
            blank = True
            continue
        out.append(line)
        blank = False
    # join wrapped lines inside a paragraph
    paragraphs = "\n".join(out).split("\n\n")
    return "\n\n".join(" ".join(p.split("\n")) for p in paragraphs).strip()


def normalize_key(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    text = re.sub(r"^(the|a|an)\s+", "", text.strip())
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


@dataclass
class PassageDraft:
    page_number: int
    ordinal: int
    heading: str
    raw_text: str
    normalized_text: str
    bbox: list[float] | None
    extraction_method: str
    injection_flags: list[str] = field(default_factory=list)


def blocks_from_ocr_text(text: str) -> list[TextBlock]:
    return [TextBlock(p.strip(), None) for p in re.split(r"\n\s*\n", text) if p.strip()]


def split_passages(pages: list[tuple[PageExtraction, str, list[TextBlock]]]) -> list[PassageDraft]:
    """One passage per paragraph block; the most recent AREA heading carries across pages."""
    drafts: list[PassageDraft] = []
    heading = ""
    ordinal = 0
    for page, method, blocks in pages:
        for block in blocks:
            norm = normalize(block.text)
            if not norm:
                continue
            match = _AREA.match(norm.split("\n")[0])
            if match:
                heading = norm.split("\n")[0][:300]
            ordinal += 1
            drafts.append(PassageDraft(
                page_number=page.page_number, ordinal=ordinal, heading=heading, raw_text=block.text,
                normalized_text=norm, bbox=list(block.bbox) if block.bbox else None,
                extraction_method=method,
            ))
    return drafts
