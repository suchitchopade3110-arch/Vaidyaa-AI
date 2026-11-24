"""Tokenizer trace tests TK-01..TK-04 (plans/P2.md section 6.3, 12.3).

The oracle for TK-01 is the pre-P2 ``parse_lab_text_ex`` / ``reassemble_cells``, copied here verbatim, so the
traced twin is compared with what the engine did before it existed (not with itself).
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional, Tuple

import pytest

from app.services import ocr_service
from app.services.lab_engine import tokenizer as tk
from app.services.lab_engine.models import ParsedRow
from app.services.lab_engine.tokenizer import (
    _Ctx, _is_atomic, _is_header_line, _is_section_heading, _is_tail_cell, _header_roles, _parse_internal,
    _tokens, finalize_ranges, normalize_line, parse_line, parse_lab_text_traced, parse_report_text,
    parse_report_text_traced, reassemble_cells, reassemble_cells_traced,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "tests" / "fixtures" / "lab_cases").glob("*.txt"))
GOLDEN = sorted((ROOT / "tests" / "fixtures" / "eval_golden").glob("*.native.pdf"))


def _old_reassemble_cells(lines: List[str]) -> List[str]:
    out: List[str] = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if parse_line(line) is not None or _is_tail_cell(line) or not tk.re.search(r"[A-Za-z]", line):
            out.append(line)
            i += 1
            continue
        j = i + 1
        acc = line
        while j < n and j - i <= 9 and _is_tail_cell(lines[j]):
            acc += " " + lines[j]
            j += 1
        if j > i + 1 and parse_line(acc) is not None:
            out.append(acc)
            i = j
        else:
            out.append(line)
            i += 1
    return out


def _old_parse_lab_text_ex(text: str, decimal_comma: bool = False) -> Tuple[List[ParsedRow], int]:
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    atomic = sum(1 for ln in lines if _is_atomic(ln))
    if lines and atomic / len(lines) >= 0.3:
        lines = _old_reassemble_cells(lines)
    ctx = _Ctx(decimal_comma=decimal_comma)
    rows: List[ParsedRow] = []
    unread = 0
    last: Optional[ParsedRow] = None
    i = 0
    while i < len(lines):
        ln = lines[i]
        if _is_header_line(ln):
            j = i
            toks: List[str] = []
            while j < len(lines) and _is_header_line(lines[j]):
                toks.extend(_tokens(normalize_line(lines[j])))
                j += 1
            if j - i >= 2 or len(toks) >= 2:
                ctx.roles = _header_roles(toks)
                last = None
                i = j
                continue
        res = _parse_internal(ln, ctx)
        unread += res.unread
        if res.rows:
            rows.extend(res.rows)
            last = res.rows[-1] if len(res.rows) == 1 else None
        elif res.kind == "interp" and last is not None:
            last.range_options.extend(res.options)
            finalize_ranges(last)
        else:
            if res.kind != "interp":
                last = None
            if _is_section_heading(ln):
                ctx.section = normalize_line(ln).lower()
        i += 1
    return rows, unread


def _texts():
    for f in FIXTURES:
        yield f.name, f.read_text(errors="replace")


@pytest.fixture(scope="module")
def golden_texts():
    mp = pytest.MonkeyPatch()
    mp.setattr(ocr_service, "ocr_image", lambda image: [])
    try:
        from app.services.preprocessor import run_ocr
        yield {p.name: run_ocr(str(p)).raw_text for p in GOLDEN}
    finally:
        mp.undo()


def test_tk01_traced_equals_pre_p2_on_fixtures():
    assert FIXTURES, "fixtures missing"
    for name, text in _texts():
        old_rows, old_unread = _old_parse_lab_text_ex(text)
        new_rows, new_unread = tk.parse_lab_text_ex(text)
        assert new_rows == old_rows and new_unread == old_unread, name
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        assert reassemble_cells(lines) == _old_reassemble_cells(lines), name


def test_tk01_traced_equals_pre_p2_on_golden_samples(golden_texts):
    assert len(golden_texts) == 3
    for name, text in golden_texts.items():
        old_rows, old_unread = _old_parse_lab_text_ex(text)
        traced = parse_lab_text_traced(text)
        assert [t.row for t in traced.rows] == old_rows and traced.unread_count == old_unread, name
        assert old_rows, name


def test_tk01_every_row_id_points_at_a_physical_line(golden_texts):
    for name, text in golden_texts.items():
        traced = parse_lab_text_traced(text)
        for t in traced.rows:
            assert t.line_ids and all(0 <= i < len(traced.lines) for i in t.line_ids), name
        for u in traced.unread:
            assert u.count >= 1 and all(0 <= i < len(traced.lines) for i in u.line_ids)
        assert sum(u.count for u in traced.unread) == traced.unread_count


def test_tk02_reassembled_row_carries_all_its_physical_lines():
    lines = ["Haemoglobin", "14.2", "g/dL", "13.0 - 17.0", "Serum Potassium 4.2 mmol/L 3.5 - 5.1"]
    out = reassemble_cells_traced(lines)
    assert out[0][1] == (0, 1, 2, 3)
    assert out[0][0] == "Haemoglobin 14.2 g/dL 13.0 - 17.0"
    assert out[1] == (lines[4], (4,))
    traced = parse_lab_text_traced("\n".join(lines))
    hb = next(t for t in traced.rows if t.row.name.lower().startswith("haemoglobin"))
    assert len(hb.line_ids) >= 2 and hb.line_ids == (0, 1, 2, 3)


def test_tk03_interpretation_line_joins_the_previous_row():
    text = "Vitamin D 12 ng/mL\nDeficient: <20\nInsufficient: 20-29\nSufficient: 30-100\nSerum Potassium 4.2 mmol/L 3.5 - 5.1"
    traced = parse_lab_text_traced(text)
    vit = traced.rows[0]
    assert vit.row.name == "Vitamin D" and vit.line_ids == (0, 1, 2, 3)
    assert traced.rows[1].line_ids == (4,)


def test_unread_part_is_reported_with_its_ids_and_text():
    traced = parse_lab_text_traced("Serum Potassium 4.2 mmol/L 3.5 - 5.1\nUnknown thing 5 x")
    assert [(u.line_ids, u.text, u.count) for u in traced.unread] == [((1,), "Unknown thing 5 x", 1)]
    assert traced.unread_count == 1 and [t.line_ids for t in traced.rows] == [(0,)]


def test_structured_csv_trace_uses_row_meta_and_keeps_rows_identical():
    body = ("#structured-lab-csv v1\nHaemoglobin 14.2 g/dL 13-17 @row=4\nSerum Potassium 4.2 mmol/L 3.5-5.1 @row=6\n"
            "#other-rows\nCalcium 9.1 mg/dL @row=7")
    plain = body.replace(" @row=4", "").replace(" @row=6", "").replace(" @row=7", "")
    t = parse_report_text_traced(body)
    assert [x.line_ids for x in t.rows] == [(4,), (6,)] and [(u.line_ids, u.text) for u in t.unread] == [((7,), "Calcium 9.1 mg/dL")]
    rows_a, un_a, _ = parse_report_text(body)
    rows_b, un_b, _ = parse_report_text(plain)
    assert rows_a == rows_b and un_a == un_b == 1      # including ParsedRow.line: the meta never reaches the row


def test_tk04_column_parser_rows_carry_page_and_bbox_inside_the_page(tmp_path):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    path = tmp_path / "cols.pdf"
    c = canvas.Canvas(str(path), pagesize=(1000, 800))
    c.setFont("Helvetica", 10)
    for k, (test, res, unit, ref) in enumerate([("Haemoglobin", "14.2", "g/dL", "13.0-17.0"),
                                                ("Serum Potassium", "4.2", "mmol/L", "3.5-5.1")]):
        y = 700 - 30 * k
        c.drawString(50, y, test)
        c.drawString(320, y, res)
        c.drawString(490, y, unit)
        c.drawString(590, y, ref)
    c.save()
    from app.services.lab_report_column_parser import column_rows_to_parsed_located, parse_lab_report_for_pipeline

    parsed = parse_lab_report_for_pipeline(str(path))
    located = column_rows_to_parsed_located(parsed["lab_values"])
    assert [p.name for p, _, _ in located] == ["Haemoglobin", "Serum Potassium"]
    for k, (row, page, bbox) in enumerate(located):
        assert page == 1 and bbox is not None
        x0, y0, x1, y1 = bbox
        assert 0 <= x0 < x1 <= 1000 and 0 <= y0 < y1 <= 800
        assert x0 < 60 and x1 > 600                       # spans the test column through the range column
        baseline_top = 800 - (700 - 30 * k)               # drawn baseline, top-left origin
        assert y0 < baseline_top < y1 + 1
