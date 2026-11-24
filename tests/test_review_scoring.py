"""Scoring of confirmed rows: SR-01..SR-10 (plans/P2.md section 7, 12.3; S1, S5, S7).

Clinical references (Table C zones, adult male unless stated): K 6.9 mmol/L, Na 112 mmol/L, platelets 12 x10^3/uL,
pCO2 82 mmHg are critical under every policy; the normal set Hb 14.2 g/dL, Na 139, K 4.2, creatinine 0.9 mg/dL,
glucose 92 mg/dL, TLC 7.2 x10^3/uL has no abnormal value.
"""

from __future__ import annotations

import json

import pytest

from app import pipeline
from app.services.lab_engine import parse_lab_text
from app.services.review.rows import apply_row_edit
from app.services.review.scoring import ConfirmedRow, score_confirmed_rows
from app.services.review.specimen import assign_specimens


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)


NORMAL = ["Haemoglobin 14.2 g/dL 13.0 - 17.0", "Serum Sodium 139 mmol/L 135 - 145",
          "Serum Potassium 4.2 mmol/L 3.5 - 5.1", "Serum Creatinine 0.9 mg/dL 0.7 - 1.3",
          "Glucose Fasting 92 mg/dL 70 - 100", "Total Leucocyte Count 7.2 x10^3/uL 4.0 - 11.0"]
PLATELETS = "Platelet Count 12000 /cumm 150000 - 450000"


def crows(lines, specimen=None):
    rows, _ = parse_lab_text("\n".join(lines))
    assign_specimens(rows)
    if specimen:
        for r in rows:
            r.specimen = specimen
    return [ConfirmedRow(parsed=r, row_id=f"row-{i}", row_index=i) for i, r in enumerate(rows)]


def score(rows, sex="male", age=45, **kw):
    return score_confirmed_rows(rows, sex=sex, age=age, **kw)


def flags(out):
    return {v["test"]: v["flag"] for v in out.response.lab_values}


def label(out):
    return str(out.response.risk_label).lower()


def test_sr01_platelets_12000_per_cumm_is_critical():
    out = score(crows(NORMAL + [PLATELETS]))
    assert flags(out)["Platelet Count"] == "CRITICAL_LOW"          # 12 x10^3/uL: below every critical limit
    assert label(out) == "critical" and out.response.confirmed is True


def test_sr02_rows_not_passed_in_cannot_appear_anywhere():
    out = score(crows(NORMAL))                                      # the platelet row was deleted by the clinician
    dumped = json.dumps(out.response.model_dump(), default=str).lower()
    assert "platelet" not in dumped and "12000" not in dumped and "12,000" not in dumped
    assert label(out) != "critical"


def test_sr03_s5_urine_sodium_csf_glucose_and_serum_potassium():
    na = crows(["URINE SPOT ELECTROLYTES", "Sodium 38 mmol/L"], specimen="urine")
    out = score(na)
    assert not flags(out)["Sodium"].startswith("CRITICAL") and not out.response.critical_alerts
    dumped = json.dumps(out.response.model_dump(), default=str).lower()
    assert "hyponat" not in dumped and label(out) != "critical"

    glu = crows(["Glucose 28 mg/dL 40 - 70"], specimen="csf")
    assert flags(score(glu))["Glucose"] == "LOW"                    # serum hypoglycaemia limit not applied to CSF

    k = crows(["Serum Potassium 6.9 mmol/L 3.5 - 5.1"], specimen="serum")
    assert flags(score(k))["Serum Potassium"] == "CRITICAL_HIGH"


def test_sr04_urine_only_rows_are_a_urinalysis_with_a_uti_pattern():
    rows = crows(["URINE ROUTINE EXAMINATION", "Nitrite Positive", "Leukocyte Esterase Positive",
                  "Pus cells 20-25 /hpf"], specimen="urine")
    out = score(rows, sex=None, age=None)
    r = out.response
    assert r.panel_type == "urinalysis"
    assert any(a.get("test") == "UTI" or "UTI" in str(a.get("field", "")) for a in r.anomalies)
    assert label(out) in ("moderate", "medium", "high", "critical")   # positive nitrite and esterase are abnormal
    assert all("source_text" in v and "row_id" in v for v in r.lab_values)


