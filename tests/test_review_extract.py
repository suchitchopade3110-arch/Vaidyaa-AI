"""Extraction tests EX-01..EX-08 (plans/P2.md section 6.1, 12.3).

``extract_report`` is the function the extraction task will call. The OCR engine is stubbed out for native PDFs
(``ocr_image`` finds nothing, so PyMuPDF's text is used), which keeps these fast and deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app import pipeline
from app.services import clinical_ner_service, ocr_service
from app.services.lab_engine.tokenizer import parse_lab_text_traced
from app.services.review.extract import NO_ROWS_WARNING, extract_report
from app.services.review.rules import review_verdict  # noqa: F401  (import check: rules are wired)

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "eval_golden"


@pytest.fixture(autouse=True)
def native_only(monkeypatch):
    monkeypatch.setattr(ocr_service, "ocr_image", lambda image: [])
    monkeypatch.setattr(clinical_ner_service, "_call_groq", _no_groq)


def _no_groq(*a, **k):
    raise RuntimeError("no network in tests")


def text_pdf(path, lines):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    c = canvas.Canvas(str(path))
    for i, line in enumerate(lines):
        c.drawString(60, 780 - 16 * i, line)
    c.save()
    return str(path)


N1 = ["HAEMATOLOGY", "Haemoglobin 14.2 g/dL 13.0 - 17.0", "BIOCHEMISTRY", "Serum Sodium 139 mmol/L 135 - 145",
      "Serum Potassium 4.2 mmol/L 3.5 - 5.1", "Serum Creatinine 0.9 mg/dL 0.7 - 1.3",
      "Glucose Fasting 92 mg/dL 70 - 100", "Platelet Count 12000 /cumm 150000 - 450000",
      "Total Leucocyte Count 7.2 4.0 - 11.0"]

URINE_ONLY = ["URINE ROUTINE EXAMINATION", "Colour Pale yellow", "Specific Gravity 1.020", "Protein Nil",
              "Glucose Nil", "Ketones Absent", "Nitrite Negative", "Pus cells 1-2 /hpf"]


def test_ex01_native_golden_rows_have_provenance():
    out = extract_report(str(GOLDEN / "D043.native.pdf"), "lab", "male", 50)
    assert out.status == "ok" and out.file_kind == "pdf" and len(out.pages) == 2
    rows = [r for r in out.rows if r.kind == "row"]
    assert len(rows) >= 20
    for r in out.rows:
        assert r.source_page in (1, 2) and r.source_text and r.confidence in (1.0, 0.85)
        x0, y0, x1, y1 = r.source_bbox
        assert 0 <= x0 < x1 <= 595.3 and 0 <= y0 < y1 <= 841.9      # inside the A4 page, in points
    assert [r.row_index for r in out.rows] == list(range(len(out.rows)))


def test_ex02_rows_equal_the_preliminary_engine_rows(monkeypatch):
    seen = []
    real = pipeline.analyze_lab_rows
    monkeypatch.setattr(pipeline, "analyze_lab_rows", lambda *a, **k: seen.append(real(*a, **k)) or seen[-1])
    out = extract_report(str(GOLDEN / "D043.native.pdf"), "lab", "male", 50)
    assert len(seen) == 1                                            # no urine in D043: one engine pass
    shown = [r for r in out.rows if r.kind == "row"]
    assert [r.name_raw for r in shown] == [s.display_name for s in seen[0].rows]
    assert [r.prelim_flag for r in shown] == [s.flag for s in seen[0].rows]


def test_ex03_urine_only_report_legacy_rows_replace_engine_rows(tmp_path):
    out = extract_report(text_pdf(tmp_path / "u.pdf", URINE_ONLY), "lab", None, None)
    rows = [r for r in out.rows if r.kind == "row"]
    assert rows, "precondition: the legacy reader found urine rows"
    assert all(r.origin == "legacy_urine" and "NR-LEGACY-URINE" in r.review_codes and r.needs_review for r in rows)
    assert all(r.specimen == "urine" for r in rows)


def test_ex04_blood_plus_urine_adds_legacy_rows_only_for_unseen_names(tmp_path):
    lines = ["HAEMATOLOGY", "Haemoglobin 6.1 g/dL 13.0 - 17.0", "Platelet Count 12000 /cumm 150000 - 450000",
             "BIOCHEMISTRY", "Serum Potassium 6.9 mmol/L 3.5 - 5.1", *URINE_ONLY]
    out = extract_report(text_pdf(tmp_path / "m.pdf", lines), "lab", "male", 50)
    names = {r.name_raw for r in out.rows if r.origin != "legacy_urine" and r.kind == "row"}
    assert {"Haemoglobin", "Platelet Count", "Serum Potassium"} <= names          # blood rows are never dropped
    key = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())  # noqa: E731
    shown = {key(r.name_raw) for r in out.rows if r.origin != "legacy_urine"}
    for r in out.rows:
        if r.origin == "legacy_urine":
            assert key(r.name_raw) not in shown
    crit = {r.name_raw for r in out.rows if r.kind == "row" and r.prelim_flag and r.prelim_flag.startswith("CRITICAL")}
    assert {"Haemoglobin", "Serum Potassium", "Platelet Count"} <= crit
    assert all(r.needs_review for r in out.rows if r.name_raw in crit)


def test_ex05_no_text_means_no_rows_a_warning_and_review_all(tmp_path):
    out = extract_report(text_pdf(tmp_path / "e.pdf", ["Fictional Laboratory", "Patient report"]), "lab", "male", 40)
    assert out.status == "ok" and not [r for r in out.rows if r.kind == "row"]
    assert NO_ROWS_WARNING in out.warnings and out.review_all


def test_ex06_missing_file_fails_with_a_generic_code(tmp_path):
    out = extract_report(str(tmp_path / "nope.pdf"), "lab", None, None)
    assert out.status == "failed" and out.error_code == "FILE_INVALID" and out.rows == []
    assert str(tmp_path) not in " ".join(out.warnings)                # no path in anything shown (S10)


def test_ex06b_unreadable_file_yields_no_rows_and_review_all_not_a_crash(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"")
    out = extract_report(str(bad), "lab", None, None)
    assert out.status == "ok" and not out.rows and out.review_all and NO_ROWS_WARNING in out.warnings


def test_ex06c_unexpected_exception_is_generic(monkeypatch, tmp_path):
    def boom(path):
        raise RuntimeError("secret value 6.9 at /private/path")

    monkeypatch.setattr(pipeline, "_run_ocr_safe", boom)
    f = tmp_path / "a.csv"
    f.write_text("Test,Result\nHb,1\n")
    out = extract_report(str(f), "lab", None, None)
    assert out.status == "failed" and out.error_code == "EXTRACTION_ERROR"
    assert "secret" not in repr(out.warnings) and "/private" not in repr(out.warnings)


def test_ex07_demographics_sentence_is_a_note_not_a_warning(tmp_path):
    path = text_pdf(tmp_path / "n1.pdf", N1)
    none = extract_report(path, "lab", None, None)
    assert any("sex and age not provided" in n for n in none.notes) and not any("not provided" in w for w in none.warnings)
    full = extract_report(path, "lab", "male", 45)
    assert full.notes == []


def test_ex08_extraction_never_scores_anything(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("scoring component called during extraction")

    monkeypatch.setattr(pipeline, "predict_tabular", boom)
    monkeypatch.setattr(pipeline, "_explain_report_safe", boom)
    monkeypatch.setattr(pipeline, "_run_ner_safe", boom)
    monkeypatch.setattr(pipeline, "_rag_service", boom)
    monkeypatch.setattr(pipeline, "analyze_blood_report", boom)
    import app.services.ensemble_predictor as ens
    import app.services.ingest_urinalysis_kb as kb
    monkeypatch.setattr(ens, "ensemble_predict", boom)
    monkeypatch.setattr(kb, "query_urinalysis_kb", boom)
    monkeypatch.setattr(clinical_ner_service, "_apply_urine_pattern_rules", boom)
    lines = N1 + URINE_ONLY
    out = extract_report(text_pdf(tmp_path / "x.pdf", lines), "lab", "male", 45)
    assert out.status == "ok" and out.rows


def test_n1_review_rows_match_the_clinical_expectation(tmp_path):
    """Report N1: platelets 12000/cumm is critical (NR-CRITICAL); TLC has no unit (NR-UNIT); the rest is clean."""
    out = extract_report(text_pdf(tmp_path / "n1.pdf", N1), "lab", "male", 45)
    by = {r.name_raw: r for r in out.rows}
    assert by["Platelet Count"].review_codes[0] == "NR-CRITICAL"
    assert "NR-UNIT" in by["Total Leucocyte Count"].review_codes
    clean = [n for n, r in by.items() if not r.needs_review]
    assert set(clean) == {"Haemoglobin", "Serum Sodium", "Serum Potassium", "Serum Creatinine", "Glucose Fasting"}
    assert {r.specimen for r in out.rows} == {"serum"}


def test_rr16_unread_numeric_line_becomes_an_unread_row(tmp_path):
    lines = ["Haemoglobin 14.2 g/dL 13.0 - 17.0", "Unknown thing 5 x"]
    assert parse_lab_text_traced("\n".join(lines)).unread[0].text == "Unknown thing 5 x"      # precondition
    out = extract_report(text_pdf(tmp_path / "r.pdf", lines), "lab", "male", 45)
    unread = [r for r in out.rows if r.kind == "unread_line"]
    assert len(unread) == 1 and unread[0].review_codes == ["NR-UNREAD"] and unread[0].source_text == "Unknown thing 5 x"
    assert unread[0].needs_review and unread[0].parsed_payload is None and unread[0].source_page == 1


def test_csv_rows_carry_the_file_line_and_full_confidence(tmp_path):
    f = tmp_path / "r.csv"
    f.write_text("Report: fictional\n\nTest,Result,Unit,Reference\nHaemoglobin,14.2,g/dL,13.0-17.0\n\n"
                 "Serum Potassium,4.2,mmol/L,3.5-5.1\n")
    out = extract_report(str(f), "lab", "male", 45)
    assert out.file_kind == "csv" and out.pages == []
    by = {r.name_raw: r for r in out.rows}
    assert by["Haemoglobin"].source_text == "Haemoglobin,14.2,g/dL,13.0-17.0"
    assert by["Serum Potassium"].source_text == "Serum Potassium,4.2,mmol/L,3.5-5.1"
    for r in out.rows:
        assert r.source_page is None and r.source_bbox is None and r.confidence == 1.0 and r.origin == "csv"
        assert r.review_codes == [] and not r.needs_review
