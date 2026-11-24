"""needs_review trigger tests RR-01..RR-27 (S2; plans/P2.md section 6.6, 12.2). No database.

Expected codes are derived from clinical facts (stated per test), never from running the code. Where a case
depends on a registry fact (a missing critical limit, an unrecognised name) the test asserts that fact first.
"""

from __future__ import annotations

import pytest

from app.services.lab_engine import analyze_lab_rows, match_analyte, parse_lab_text
from app.services.lab_engine.analytes import REGISTRY
from app.services.lab_engine.models import ParsedRow
from app.services.review.rules import RowContext, compute_confidence, review_verdict, unread_verdict
from app.services.review.specimen import assign_specimens


def verdicts(text, sex="male", age=45, ctx=None, rows=None):
    """Parse report text, assign specimens, score preliminarily and return [(name, scored, verdict)]."""
    rows = rows if rows is not None else parse_lab_text(text)[0]
    assign_specimens(rows)
    analysis = analyze_lab_rows(rows, sex, age)
    out = []
    for r in analysis.rows:
        warns = [w for w in analysis.warnings if w.startswith(f"{r.display_name}:")]
        c = ctx(r) if callable(ctx) else (ctx or RowContext())
        c = RowContext(origin=c.origin, ocr_min_conf=c.ocr_min_conf, line_count=c.line_count, located=c.located,
                       warnings=warns)
        out.append((r.display_name, r, review_verdict(r, c)))
    return out


def one(text, **kw):
    got = verdicts(text, **kw)
    assert len(got) == 1, got
    return got[0]


def codes(text, **kw):
    return one(text, **kw)[2].codes


def test_rr01_certain_normal_row_needs_no_review():
    _, r, v = one("Serum Potassium 4.2 mmol/L 3.5 - 5.1")
    assert r.flag == "NORMAL" and v.codes == [] and not v.needs_review and v.reason is None


def test_rr02_critical_potassium():
    c = codes("Serum Potassium 6.9 mmol/L 3.5 - 5.1")
    assert c[0] == "NR-CRITICAL"          # 6.9 is above Table C's 6.0-6.5 guard band


def test_rr03_missing_unit_on_leucocyte_count():
    got = one("Total Leucocyte Count 7.2 4.0 - 11.0")
    assert "NR-UNIT" in got[2].codes
    assert "Unit not printed" in got[2].reasons


def test_rr04_missing_unit_on_haemoglobin():
    assert "NR-UNIT" in codes("Haemoglobin 13.5 13.0 - 17.0")


def test_rr05_ph_dimensionless_inside_range_needs_none():
    rows, _ = parse_lab_text("ARTERIAL BLOOD GAS\npH 7.40 7.35 - 7.45")
    got = verdicts("", rows=rows)
    assert len(got) == 1 and got[0][1].analyte_key == "blood_ph" and got[0][2].codes == []


def test_rr06_unknown_test_name():
    name = "Zorbit Marker"
    assert match_analyte(name) is None
    c = codes(f"{name} 85 ug/dL 60 - 120")
    assert c == ["NR-UNKNOWN-TEST"]


def test_rr07_tsh_52_far_outside_and_marked():
    assert not REGISTRY["tsh"].criticals, "precondition: the registry has no TSH critical limit"
    c = codes("TSH 52 uIU/mL 0.4 - 4.5")
    assert "NR-RANGE10" in c and "NR-MARKED" in c       # 52 > 10 x 4.5; (52-4.5)/4.5 = 1055 % above the bound


def test_rr08_creatinine_0_06_decimal_slip():
    assert not [c for c in REGISTRY["creatinine"].criticals if c.direction == "below"], "precondition"
    c = codes("Serum Creatinine 0.06 mg/dL 0.7 - 1.3")
    assert "NR-RANGE10" in c and "NR-MARKED" in c       # 0.06 < 0.07; (0.7-0.06)/0.7 = 91 %


def test_rr09_printed_flag_disagrees_with_range():
    assert "NR-FLAG" in codes("Haemoglobin 12.1 g/dL 13.0 - 17.0 H")   # printed H, value below range


