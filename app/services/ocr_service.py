"""PaddleOCR-backed OCR service for reports and scanned images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import fitz
import numpy as np

from app.services.review.layout import LayoutLine, LayoutToken, PageLayout, union_bbox
from app.utils.retry import with_retry

_ocr_engine = None
MODEL_ROOT = Path(__file__).resolve().parents[2] / "data" / "paddleocr"


def _get_ocr_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        det_dir = MODEL_ROOT / "det"
        rec_dir = MODEL_ROOT / "rec"
        cls_dir = MODEL_ROOT / "cls"
        for directory in (det_dir, rec_dir, cls_dir):
            directory.mkdir(parents=True, exist_ok=True)

        _ocr_engine = PaddleOCR(
            # The per-crop angle classifier flips digits on clean, upright
            # scans ("99" -> "66", "9" -> "6" at 0.999 confidence: 16/240
            # values misread vs 0/240 without it). Lab reports are upright
            # pages; leave rotation to page-level handling.
            use_angle_cls=False,
            lang="en",
            use_gpu=False,
            show_log=False,
            enable_mkldnn=True,
            det_model_dir=str(det_dir),
            rec_model_dir=str(rec_dir),
            cls_model_dir=str(cls_dir),
        )
    return _ocr_engine


def preprocess_image(image: np.ndarray) -> np.ndarray:
    """CLAHE contrast enhancement + denoising for document OCR."""
    if image is None:
        raise ValueError("Cannot OCR an empty image")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    denoised = cv2.fastNlMeansDenoising(enhanced, h=10)
    return cv2.cvtColor(denoised, cv2.COLOR_GRAY2BGR)


@with_retry(max_retries=1, backoff_seconds=1.0)
def ocr_image(image: np.ndarray) -> list[dict[str, Any]]:
    """Run PaddleOCR on a numpy image and return text/confidence/bbox rows."""
    result = _get_ocr_engine().ocr(image, cls=False)
    if not result or not result[0]:
        return []

    extracted = []
    for line in result[0]:
        bbox, (text, confidence) = line
        text = str(text).strip()
        if not text:
            continue
        extracted.append(
            {
                "text": text,
                "confidence": round(float(confidence), 3),
                "bbox": bbox,
            }
        )
    return extracted


@dataclass
class OcrBox:
    """One PaddleOCR text box in image pixels (axis-aligned)."""

    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass
class OcrLineGroup:
    """One reading-order line: its text (as ``lines_from_boxes`` emits it) and the boxes it was built from."""

    text: str
    boxes: list[OcrBox]


def group_ocr_boxes(ocr_lines: list[dict[str, Any]]) -> list[OcrLineGroup]:
    """Group PaddleOCR boxes into reading-order lines (the logic ``lines_from_boxes`` always used).

    Boxes are grouped into rows by vertical centre, ordered left to right, and a wide horizontal gap is kept as
    three spaces (a column break the lab tokenizer can use). Items without a bbox are skipped."""
    boxes = []
    for item in ocr_lines:
        bbox = item.get("bbox")
        if not bbox:
            continue
        xs = [float(pt[0]) for pt in bbox]
        ys = [float(pt[1]) for pt in bbox]
        ob = OcrBox(item["text"], float(item.get("confidence", 1.0)), min(xs), min(ys), max(xs), max(ys))
        boxes.append((min(xs), max(xs), (min(ys) + max(ys)) / 2.0, max(ys) - min(ys), item["text"], ob))
    if not boxes:
        return []

    heights = sorted(b[3] for b in boxes if b[3] > 0) or [10.0]
    median_h = heights[len(heights) // 2]
    rows: list[list[tuple]] = []
    for box in sorted(boxes, key=lambda b: b[2]):
        if rows and abs(box[2] - (sum(b[2] for b in rows[-1]) / len(rows[-1]))) <= median_h * 0.5:
            rows[-1].append(box)
        else:
            rows.append([box])

    out: list[OcrLineGroup] = []
    for row in rows:
        row.sort(key=lambda b: b[0])
        parts = [row[0][4]]
        for prev, cur in zip(row, row[1:]):
            gap = cur[0] - prev[1]
            parts.append(("   " if gap > median_h * 2.0 else " ") + cur[4])
        out.append(OcrLineGroup("".join(parts), [b[5] for b in row]))
    return out


def lines_from_boxes(ocr_lines: list[dict[str, Any]]) -> str:
    """Rebuild reading-order text from PaddleOCR boxes.

    PaddleOCR returns one box per word group; these used to be joined with
    single spaces, so a scanned report became ONE line and the line-based lab
    parser couldn't tell rows (or columns) apart. See ``group_ocr_boxes``."""
    groups = group_ocr_boxes(ocr_lines)
    if not groups:
        return " ".join(item.get("text", "") for item in ocr_lines)
    return "\n".join(g.text for g in groups)


def _ocr_page_layout(n: int, ocr_lines: list[dict[str, Any]], *, width: float, height: float, unit: str,
                     scale: float, rotation: int = 0) -> PageLayout:
    """PageLayout from OCR boxes; ``scale`` converts box pixels to the layout unit (72/dpi for PDF pages)."""
    lines: list[LayoutLine] = []
    for g in group_ocr_boxes(ocr_lines):
        toks = [LayoutToken(b.text, (b.x0 * scale, b.y0 * scale, b.x1 * scale, b.y1 * scale), b.conf)
                for b in g.boxes]
        bbox = union_bbox(t.bbox for t in toks)
        if bbox is not None:
            lines.append(LayoutLine(g.text, bbox, toks))
    return PageLayout(n=n, width=width, height=height, unit=unit, method="ocr", rotation=rotation, lines=lines)


