"""Layout capture for provenance (E7, plans/P2.md section 6.2-6.3).

Pure data structures and geometry helpers; no OCR, no database, no imports from the lab engine.

- ``LayoutToken`` / ``LayoutLine`` / ``PageLayout``: what a page looked like, in displayed-page space
  (PDF points, top-left origin, rotation applied) or, for images, pixels of the EXIF-transposed image.
- ``align_native_lines``: pairs the non-empty lines of a native page's text with the word-box lines.
- ``DocumentLayout``: the physical (non-empty, stripped) lines of the whole report text, numbered from 0 in
  document order, each with its page and its ``LayoutLine`` when one could be found. The tokenizer's traced
  parse reports which physical line ids a row came from; ``DocumentLayout`` turns ids into page, bbox, source
  text and reading confidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class LayoutToken:
    text: str
    bbox: BBox
    conf: float = 1.0


@dataclass
class LayoutLine:
    text: str
    bbox: BBox
    tokens: list[LayoutToken] = field(default_factory=list)

    @property
    def min_conf(self) -> float:
        return min((t.conf for t in self.tokens), default=1.0)


@dataclass
class PageLayout:
    n: int                       # 1-based
    width: float
    height: float
    unit: str                    # "pt" (PDF) | "px" (image)
    method: str                  # "text" | "ocr"
    rotation: int = 0
    lines: list[LayoutLine] = field(default_factory=list)

    def meta(self) -> dict[str, Any]:
        return {"n": self.n, "width": self.width, "height": self.height, "unit": self.unit,
                "method": self.method, "rotation": self.rotation}


@dataclass
class PhysicalLine:
    id: int
    page: Optional[int]
    text: str
    line: Optional[LayoutLine] = None


@dataclass
class RowGeometry:
    page: Optional[int]
    bbox: Optional[BBox]
    source_text: str
    confidence: Optional[float]      # minimum token confidence over the located lines; None if none located


def union_bbox(boxes: Iterable[Sequence[float]]) -> Optional[BBox]:
    xs0: list[float] = []
    ys0: list[float] = []
    xs1: list[float] = []
    ys1: list[float] = []
    for b in boxes:
        xs0.append(float(b[0]))
        ys0.append(float(b[1]))
        xs1.append(float(b[2]))
        ys1.append(float(b[3]))
    if not xs0:
        return None
    return (min(xs0), min(ys0), max(xs1), max(ys1))


def quad_to_bbox(quad: Sequence[Sequence[float]], scale: float = 1.0) -> BBox:
    """A 4-point OCR polygon -> axis-aligned [x0, y0, x1, y1], multiplied by ``scale``."""
    xs = [float(p[0]) for p in quad]
    ys = [float(p[1]) for p in quad]
    return (min(xs) * scale, min(ys) * scale, max(xs) * scale, max(ys) * scale)


def norm_text(text: str) -> str:
    return " ".join((text or "").split())


def align_native_lines(text_lines: Sequence[str], layout_lines: Sequence[LayoutLine],
                       lookahead: int = 5) -> list[Optional[LayoutLine]]:
    """Pair each text line with a layout line by whitespace-normalised equality.

    Two pointers over document order; a text line may skip at most ``lookahead`` layout lines. A text line with
    no partner gets None (no geometry; the row is then flagged NR-NOLOCATE, which is the safe outcome)."""
    out: list[Optional[LayoutLine]] = []
    j = 0
    norm_layout = [norm_text(ln.text) for ln in layout_lines]
    for text in text_lines:
        t = norm_text(text)
        found: Optional[int] = None
        for k in range(j, min(j + lookahead + 1, len(layout_lines))):
            if norm_layout[k] == t:
                found = k
                break
        if found is None:
            out.append(None)
        else:
            out.append(layout_lines[found])
            j = found + 1
    return out


def stripped_lines(text: str) -> list[str]:
    return [ln for ln in (raw.strip() for raw in (text or "").splitlines()) if ln]


@dataclass
class DocumentLayout:
    kind: str = "unknown"                       # "pdf" | "image" | "csv" | "unknown"
    pages: list[PageLayout] = field(default_factory=list)
    physical: list[PhysicalLine] = field(default_factory=list)

    @classmethod
    def build(cls, kind: str, raw_text: str, page_texts: Sequence[str],
              page_layouts: Sequence[Optional[PageLayout]]) -> "DocumentLayout":
        """Number the physical lines of ``raw_text`` and attach geometry page by page.

        Geometry is attached only when the per-page text lines concatenate to exactly the report's physical
        lines (pages are separated by blank lines, so this holds for every PDF and image read); otherwise the
        lines keep no geometry rather than a wrong one."""
        phys_text = stripped_lines(raw_text)
        pages = [p for p in page_layouts if p is not None]
        per_page = [stripped_lines(t) for t in page_texts]
        flat = [ln for lines in per_page for ln in lines]
        physical: list[PhysicalLine] = []
        if flat == phys_text and len(page_texts) == len(page_layouts):
            for pg_text_lines, layout in zip(per_page, page_layouts):
                if layout is None:
                    physical.extend(PhysicalLine(len(physical) + i, None, t) for i, t in enumerate(pg_text_lines))
                    continue
                aligned = align_native_lines(pg_text_lines, layout.lines)
                for t, ln in zip(pg_text_lines, aligned):
                    physical.append(PhysicalLine(len(physical), layout.n, t, ln))
        else:
            physical = [PhysicalLine(i, None, t) for i, t in enumerate(phys_text)]
        return cls(kind=kind, pages=pages, physical=physical)

    def page_meta(self) -> list[dict[str, Any]]:
        return [p.meta() for p in self.pages]

    def page(self, n: int) -> Optional[PageLayout]:
        return next((p for p in self.pages if p.n == n), None)

    def locate(self, line_ids: Sequence[int]) -> RowGeometry:
        """Page, bbox (union on the first located line's page), source text and reading confidence of a row."""
        found = [self.physical[i] for i in line_ids if 0 <= i < len(self.physical)]
        text = " | ".join(p.text for p in found)
        located = [p for p in found if p.line is not None and p.page is not None]
        if not located:
            return RowGeometry(None, None, text, None)
        page = located[0].page
        on_page = [p for p in located if p.page == page]
        bbox = union_bbox(p.line.bbox for p in on_page)  # type: ignore[union-attr]
        conf = min(p.line.min_conf for p in located)  # type: ignore[union-attr]
        return RowGeometry(page, bbox, text, conf)

    def find_line(self, *needles: str) -> Optional[PhysicalLine]:
        """First physical line (document order) whose normalised text contains every needle (case-insensitive)."""
        wanted = [norm_text(n).lower() for n in needles if norm_text(n)]
        for p in self.physical:
            low = norm_text(p.text).lower()
            if wanted and all(w in low for w in wanted):
                return p
        return None