def test_rr10_ocr_low_token_confidence():
    ctx = RowContext(origin="ocr", ocr_min_conf=0.82)
    _, _, v = one("Serum Potassium 4.2 mmol/L 3.5 - 5.1", ctx=ctx)
    assert "NR-OCR" in v.codes
    assert compute_confidence("ocr", min_token_conf=0.82) == 0.82


def test_rr11_ocr_confident_row():
    ctx = RowContext(origin="ocr", ocr_min_conf=0.95)
    _, _, v = one("Serum Potassium 4.2 mmol/L 3.5 - 5.1", ctx=ctx)
    assert "NR-OCR" not in v.codes and v.codes == []
    assert compute_confidence("ocr", min_token_conf=0.95) == 0.95


def test_rr12_mixed_report_row_before_any_heading_is_specimen_unknown():
    text = "Haemoglobin 14.2 g/dL 13.0 - 17.0\nURINE ROUTINE EXAMINATION\nSpecific Gravity 1.020"
    got = verdicts(text)
    by = {n: v for n, _, v in got}
    assert "NR-SPECIMEN" in by["Haemoglobin"].codes
    assert "NR-SPECIMEN" not in by["Specific Gravity"].codes


def test_rr13_several_candidates_none_chosen():
    rows, _ = parse_lab_text("Potassium 4.2 5.1 6.7 mmol/L 3.5 - 5.1")
    assert rows and rows[0].candidates, "precondition: the reader found several candidates"
    got = verdicts("", rows=rows)
    assert "NR-MULTI" in got[0][2].codes


def test_rr14_column_parser_row():
    ctx = RowContext(origin="column")
    _, _, v = one("Serum Potassium 4.2 mmol/L 3.5 - 5.1", ctx=ctx)
    assert v.codes == ["NR-COLUMN"]
    assert compute_confidence("column") == 0.85


def test_rr15_cell_per_line_text_is_reassembled():
    ctx = RowContext(origin="text", line_count=4)
    _, _, v = one("Haemoglobin 14.2 g/dL 13.0 - 17.0", ctx=ctx)
    assert v.codes == ["NR-REASSEMBLED"]
    assert compute_confidence("text", line_count=4) == 0.85
    assert compute_confidence("text", line_count=1) == 1.0


def test_rr16_unread_line_verdict():
    v = unread_verdict()
    assert v.codes == ["NR-UNREAD"] and v.needs_review and v.reason
    assert "NR-" not in v.reason


def test_rr17_pco2_82_is_marked():
    assert not [c for c in REGISTRY["pco2"].criticals if c.direction == "above"], "precondition: no pCO2 limit"
    c = codes("ARTERIAL BLOOD GAS\npCO2 82 mmHg 35 - 45")
    assert "NR-MARKED" in c                              # (82-45)/45 = 82 % > 50 %


def test_rr18_bicarbonate_8_is_marked():
    key = match_analyte("Bicarbonate").key
    assert not [c for c in REGISTRY[key].criticals if c.direction == "below"], "precondition: no HCO3 limit"
    assert "NR-MARKED" in codes("Bicarbonate 8 mmol/L 22 - 29")      # (22-8)/22 = 64 %


def test_rr19_qualitative_row_has_no_key_until_p3():
    assert "NR-UNKNOWN-TEST" in codes("HBsAg Reactive")


def test_rr20_legacy_urine_row():
    p = ParsedRow(name="Nitrite", text_value="Positive", section="urine examination", specimen="urine")
    r = analyze_lab_rows([p], None, None).rows[0]
    v = review_verdict(r, RowContext(origin="legacy_urine", ocr_min_conf=0.99))
    assert "NR-LEGACY-URINE" in v.codes


def test_rr21_sex_dependent_range_without_sex():
    c = codes("Haemoglobin 14.0 g/dL Male: 13.0 - 17.0 Female: 12.0 - 15.5", sex=None)
    assert "NR-NORANGE" in c
    _, _, v = one("Haemoglobin 14.0 g/dL Male: 13.0 - 17.0 Female: 12.0 - 15.5", sex=None)
    assert any("sex/age, which was not given" in t for t in v.reasons)     # S4: sex is never assumed