def _native_page_layout(n: int, page: Any) -> PageLayout:
    """PageLayout from PyMuPDF word boxes, in displayed-page points (rotation applied)."""
    import fitz

    rot = int(page.rotation or 0)
    matrix = page.rotation_matrix if rot else None
    groups: dict[tuple[int, int], list[tuple[float, float, float, float, str]]] = {}
    for x0, y0, x1, y1, word, block, line, _wn in page.get_text("words"):
        rect = fitz.Rect(x0, y0, x1, y1)
        if matrix is not None:
            rect = rect * matrix
            rect.normalize()
        groups.setdefault((block, line), []).append((rect.x0, rect.y0, rect.x1, rect.y1, str(word)))
    lines: list[LayoutLine] = []
    for words in groups.values():
        toks = [LayoutToken(w[4], (w[0], w[1], w[2], w[3]), 1.0) for w in words]
        bbox = union_bbox(t.bbox for t in toks)
        if bbox is not None:
            lines.append(LayoutLine(" ".join(t.text for t in toks), bbox, toks))
    return PageLayout(n=n, width=float(page.rect.width), height=float(page.rect.height), unit="pt",
                      method="text", rotation=rot, lines=lines)


def extract_text_from_pdf(pdf_path: str | Path | bytes, dpi: int = 200) -> dict[str, Any]:
    """Extract PDF text using native text plus PaddleOCR page rendering."""
    doc = fitz.open(stream=pdf_path, filetype="pdf") if isinstance(pdf_path, bytes) else fitz.open(str(pdf_path))
    all_pages = []
    full_text = []

    try:
        for page_num, page in enumerate(doc, start=1):
            native_text = page.get_text("text").strip()
            pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
            img = cv2.imdecode(np.frombuffer(pix.tobytes("png"), dtype=np.uint8), cv2.IMREAD_COLOR)
            ocr_lines = ocr_image(preprocess_image(img))
            ocr_text = lines_from_boxes(ocr_lines)
            avg_conf = round(sum(line["confidence"] for line in ocr_lines) / len(ocr_lines), 3) if ocr_lines else 0.0

            use_native = bool(native_text) and len(native_text) > len(ocr_text) * 0.8
            page_text = native_text if use_native else ocr_text
            if use_native:
                layout = _native_page_layout(page_num, page)
            else:
                layout = _ocr_page_layout(page_num, ocr_lines, width=float(page.rect.width),
                                          height=float(page.rect.height), unit="pt", scale=72.0 / dpi,
                                          rotation=int(page.rotation or 0))
            all_pages.append(
                {
                    "page": page_num,
                    "text": page_text,
                    "ocr_lines": ocr_lines,
                    "avg_confidence": 0.95 if use_native else avg_conf,
                    "method": "native" if use_native else "paddle_ocr",
                    "layout": layout,
                }
            )
            full_text.append(page_text)
    finally:
        doc.close()

    return {
        "full_text": "\n\n".join(full_text),
        "pages": all_pages,
        "total_pages": len(all_pages),
        "source_type": "pdf",
        "avg_confidence": _average_page_confidence(all_pages),
    }


def extract_text_from_image(image_path: str | Path | bytes) -> dict[str, Any]:
    """Extract text from JPG/PNG/BMP/TIFF bytes or path."""
    if isinstance(image_path, bytes):
        img = cv2.imdecode(np.frombuffer(image_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    else:
        img = cv2.imread(str(image_path))

    ocr_lines = ocr_image(preprocess_image(img))
    full_text = lines_from_boxes(ocr_lines)
    avg_conf = round(sum(line["confidence"] for line in ocr_lines) / len(ocr_lines), 3) if ocr_lines else 0.0
    height, width = (img.shape[0], img.shape[1]) if img is not None else (0, 0)
    return {
        "full_text": full_text,
        "ocr_lines": ocr_lines,
        "avg_confidence": avg_conf,
        "total_pages": 1,
        "source_type": "image",
        "layout": _ocr_page_layout(1, ocr_lines, width=float(width), height=float(height), unit="px", scale=1.0),
    }


def extract_table_from_image(image: np.ndarray) -> list[list[str]]:
    """Group OCR lines into table-like rows by Y coordinate."""
    ocr_lines = ocr_image(preprocess_image(image))
    if not ocr_lines:
        return []

    ocr_lines.sort(key=lambda line: line["bbox"][0][1])
    rows = []
    current_row = [ocr_lines[0]]

    for line in ocr_lines[1:]:
        y_current = line["bbox"][0][1]
        y_last = current_row[-1]["bbox"][0][1]
        if abs(y_current - y_last) < 15:
            current_row.append(line)
        else:
            current_row.sort(key=lambda item: item["bbox"][0][0])
            rows.append([item["text"] for item in current_row])
            current_row = [line]

    current_row.sort(key=lambda item: item["bbox"][0][0])
    rows.append([item["text"] for item in current_row])
    return rows


def extract_text_from_file(file: str | Path | bytes, file_type: str = "auto") -> dict[str, Any]:
    """Universal PaddleOCR entry point for PDFs and image files."""
    if file_type == "auto" and isinstance(file, (str, Path)):
        suffix = Path(file).suffix.lower()
        file_type = "pdf" if suffix == ".pdf" else "image"

    result = extract_text_from_pdf(file) if file_type == "pdf" else extract_text_from_image(file)
    if "avg_confidence" not in result:
        result["avg_confidence"] = _average_page_confidence(result.get("pages", []))
    return result


def _average_page_confidence(pages: list[dict[str, Any]]) -> float:
    confidences = [page["avg_confidence"] for page in pages if page.get("avg_confidence")]
    return round(sum(confidences) / len(confidences), 3) if confidences else 0.0
