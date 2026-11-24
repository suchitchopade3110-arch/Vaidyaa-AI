"""Layout capture tests LY-01..LY-06 (plans/P2.md section 6.2, 12.3; PRD E7).

Native-text tests stub the OCR engine out (``ocr_image`` returns no boxes) so they run in seconds; the native
text path does not read the OCR result except to compare lengths, and a stub that finds nothing selects it.
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz
import pytest

from app.services import ocr_service
from app.services.preprocessor import run_ocr, run_ocr_with_layout
from app.services.review.layout import DocumentLayout, align_native_lines, LayoutLine

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "eval_golden"
DEV_NATIVE = ROOT / "eval" / "corpus" / "dev" / "native"


@pytest.fixture
def no_ocr(monkeypatch):
    monkeypatch.setattr(ocr_service, "ocr_image", lambda image: [])


def _old_lines_from_boxes(ocr_lines):
    """The pre-P2 implementation, copied verbatim, as the oracle for LY-01."""
    boxes = []
    for item in ocr_lines:
        bbox = item.get("bbox")
        if not bbox:
            continue
        xs = [float(pt[0]) for pt in bbox]
        ys = [float(pt[1]) for pt in bbox]
        boxes.append((min(xs), max(xs), (min(ys) + max(ys)) / 2.0, max(ys) - min(ys), item["text"]))
    if not boxes:
        return " ".join(item.get("text", "") for item in ocr_lines)
    heights = sorted(b[3] for b in boxes if b[3] > 0) or [10.0]
    median_h = heights[len(heights) // 2]
    rows = []
    for box in sorted(boxes, key=lambda b: b[2]):
        if rows and abs(box[2] - (sum(b[2] for b in rows[-1]) / len(rows[-1]))) <= median_h * 0.5:
            rows[-1].append(box)
        else:
            rows.append([box])
    out = []
    for row in rows:
        row.sort(key=lambda b: b[0])
        parts = [row[0][4]]
        for prev, cur in zip(row, row[1:]):
            gap = cur[0] - prev[1]
            parts.append(("   " if gap > median_h * 2.0 else " ") + cur[4])
        out.append("".join(parts))
    return "\n".join(out)


def _box(text, x0, y0, x1, y1, conf=0.99):
    return {"text": text, "confidence": conf, "bbox": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]}


BOX_SETS = [
    [],
    [{"text": "no bbox"}],
    [_box("Hb", 10, 10, 40, 30), _box("14.2", 200, 11, 240, 31), _box("g/dL", 250, 10, 290, 30)],
    [_box("Na", 10, 60, 40, 80), _box("139", 60, 62, 90, 82), _box("K", 10, 10, 20, 30), _box("4.2", 60, 12, 90, 32)],
    [_box("A", 5, 5, 15, 15), _box("B", 500, 5, 510, 15), _box("C", 20, 100, 30, 110)],
    [_box("one", 0, 0, 30, 20), {"text": "skipped"}, _box("two", 40, 1, 70, 21)],
]


@pytest.mark.parametrize("boxes", BOX_SETS)
def test_ly01_lines_from_boxes_identical_to_pre_p2(boxes):
    assert ocr_service.lines_from_boxes(boxes) == _old_lines_from_boxes(boxes)


def test_ly02_ocr_line_bbox_is_union_of_boxes_in_points():
    # three boxes on one line, 200 dpi pixels; points = pixels * 72 / 200 = * 0.36
    boxes = [_box("Hb", 100, 200, 150, 230), _box("14.2", 400, 205, 470, 236), _box("g/dL", 500, 198, 560, 229)]
    page = ocr_service._ocr_page_layout(1, boxes, width=595.0, height=842.0, unit="pt", scale=72 / 200)
    assert len(page.lines) == 1
    line = page.lines[0]
    # union: x 100..560, y 198..236  ->  *0.36
    assert line.bbox == pytest.approx((36.0, 71.28, 201.6, 84.96))
    assert [t.text for t in line.tokens] == ["Hb", "14.2", "g/dL"]
    assert line.min_conf == pytest.approx(0.99)


@pytest.mark.parametrize("sample", ["D043", "D071", "D203"])
def test_ly03_native_alignment_complete_on_golden(no_ocr, sample):
    out, layout = run_ocr_with_layout(str(GOLDEN / f"{sample}.native.pdf"))
    assert layout.kind == "pdf" and layout.pages
    missing = [p.text for p in layout.physical if p.line is None]
    assert not missing, missing[:5]
    assert all(p.page in {pg.n for pg in layout.pages} for p in layout.physical)
    # physical numbering is exactly the non-empty stripped lines of the report text
    assert [p.text for p in layout.physical] == [ln.strip() for ln in out.raw_text.splitlines() if ln.strip()]


def test_ly03_native_alignment_complete_on_first_40_dev_native(no_ocr):
    files = sorted(DEV_NATIVE.glob("*.pdf"))[:40]
    if not files:
        pytest.skip("dev corpus not present")
    bad = {}
    for f in files:
        _, layout = run_ocr_with_layout(str(f))
        missing = [p.text for p in layout.physical if p.line is None]
        if missing:
            bad[f.name] = missing[:3]
    assert not bad, bad


def test_ly04_rotated_page_bbox_covers_the_text(no_ocr, tmp_path):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    src = tmp_path / "plain.pdf"
    c = canvas.Canvas(str(src))
    c.setFont("Helvetica", 14)
    c.drawString(60, 700, "Haemoglobin 14.2 g/dL 13.0 - 17.0")
    c.save()
    doc = fitz.open(str(src))
    doc[0].set_rotation(90)
    rot = tmp_path / "rot90.pdf"
    doc.save(str(rot))
    doc.close()

    _, layout = run_ocr_with_layout(str(rot))
    page = layout.pages[0]
    assert page.rotation == 90
    line = next(p.line for p in layout.physical if "Haemoglobin" in p.text)
    assert line is not None
    x0, y0, x1, y1 = line.bbox
    d = fitz.open(str(rot))
    pg = d[0]
    # displayed page of a 90-degree rotation is landscape and the stored geometry lives in that space
    assert page.width == pytest.approx(pg.rect.width) and page.width > page.height
    pix = pg.get_pixmap(matrix=fitz.Matrix(1, 1), alpha=False)
    dark = 0
    for x in range(int(x0), int(x1) + 1):
        for y in range(int(y0), int(y1) + 1):
            if x < pix.width and y < pix.height and sum(pix.pixel(x, y)[:3]) < 300:
                dark += 1
    assert dark > 20
    # back to unrotated page space: the text box contains the value
    r = fitz.Rect(line.bbox) * pg.derotation_matrix
    r.normalize()
    assert "14.2" in pg.get_textbox(r)
    d.close()


CSV_BODY = (
    "Report: fictional\n"                       # line 1
    "\n"                                         # line 2 (blank)
    "Test,Result,Unit,Reference\n"               # line 3 (header)
    "Haemoglobin,14.2,g/dL,13.0-17.0\n"          # line 4
    "\n"                                         # line 5
    "Serum Potassium,4.2,mmol/L,3.5-5.1\n"       # line 6
    "Note,see 12 other,,\n"                      # line 7 (leftover: digits, not a result)
)


def test_ly05_csv_row_meta_and_identical_rows(tmp_path):
    from app.services.lab_engine.tokenizer import parse_report_text

    f = tmp_path / "r.csv"
    f.write_text(CSV_BODY)
    plain = run_ocr(str(f)).raw_text
    meta, layout = run_ocr_with_layout(str(f))
    assert "@row=" not in plain                     # the untraced reader is byte-identical to before
    assert layout.kind == "csv"
    lines = meta.raw_text.splitlines()
    canon = [ln for ln in lines if ln.startswith("Haemoglobin") or ln.startswith("Serum Potassium")]
    assert canon[0].endswith("@row=4") and canon[1].endswith("@row=6")
    assert any(ln.endswith("@row=7") for ln in lines)   # leftover lines carry it too
    # physical line N of the file is what @row=N names
    physical = CSV_BODY.splitlines()
    assert physical[4 - 1].startswith("Haemoglobin") and physical[6 - 1].startswith("Serum Potassium")
    rows_a, unread_a, warn_a = parse_report_text(plain)
    rows_b, unread_b, warn_b = parse_report_text(meta.raw_text)
    strip = lambda rs: [(r.name, r.value, r.unit, r.ref_low, r.ref_high, r.printed_flag) for r in rs]  # noqa: E731
    assert strip(rows_a) == strip(rows_b) == [("Haemoglobin", 14.2, "g/dL", 13.0, 17.0, None),
                                              ("Serum Potassium", 4.2, "mmol/L", 3.5, 5.1, None)]
    assert unread_a == unread_b and warn_a == warn_b


def test_ly06_exif_orientation_applied_to_layout_size(no_ocr, tmp_path):
    from PIL import Image

    img = Image.new("RGB", (200, 100), "white")        # landscape pixels
    exif = Image.Exif()
    exif[0x0112] = 6                                    # rotate 90 CW to display: becomes 100 x 200 portrait
    f = tmp_path / "photo.jpg"
    img.save(f, exif=exif)
    out, layout = run_ocr_with_layout(str(f))
    page = layout.pages[0] if layout.pages else None
    result = ocr_service.extract_text_from_image(str(f))
    lay = result["layout"]
    assert (lay.width, lay.height, lay.unit) == (100.0, 200.0, "px")
    assert page is None or (page.width, page.height) == (100.0, 200.0)


def test_align_skips_at_most_lookahead_and_gives_none_when_unmatched():
    mk = lambda t: LayoutLine(t, (0, 0, 1, 1))  # noqa: E731
    layout = [mk("a"), mk("b"), mk("c")]
    got = align_native_lines(["a", "zzz", "c"], layout)
    assert got[0] is layout[0] and got[1] is None and got[2] is layout[2]


def test_document_layout_without_matching_pages_keeps_lines_but_no_geometry():
    doc = DocumentLayout.build("pdf", "x 1\ny 2", ["different"], [None])
    assert [p.text for p in doc.physical] == ["x 1", "y 2"] and all(p.line is None for p in doc.physical)
    assert doc.locate([0, 1]).bbox is None