def test_rr22_same_row_with_sex_male_is_clean():
    assert codes("Haemoglobin 14.0 g/dL Male: 13.0 - 17.0 Female: 12.0 - 15.5", sex="male") == []


def test_rr23_negative_potassium_is_invalid():
    assert "NR-INVALID" in codes("Serum Potassium -4.2 mmol/L 3.5 - 5.1")


def test_rr24_spot_urine_sodium_is_never_hyponatraemia():
    got = verdicts("URINE SPOT ELECTROLYTES\nSodium 38 mmol/L")
    _, r, v = got[0]
    assert r.parsed.specimen == "urine" and r.analyte_key is None
    assert not r.flag.startswith("CRITICAL")
    assert v.codes == ["NR-UNKNOWN-TEST"] or "NR-UNKNOWN-TEST" in v.codes


def test_rr26_certain_normal_glucose():
    assert codes("Glucose Fasting 92 mg/dL 70 - 100") == []


def test_rr27_critical_low_sodium():
    assert "NR-CRITICAL" in codes("Serum Sodium 112 mmol/L 135 - 145")     # below Table C's 118-125 guard band


def test_reason_text_never_contains_codes_and_is_nonempty_iff_review():
    cases = ["Serum Potassium 6.9 mmol/L 3.5 - 5.1", "Total Leucocyte Count 7.2 4.0 - 11.0",
             "TSH 52 uIU/mL 0.4 - 4.5", "Serum Potassium 4.2 mmol/L 3.5 - 5.1", "HBsAg Reactive"]
    for t in cases:
        for _, _, v in verdicts(t):
            assert "NR-" not in (v.reason or "")
            assert bool(v.reason) == v.needs_review == bool(v.codes)
            assert len(v.codes) == len(v.reasons)


def test_priority_order_of_reasons():
    _, _, v = one("Serum Potassium 6.9 mmol/L 3.5 - 5.1", ctx=RowContext(origin="column"))
    assert v.codes[0] == "NR-CRITICAL" and v.codes[-1] == "NR-COLUMN"
    assert v.reason.startswith("Looks critical — please verify")


def test_unknown_flag_always_needs_review():
    p = ParsedRow(name="Serum Potassium", value=4.2, unit="mmol/L")           # no printed range, sex/age irrelevant
    rows = verdicts("", rows=[p], sex=None, age=None)
    for _, r, v in rows:
        if r.flag == "UNKNOWN":
            assert v.needs_review


@pytest.mark.parametrize("origin, n, conf, want", [("csv", 1, None, 1.0), ("manual", 1, None, 1.0),
                                                   ("legacy_urine", 1, None, 0.5), ("legacy_urine", 1, 0.9, 0.9)])
def test_compute_confidence_table(origin, n, conf, want):
    assert compute_confidence(origin, line_count=n, min_token_conf=conf) == want


def test_rr25_invariants_over_the_golden_samples(monkeypatch):
    from pathlib import Path

    from app.services import ocr_service
    from app.services.review.extract import extract_report

    monkeypatch.setattr(ocr_service, "ocr_image", lambda image: [])
    golden = sorted((Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "eval_golden").glob("*.native.pdf"))
    assert len(golden) == 3
    total = 0
    for pdf in golden:
        out = extract_report(str(pdf), "lab", "male", 45)
        assert out.status == "ok"
        for r in out.rows:
            total += 1
            assert r.needs_review == bool(r.review_codes)
            assert bool(r.review_reason) == r.needs_review
            assert "NR-" not in (r.review_reason or "")
            if r.kind == "unread_line":
                assert r.needs_review and r.review_codes == ["NR-UNREAD"]
            if r.prelim_flag == "UNKNOWN" or (r.prelim_flag or "").startswith("CRITICAL"):
                assert r.needs_review, (pdf.name, r.name_raw, r.prelim_flag)
    assert total > 60           # three non-trivial reports (39 + ...): the property is checked on real rows