def test_sr05_no_raw_text_reaches_the_models(tmp_path, monkeypatch):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    path = tmp_path / "s.pdf"
    c = canvas.Canvas(str(path))
    for i, line in enumerate(["Fictional Lab", "Patient on SENTINELDRUG 5 mg daily", *NORMAL]):
        c.drawString(60, 780 - 16 * i, line)
    c.save()
    seen = []
    monkeypatch.setattr(pipeline, "predict_tabular", lambda *a, **k: seen.append(("tab", repr((a, k)))) or {})
    monkeypatch.setattr(pipeline, "_explain_report_safe", lambda *a, **k: seen.append(("rag", repr((a, k)))) or
                        {"verdict": "x", "explanation": "e", "confidence_score": 50.0, "plain_language_summary": "s",
                         "sources": [], "uncertainty_flag": False, "hallucination_flagged": False})
    monkeypatch.setattr(pipeline, "_run_ner_safe", lambda *a, **k: seen.append(("ner", repr((a, k)))) or None)
    from app.services import ocr_service
    monkeypatch.setattr(ocr_service, "ocr_image", lambda image: [])
    pipeline.run_report_pipeline(str(path), report_type="lab", gender="male", age=45)
    assert seen and not [s for s in seen if s[0] == "ner"]
    assert not any("SENTINEL" in text.upper() for _, text in seen)


def test_sr06_s7_headlines_on_confirmed_rows():
    normal = score(crows(NORMAL))
    assert label(normal) != "critical"                             # (a) no critical value anywhere
    one_abnormal = score(crows(NORMAL + ["Haemoglobin 11.0 g/dL 13.0 - 17.0"]))
    assert label(one_abnormal) not in ("low", "normal")           # (b) Hb 11.0 is below 13.0 for a man
    unknown = score(crows(NORMAL + ["Zorbit Marker 85 ug/dL"]))
    assert label(unknown) == "unknown"                              # (c) a confirmed row nobody can assess


def test_sr07_row_flags_align_with_input_and_duplicates_take_the_survivors_flag():
    rows = crows(["Serum Potassium 6.9 mmol/L 3.5 - 5.1", "Serum Sodium 139 mmol/L 135 - 145",
                  "Serum Potassium 6.9 mmol/L 3.5 - 5.1"])
    out = score(rows)
    assert out.row_flags == ["CRITICAL_HIGH", "NORMAL", "CRITICAL_HIGH"]
    k = next(v for v in out.response.lab_values if v["test"] == "Serum Potassium")
    assert sorted([k["row_id"], *k["merged_row_ids"]]) == ["row-0", "row-2"]


def test_sr09_critical_alerts_are_present_on_critical_results():
    out = score(crows(["Serum Potassium 6.9 mmol/L 3.5 - 5.1"]))
    alerts = out.response.critical_alerts
    assert alerts and "potassium" in alerts[0]["test"].lower()
    assert not score(crows(NORMAL)).response.critical_alerts


def test_edited_row_is_scored_with_the_edited_value_and_keeps_provenance():
    rows = crows(["Serum Potassium 4.2 mmol/L 3.5 - 5.1"])
    rows[0].parsed = apply_row_edit(rows[0].parsed, value_raw="6.9")
    rows[0].review_status, rows[0].source_page, rows[0].source_text = "edited", 1, "Serum Potassium 4.2 mmol/L 3.5 - 5.1"
    out = score(rows)
    v = out.response.lab_values[0]
    assert v["flag"] == "CRITICAL_HIGH" and v["result"] == 6.9
    assert (v["review_status"], v["source_page"], v["row_id"]) == ("edited", 1, "row-0")
    assert "4.2" not in json.dumps(out.response.critical_alerts)


def test_empty_confirmed_set_is_insufficient_evidence_never_low_risk():
    out = score([])
    assert out.response.risk_label == "unknown" and out.response.risk_score is None
    assert "Insufficient evidence" in out.response.explanation
