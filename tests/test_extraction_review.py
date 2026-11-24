"""Review flow tests: AC-1.1 to AC-1.7, S1, S9, PV-07, PG-* (plans/P2.md section 12.1).

The database-backed tests arrive with the API tasks (T11-T13). The provenance tests AC-1.6-* need no database and
live here already: they compare what ``extract_report`` reports with the generator's ground-truth geometry, which
is independent of product code.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import fitz
import pytest

from app.services import ocr_service
from app.services.review.extract import extract_report
from tools.eval import render
from tools.eval.geometry import gt_bbox_in_input

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "eval_golden"
SAMPLES = ("D043", "D071", "D203")


def _gt(sample):
    return json.loads((GOLDEN / f"{sample}.gt.json").read_text())


def _tokens(name):
    return {t for t in re.findall(r"[a-z0-9]+", name.lower()) if len(t) >= 2}


def numeric_gt_rows(gt):
    return [g for g in gt["rows"] if g.get("value") is not None and g.get("value_type") in ("numeric", "qualified")]


def validity_floor(gt):
    """The plan asks for >= 10 matched rows per sample; D071 prints only 5 numeric results, so the floor is
    min(10, numeric rows in the ground truth): every numeric row of a small report must be matched."""
    return min(10, len(numeric_gt_rows(gt)))


def match_rows(out, gt):
    """(gt_row, extracted_row) pairs by numeric value and a shared name token, on the same page when known."""
    pairs = []
    used = set()
    extracted = [r for r in out.rows if r.kind == "row" and r.origin != "legacy_urine" and r.value_num is not None]
    for g in gt["rows"]:
        if g.get("value") is None or g.get("value_type") not in ("numeric", "qualified"):
            continue
        gtok = _tokens(g["name"])
        for r in extracted:
            if id(r) in used or float(r.value_num) != float(g["value"]):
                continue
            if gtok & _tokens(r.name_raw) and r.source_page == g["page"]:
                pairs.append((g, r, next(iter(gtok & _tokens(r.name_raw)))))
                used.add(id(r))
                break
    return pairs


@pytest.mark.parametrize("sample", SAMPLES)
def test_ac16_native_page_bbox_and_text_match_ground_truth(sample, monkeypatch):
    monkeypatch.setattr(ocr_service, "ocr_image", lambda image: [])
    gt = _gt(sample)
    pdf = GOLDEN / f"{sample}.native.pdf"
    out = extract_report(str(pdf), "lab", "male", 45)
    pairs = match_rows(out, gt)
    assert len(pairs) >= validity_floor(gt) >= 5, f"validity guard: only {len(pairs)} rows matched for {sample}"
    doc = fitz.open(str(pdf))
    try:
        for g, r, token in pairs:
            assert r.source_page == g["page"]
            gx0, gy0, gx1, gy1 = g["bbox"]
            cy = (r.source_bbox[1] + r.source_bbox[3]) / 2
            assert gy0 - 1 <= cy <= gy1 + 1, (g["row_id"], r.source_bbox, g["bbox"])
            words = doc[r.source_page - 1].get_textbox(fitz.Rect(r.source_bbox))
            assert g["value_text"] in words and token in words.lower(), (g["row_id"], words)
    finally:
        doc.close()


def _spy_ocr(monkeypatch):
    calls = []
    real = ocr_service.ocr_image

    def spy(image):
        lines = real(image)
        calls.append(lines)
        return lines

    monkeypatch.setattr(ocr_service, "ocr_image", spy)
    return calls


def _min_conf_inside(boxes, bbox_pt, k, source_text):
    """Minimum Paddle confidence over the boxes that lie in ``bbox_pt`` (pixels * k -> points) and whose text is
    part of the row's source text (a neighbouring cell stacked inside the same band is not the row's box)."""
    x0, y0, x1, y1 = bbox_pt
    best = None
    for b in boxes:
        xs = [p[0] * k for p in b["bbox"]]
        ys = [p[1] * k for p in b["bbox"]]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
        if x0 - 0.5 <= cx <= x1 + 0.5 and y0 - 0.5 <= cy <= y1 + 0.5 and b["text"] in source_text:
            best = b["confidence"] if best is None else min(best, b["confidence"])
    return best


def test_ac16_ocr_scan_bbox_and_confidence_match_ground_truth(tmp_path, monkeypatch):
    gt = _gt("D043")
    scan = tmp_path / "D043.scan.pdf"
    meta = render.render_scan(str(GOLDEN / "D043.native.pdf"), str(scan), seed=7)
    pages = {int(p["page"]): p for p in meta["pages"]}
    calls = _spy_ocr(monkeypatch)
    out = extract_report(str(scan), "lab", "male", 45)
    assert out.status == "ok" and all(p["method"] == "ocr" for p in out.pages)
    pairs = match_rows(out, gt)
    assert len(pairs) >= validity_floor(gt), f"validity guard: only {len(pairs)} rows matched"
    for g, r, _tok in pairs:
        box = gt_bbox_in_input(g["bbox"], "scan", pages[g["page"]])
        cy = (r.source_bbox[1] + r.source_bbox[3]) / 2
        assert box[1] - 2 <= cy <= box[3] + 2, (g["row_id"], r.source_bbox, box)
        assert min(r.source_bbox[2], box[2]) - max(r.source_bbox[0], box[0]) > 0          # horizontal overlap
        assert r.confidence < 1.0
        page_boxes = calls[g["page"] - 1]
        expected = _min_conf_inside(page_boxes, r.source_bbox, 72 / 200, r.source_text)
        assert expected is not None and r.confidence == pytest.approx(expected, abs=1e-6)


def test_ac16_photo_bbox_in_pixels_matches_ground_truth(tmp_path):
    gt = _gt("D071")
    meta = render.render_photo(str(GOLDEN / "D071.native.pdf"), str(tmp_path / "D071"), seed=5)
    pages = {int(p["page"]): p for p in meta["pages"]}
    png = meta["files"][0]
    out = extract_report(png, "lab", "male", 45)
    assert out.file_kind == "image" and out.pages[0]["unit"] == "px"
    gt1 = {**gt, "rows": [g for g in gt["rows"] if g["page"] == 1]}
    pairs = match_rows(out, gt1)
    assert len(pairs) >= validity_floor(gt1) >= 5, f"validity guard: only {len(pairs)} rows matched"
    for g, r, _tok in pairs:
        box = gt_bbox_in_input(g["bbox"], "photo", pages[1])
        cy = (r.source_bbox[1] + r.source_bbox[3]) / 2
        assert box[1] - 4 <= cy <= box[3] + 4, (g["row_id"], r.source_bbox, box)


def test_ac16_csv_rows_have_no_page_and_keep_the_file_line(tmp_path):
    f = tmp_path / "r.csv"
    lines = ["Test,Result,Unit,Reference", "Haemoglobin,14.2,g/dL,13.0-17.0", "Serum Potassium,4.2,mmol/L,3.5-5.1"]
    f.write_text("\n".join(lines) + "\n")
    out = extract_report(str(f), "lab", "male", 45)
    assert len(out.rows) == 2
    for r, line in zip(out.rows, lines[1:]):
        assert r.source_page is None and r.source_bbox is None and r.source_text == line and r.confidence == 1.0
