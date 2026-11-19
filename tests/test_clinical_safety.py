"""Regression tests for clinical-safety defects (no network, no DB).

Each test pins a silent-wrong-answer bug:
  - a claim whose reasoning step failed was returned with confidence 70 and
    uncertainty_flag=False (the hard-coded value ignored the threshold);
  - CSV lab uploads were accepted by the API but never read ("Unsupported
    OCR format"), so an abnormal panel was reported as "low risk";
  - a lab report with nothing extractable was scored as "low risk";
  - the table regex stitched adjacent lines, turning a date into a
    reference range and flagging a non-lab row CRITICAL;
  - de-duplication keyed on value alone dropped a different test with the
    same number (Potassium 4.0 vs Albumin 4.0).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from app.pipeline import apply_evidence_threshold, _to_0_100  # noqa: E402
from app.services import preprocessor  # noqa: E402

TWO_SOURCES = [{"id": "s1", "text": "a", "score": 0.71}, {"id": "s2", "text": "b", "score": 0.52}]


# ── Claim threshold flagging (README "hallucination mitigation" layer 3) ─────


def test_failed_reasoning_is_insufficient_evidence_not_confident():
    verdict = {
        "verdict": "uncertain",
        "explanation": "Evidence was retrieved, but reasoning could not be completed.",
        "confidence_score": 0.2,
        "hallucination_detected": False,
        "hallucination_details": {"error": "GROQ_API_KEY is not set"},
    }
    out = apply_evidence_threshold(verdict, TWO_SOURCES)
    assert out["uncertainty_flag"] is True
    assert out["verdict"] == "Uncertain"
    assert out["confidence_score"] <= 30
    assert out["explanation"].startswith("Insufficient evidence")


def test_fewer_than_min_sources_is_insufficient_even_if_model_is_confident():
    verdict = {"verdict": "verified", "confidence_score": 0.95, "explanation": "ok"}
    out = apply_evidence_threshold(verdict, TWO_SOURCES[:1])
    assert out["uncertainty_flag"] is True and out["verdict"] == "Uncertain"
    assert "1 supporting source" in out["explanation"]


def test_hallucination_flag_downgrades_verdict():
    verdict = {"verdict": "verified", "confidence_score": 0.9, "hallucination_detected": True}
    out = apply_evidence_threshold(verdict, TWO_SOURCES)
    assert out["uncertainty_flag"] is True and out["hallucination_flagged"] is True
    assert out["verdict"] == "Uncertain"


def test_low_confidence_downgrades_verdict():
    out = apply_evidence_threshold({"verdict": "refuted", "confidence_score": 0.1}, TWO_SOURCES)
    assert out["uncertainty_flag"] is True and out["verdict"] == "Uncertain"


def test_low_relevance_sources_do_not_count_as_support():
    # Real keyword-fallback scores for "The moon is made of green cheese and
    # cures diabetes." against the bundled KB: four hits, all off-topic.
    off_topic = [{"id": i, "text": "x", "score": sc} for i, sc in enumerate([0.25, 0.25, 0.125, 0.125])]
    out = apply_evidence_threshold({"verdict": "verified", "confidence_score": 0.9}, off_topic)
    assert out["verdict"] == "Uncertain" and out["uncertainty_flag"] is True
    assert out["sources"] == []  # off-topic passages are not presented as citations
    unscored = [{"id": 1, "text": "x"}, {"id": 2, "text": "y", "score": None}]
    assert apply_evidence_threshold({"verdict": "verified", "confidence_score": 0.9}, unscored)["uncertainty_flag"] is True


def test_nonsense_claim_never_reaches_llm_with_bundled_kb(monkeypatch):
    """End to end against the real bundled knowledge base (keyword fallback,
    no ChromaDB server, no Groq): an off-topic claim must not be handed to
    the reasoning step with fake 'supporting' evidence."""
    import app.pipeline as pipeline
    from app.services.rag_pipeline import rag_pipeline

    seen = {}

    def fake_verify(claim_text, entities, sources):
        seen["sources"] = sources
        return {"verdict": "verified", "confidence_score": 0.95, "explanation": "LLM says yes"}

    monkeypatch.setattr(rag_pipeline, "verify_claim", fake_verify)
    monkeypatch.setattr(pipeline, "_rag_service", lambda: rag_pipeline)
    result = pipeline.run_claim_pipeline("The moon is made of green cheese and cures diabetes.")
    assert seen["sources"] == []
    assert result.verdict == "Uncertain" and result.uncertainty_flag is True


@pytest.mark.parametrize("raw,label", [("verified", "Verified"), ("supported", "Verified"),
                                       ("refuted", "Refuted"), ("contradicted", "Refuted")])
def test_well_supported_verdict_passes_through_with_model_confidence(raw, label):
    out = apply_evidence_threshold({"verdict": raw, "confidence_score": 0.82, "explanation": "cited"}, TWO_SOURCES)
    assert out["verdict"] == label
    assert out["confidence_score"] == 82.0  # the model's own number, on 0-100
    assert out["uncertainty_flag"] is False


@pytest.mark.parametrize("value,expected", [(0.65, 65.0), (65, 65.0), (1, 100.0), (0, 0.0),
                                            (150, 100.0), (-3, 0.0), (None, 0.0), ("x", 0.0),
                                            (float("nan"), 0.0)])
def test_confidence_is_always_0_to_100(value, expected):
    assert _to_0_100(value) == expected


# ── Lab report ingestion ─────────────────────────────────────────────────────


def test_csv_lab_report_is_read_and_abnormal_values_flagged(tmp_path):
    path = tmp_path / "labs.csv"
    path.write_text(
        "parameter,value,unit,reference_range\n"
        "HbA1c,7.8,%,4.0-5.6\n"
        "Glucose (fasting),142,mg/dL,70-99\n"
        "Hemoglobin,13.5,g/dL,12-16\n"
    )
    ocr = preprocessor.run_ocr(str(path))
    assert "HbA1c 7.8" in ocr.raw_text
    values = {v.name: v for v in preprocessor.extract_all_lab_values(ocr.raw_text)}
    assert values["HbA1c"].value == 7.8 and values["HbA1c"].status != "NORMAL"
    assert values["Glucose (fasting)"].status != "NORMAL"
    assert values["Hemoglobin"].status == "NORMAL"


def test_semicolon_csv_is_sniffed(tmp_path):
    path = tmp_path / "labs.csv"
    path.write_text("Test;Result;Unit;Range\nSodium;128;mmol/L;135-145\n")
    values = preprocessor.extract_all_lab_values(preprocessor.run_ocr(str(path)).raw_text)
    assert [(v.name, v.value) for v in values] == [("Sodium", 128.0)]


def test_lines_are_not_stitched_into_fake_reference_ranges():
    text = "Patient ref 12345\nVisit 2026-01-04\n"
    assert preprocessor.extract_lab_table_from_pdf(text) == []


def test_inverted_range_is_rejected():
    assert preprocessor.extract_lab_table_from_pdf("Ferritin 40 ng/mL 300-20") == []


def test_dedup_keeps_different_tests_with_same_value():
    text = "Potassium 4.0 mmol/L 3.5-5.1\nAlbumin 4.0 g/dL 3.5-5.0\nSodium 128 mmol/L 135-145"
    names = [v.name for v in preprocessor.extract_all_lab_values(text)]
    assert names == ["Potassium", "Albumin", "Sodium"]


def test_dedup_still_merges_ocr_duplicates_of_one_test():
    LV = preprocessor.LabValue
    merged = preprocessor.deduplicate_lab_values([
        LV(name="Hemoglobin Hb", value=13.5, unit="g/dL"),
        LV(name="Hemoglobin", value=13.5, unit="g/dL"),
        LV(name="MCHC", value=13.5, unit="g/dL"),
    ])
    assert [v.name for v in merged] == ["Hemoglobin", "MCHC"]


def test_dedup_tolerates_missing_values():
    LV = preprocessor.LabValue
    merged = preprocessor.deduplicate_lab_values([LV(name="Nitrite", value=None, unit=""),
                                                  LV(name="Protein", value=None, unit="")])
    assert len(merged) == 2


def test_report_with_no_extractable_labs_is_not_called_low_risk(tmp_path, monkeypatch):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    path = tmp_path / "nolabs.csv"
    path.write_text("Patient ref,12345\nVisit,2026-01-04\n")
    result = pipeline.run_report_pipeline(str(path), report_type="lab")
    assert result.status == "completed"
    assert result.uncertainty_flag is True
    assert result.risk_score is None and result.risk_label == "unknown"
    assert "no lab values could be extracted" in result.explanation


# ── Unit reconciliation (SI vs conventional) ─────────────────────────────────


def _si_report(tmp_path, monkeypatch, rows: str, gender: str = "female"):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    path = tmp_path / "si.csv"
    path.write_text("Test,Result,Unit,Range\n" + rows)
    result = pipeline.run_report_pipeline(str(path), report_type="lab", gender=gender)
    return {row["test"]: row for row in result.lab_values}, result


def test_si_glucose_is_converted_not_relabelled(tmp_path, monkeypatch):
    # 7.9 mmol/L ~= 142 mg/dL: HIGH. It used to become "7.9 mg/dL" and be
    # flagged LOW — the opposite direction.
    rows, result = _si_report(tmp_path, monkeypatch, "Glucose (fasting),7.9,mmol/L,3.9-5.5\n")
    g = rows["Glucose (fasting)"]
    assert g["unit"] == "mg/dL" and 140 < g["result"] < 145
    assert g["flag"] == "HIGH"
    assert "7.9 mmol/L" in g["note"]
    assert all(a.get("direction") != "LOW" for a in result.anomalies)


@pytest.mark.parametrize("row,test,expected_flag,lo,hi", [
    ("Creatinine,150,umol/L,60-110\n", "Creatinine", "HIGH", 1.6, 1.8),
    ("Hemoglobin,90,g/L,120-160\n", "Hemoglobin", "LOW", 8.9, 9.1),
    ("HbA1c,53,mmol/mol,20-42\n", "HbA1c", "HIGH", 6.9, 7.1),
])
def test_common_si_units_convert(tmp_path, monkeypatch, row, test, expected_flag, lo, hi):
    rows, _ = _si_report(tmp_path, monkeypatch, row)
    assert lo < rows[test]["result"] < hi
    assert rows[test]["flag"] == expected_flag


def test_unconvertible_unit_keeps_reports_own_range_and_is_not_scored(tmp_path, monkeypatch):
    rows, result = _si_report(tmp_path, monkeypatch, "Ferritin,40,pmol/L,30-400\n")
    f = rows["Ferritin"]
    assert f["value_type"] == "unit_mismatch"
    assert f["unit"] == "pmol/L" and f["ref_low"] == 30.0 and f["ref_high"] == 400.0
    assert not any("ferritin" in str(a.get("field", "")).lower() for a in result.anomalies)


def test_conventional_units_are_untouched():
    from clinical_reference import convert_to_reference_unit

    assert convert_to_reference_unit("glucose", 142, "mg/dL", "mg/dL") == (142.0, True)
    assert convert_to_reference_unit("glucose", 142, "", "mg/dL") == (142.0, True)
    # thousands units are really converted now (they used to keep the number and rescale the range)
    assert convert_to_reference_unit("wbc", 7.2, "10^3/uL", "/µL") == (7200.0, True)
    assert convert_to_reference_unit("ferritin", 40, "pmol/L", "ng/mL") == (None, False)


# ── "Name: value unit (Ref: low-high) FLAG" layout (very common print format) ─


REF_BRACKET_LINES = [
    "Hemoglobin: 9.2 g/dL (Ref: 13.0-17.5) LOW",
    "Fasting Blood Sugar: 168 mg/dL (Ref: 70-99) HIGH",
    "HbA1c: 7.3% (Ref: 0-5.7) HIGH",
    "Platelets: 95 x10^3/uL (Ref: 150-450) CRITICAL",
]


def test_ref_bracket_layout_is_parsed_with_the_reports_own_ranges():
    rows = {v.name: v for v in preprocessor.extract_lab_table_from_pdf("\n".join(REF_BRACKET_LINES))}
    assert set(rows) == {"Hemoglobin", "Fasting Blood Sugar", "HbA1c", "Platelets"}
    assert (rows["Fasting Blood Sugar"].value, rows["Fasting Blood Sugar"].ref_low, rows["Fasting Blood Sugar"].ref_high) == (168.0, 70.0, 99.0)
    assert (rows["HbA1c"].value, rows["HbA1c"].unit) == (7.3, "%")


@pytest.mark.parametrize("line", [
    "Glucose [Normal range 70-99] ",  # no value
    "Patient ref: 12345 Visit: 2026-01-04",
    "Ward 7 Bed 12 (Ref: 3-9)",
])
def test_ref_bracket_layout_does_not_invent_rows(line):
    assert preprocessor.extract_lab_table_from_pdf(line) == []


def test_text_pdf_in_ref_bracket_layout_is_not_reported_normal(tmp_path, monkeypatch):
    """The x-band column parser used to emit whole lines as test names with
    no numeric result for this layout, and those rows replaced real
    extraction: Hb 9.2, FBS 168 and platelets 95k all came back NORMAL /
    low risk."""
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    path = tmp_path / "lab.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(100, 750, "Lab Report - Test Patient")
    for i, line in enumerate(REF_BRACKET_LINES):
        c.drawString(100, 700 - 20 * i, line)
    c.save()

    result = pipeline.run_report_pipeline(str(path), report_type="lab", gender="male", age=54)
    flags = {row["test"]: row["flag"] for row in result.lab_values}
    assert flags == {"Hemoglobin": "LOW", "Fasting Blood Sugar": "HIGH", "HbA1c": "HIGH", "Platelets": "LOW"}
    assert result.risk_label not in ("low", "unknown")
    assert len(result.anomalies) == 4


def test_column_parser_output_without_numeric_results_is_rejected(monkeypatch, tmp_path):
    import app.pipeline as pipeline
    import app.services.lab_report_column_parser as column_parser

    junk = {"panel_type": "general", "lab_values": [
        {"test": "Hemoglobin: 9.2 g/dL (Ref", "result": "LOW", "unit": "", "reference": "--"},
        {"test": "Platelets: 95 x10^3/uL (R", "result": "CRITICAL", "unit": "", "reference": "--"},
    ]}
    monkeypatch.setattr(column_parser, "parse_lab_report_for_pipeline", lambda path: junk)
    pdf = tmp_path / "x.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    assert pipeline._parse_column_lab_report_safe(str(pdf), "lab") == {}

    # rows with a number but no unit and no range are not accepted either (gate: >=60% with a range or unit)
    bare = {"panel_type": "cbc", "lab_values": [{"test": "Hemoglobin", "result": "9.2"}, {"test": "WBC", "result": "7.1"}]}
    monkeypatch.setattr(column_parser, "parse_lab_report_for_pipeline", lambda path: bare)
    assert pipeline._parse_column_lab_report_safe(str(pdf), "lab") == {}

    good = {"panel_type": "cbc", "lab_values": [
        {"test": "Hemoglobin", "result": "9.2", "unit": "g/dL", "ref_raw": "13.0-17.5", "ref_low": 13.0, "ref_high": 17.5},
        {"test": "WBC", "result": "7.1", "unit": "cells/cumm", "ref_raw": "4000-11000", "ref_low": 4000.0, "ref_high": 11000.0},
    ]}
    monkeypatch.setattr(column_parser, "parse_lab_report_for_pipeline", lambda path: good)
    assert pipeline._parse_column_lab_report_safe(str(pdf), "lab") == good


# ── Chest X-ray classifier: no double sigmoid ────────────────────────────────


def test_xrv_outputs_are_not_passed_through_a_second_sigmoid():
    import torch

    from app.image_pipeline.classifier_v2 import _as_probabilities

    calibrated = torch.tensor([[0.0, 0.2, 0.5, 0.9]])

    class XrvLike:  # pretrained torchxrayvision DenseNet: probabilities out
        op_threshs = torch.tensor([0.5] * 4)

    class RawLogits:  # a plain classifier head: logits out
        op_threshs = None

    assert torch.equal(_as_probabilities(XrvLike(), calibrated), calibrated)
    assert torch.allclose(_as_probabilities(RawLogits(), calibrated), torch.sigmoid(calibrated))
    # The bug: a second sigmoid lifts every class to >= 0.5, i.e. "detected".
    assert (torch.sigmoid(calibrated) >= 0.5).all()


def test_black_frame_does_not_light_up_every_pathology():
    import os
    from pathlib import Path

    import numpy as np
    from PIL import Image

    weights = Path(os.path.expanduser("~/.torchxrayvision/models_data"))
    if not any(weights.glob("nih-densenet121*.pt")):
        pytest.skip("torchxrayvision NIH weights not cached")
    from app.image_pipeline.classifier_v2 import NIH_LABELS, classify_image_14class

    result = classify_image_14class(Image.fromarray(np.zeros((512, 512), dtype=np.uint8)))
    assert result.model_version.startswith("torchxrayvision")
    assert len(result.detected_pathologies) < len(NIH_LABELS) // 2


# ── Chest X-ray input: whole radiograph + plausibility gate ──────────────────

NIH_CXR = os.path.join(os.path.dirname(__file__), "fixtures", "nih_cxr_00000001_000.png")


def _synthetic(kind: str):
    import numpy as np
    from PIL import Image

    rng = np.random.default_rng(0)
    if kind == "noise":
        return Image.fromarray(rng.integers(0, 255, (512, 512), dtype=np.uint8))
    if kind == "black":
        return Image.fromarray(np.zeros((512, 512), dtype=np.uint8))
    if kind == "gradient":
        return Image.fromarray(np.tile(np.linspace(0, 255, 512, dtype=np.uint8), (512, 1)))
    if kind == "colour":
        arr = np.zeros((256, 256, 3), dtype=np.uint8)
        arr[..., 0] = 220
        arr[64:192, 64:192, 2] = 200
        arr = (arr + rng.integers(0, 6, arr.shape)).astype(np.uint8)
        return Image.fromarray(arr)
    raise ValueError(kind)


def test_real_chest_radiograph_passes_plausibility_gate():
    from PIL import Image

    from app.pipeline import radiograph_plausibility

    assert radiograph_plausibility(Image.open(NIH_CXR)) == (True, "")


@pytest.mark.parametrize("kind", ["noise", "black", "gradient", "colour"])
def test_non_radiographs_are_rejected_before_classification(kind):
    from app.pipeline import radiograph_plausibility

    ok, why = radiograph_plausibility(_synthetic(kind))
    assert ok is False and why


def test_noise_image_gets_no_findings_end_to_end(tmp_path):
    from app.pipeline import run_image_pipeline

    path = tmp_path / "noise.png"
    _synthetic("noise").save(path)
    result = run_image_pipeline(str(path), image_type="xray")
    assert result.status == "low_quality"
    assert "does not look like a chest radiograph" in result.error
    assert not result.image_classification


def test_classifier_sees_whole_radiograph_and_matches_reference_model():
    """Same answer as torchxrayvision's documented preprocessing on the raw
    film — the pipeline used to classify the CLAHE-enhanced ROI crop."""
    from pathlib import Path

    weights = Path(os.path.expanduser("~/.torchxrayvision/models_data"))
    if not any(weights.glob("nih-densenet121*.pt")):
        pytest.skip("torchxrayvision NIH weights not cached")
    import skimage.io
    import torch
    import torchvision
    import torchxrayvision as xrv

    from app.image_pipeline.classifier_v2 import get_xrv_model
    from app.pipeline import run_image_pipeline

    model = get_xrv_model()
    img = xrv.datasets.normalize(skimage.io.imread(NIH_CXR), 255)
    img = img.mean(2)[None] if img.ndim == 3 else img[None]
    img = torchvision.transforms.Compose([xrv.datasets.XRayCenterCrop(), xrv.datasets.XRayResizer(224)])(img)
    with torch.no_grad():
        ref = model(torch.from_numpy(img)[None].float()).squeeze(0).numpy()
    reference_top = max((p for p in model.pathologies if p), key=lambda p: ref[list(model.pathologies).index(p)])

    result = run_image_pipeline(NIH_CXR, image_type="xray")
    assert result.status == "completed"
    assert result.image_classification["primary_finding"] == reference_top == "Cardiomegaly"


def test_low_risk_consensus_is_not_reported_as_uncertainty():
    """A low ensemble score is a confident 'low risk'. The flag used to be
    `score < 40 or disagreement`, so every normal report was 'uncertain'."""
    from app.services.ensemble_predictor import ensemble_predict

    normal = [{"test": "Hemoglobin", "result": 14.2, "flag": "NORMAL", "ref_low": 13.0, "ref_high": 17.0}]
    out = ensemble_predict(ml_features={"hemoglobin": 14.2}, lab_values=normal, clinical_risk_level="normal")
    assert out["ensemble_risk_score"] < 40
    assert out["uncertainty_flag"] is (out["model_agreement"] == "low")


# ── Mixed blood + urine reports (urinalysis is additional, never a replacement) ─


def _text_pdf(path, lines):
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    c = canvas.Canvas(str(path))
    for i, line in enumerate(lines):
        c.drawString(60, 780 - 16 * i, line)
    c.save()
    return str(path)


def test_blood_criticals_survive_a_urine_section_in_the_same_report(tmp_path, monkeypatch):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    path = _text_pdf(tmp_path / "mixed.pdf", [
        "HAEMATOLOGY", "Haemoglobin 6.1 g/dL 13.0 - 17.0", "Platelet Count 12000 /cumm 150000 - 450000",
        "BIOCHEMISTRY", "Serum Potassium 6.9 mmol/L 3.5 - 5.1",
        "URINE ROUTINE EXAMINATION", "Colour Pale yellow", "Specific Gravity 1.020",
        "Protein Nil", "Glucose Nil", "Ketones Absent", "Nitrite Negative", "Pus cells 1-2 /hpf",
    ])
    result = pipeline.run_report_pipeline(path, report_type="lab", gender="male", age=50)
    flags = {row["test"]: row["flag"] for row in result.lab_values}
    assert flags["Haemoglobin"] == "CRITICAL_LOW"
    assert flags["Platelet Count"] == "CRITICAL_LOW"
    assert flags["Serum Potassium"] == "CRITICAL_HIGH"
    assert result.risk_label == "critical" and result.risk_score >= 90
    assert "urinalysis" in result.panel_type


def test_pure_urinalysis_report_still_uses_the_urine_path(tmp_path, monkeypatch):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    path = _text_pdf(tmp_path / "urine.pdf", [
        "URINE ROUTINE EXAMINATION", "Colour Yellow", "Specific Gravity 1.025", "Protein ++",
        "Glucose +++", "Ketones Present", "Nitrite Positive", "Leukocyte Esterase Positive", "Pus cells 20-25 /hpf",
    ])
    result = pipeline.run_report_pipeline(path, report_type="lab", gender="female", age=40)
    assert result.status == "completed"
    assert result.panel_type == "urinalysis"
    assert result.risk_label not in ("low", "unknown")


NON_RADIOGRAPHS = sorted(
    os.path.join(os.path.dirname(__file__), "fixtures", "non_radiographs", name)
    for name in os.listdir(os.path.join(os.path.dirname(__file__), "fixtures", "non_radiographs"))
)


@pytest.mark.parametrize("path", NON_RADIOGRAPHS, ids=os.path.basename)
def test_documents_shapes_and_blobs_are_not_classified_as_radiographs(path):
    from PIL import Image

    from app.pipeline import radiograph_plausibility

    ok, why = radiograph_plausibility(Image.open(path))
    assert ok is False and why


def test_borderline_probabilities_are_not_reported_as_detected_findings():
    from pathlib import Path

    weights = Path(os.path.expanduser("~/.torchxrayvision/models_data"))
    if not any(weights.glob("nih-densenet121*.pt")):
        pytest.skip("torchxrayvision NIH weights not cached")
    from PIL import Image

    from app.image_pipeline.classifier_v2 import classify_image_14class

    result = classify_image_14class(Image.open(NIH_CXR))
    assert result.detected_pathologies == ["Cardiomegaly"]  # the image's dataset label
    probabilities = {f.label: f for f in result.all_findings}
    for label in result.borderline_pathologies:
        assert not probabilities[label].detected


# ── OCR: keep rows and columns from PaddleOCR boxes ──────────────────────────


def _box(x0, x1, y, text, h=20):
    return {"text": text, "confidence": 0.99, "bbox": [[x0, y], [x1, y], [x1, y + h], [x0, y + h]]}


def test_ocr_boxes_are_rebuilt_into_rows_and_columns():
    """Boxes used to be joined with single spaces, turning a scanned report
    into one line the lab parser couldn't split into rows."""
    from app.services.ocr_service import lines_from_boxes

    boxes = [  # deliberately out of order, slight vertical jitter
        _box(400, 470, 102, "mmol/L"), _box(20, 140, 100, "Potassium"), _box(250, 290, 98, "6.9"),
        _box(520, 620, 101, "3.5 - 5.1"), _box(20, 160, 140, "Haemoglobin"), _box(250, 290, 141, "6.1"),
        _box(400, 450, 139, "g/dL"), _box(520, 640, 140, "13.0 - 17.0"),
    ]
    text = lines_from_boxes(boxes)
    assert text.split("\n") == [
        "Potassium   6.9   mmol/L   3.5 - 5.1",
        "Haemoglobin   6.1   g/dL   13.0 - 17.0",
    ]


def test_ocr_boxes_without_geometry_fall_back_to_plain_join():
    from app.services.ocr_service import lines_from_boxes

    assert lines_from_boxes([{"text": "Glucose"}, {"text": "142"}]) == "Glucose 142"


# ── Held-out blind-set misses (fixed after round 5) ──────────────────────────


def _engine(text, sex, age):
    from app.services.lab_engine import analyze_lab_rows, parse_lab_text

    rows, unread = parse_lab_text(text)
    return analyze_lab_rows(rows, sex=sex, age=age, unparsed_numeric_lines=unread).rows


@pytest.mark.parametrize("header", [
    "Parameter   Last Visit   Today   Units   Bio. Ref.",
    "Test   Follow-up   Admission   Unit   Ref. Range",
])
def test_cumulative_headers_with_multiword_or_dotted_titles(header):
    rows = _engine(header + "\nTSH   2.1   14.8   uIU/mL   0.4-4.0", "F", 45)
    current = 14.8 if "Today" in header else 2.1  # "Admission" is the earlier reading only if marked so
    assert len(rows) == 1
    if "Today" in header:
        assert (rows[0].value, rows[0].flag) == (current, "HIGH")
    else:
        assert rows[0].flag in ("UNKNOWN", "HIGH", "NORMAL")  # ambiguous order: never silently wrong
        if rows[0].flag != "UNKNOWN":
            assert rows[0].value in (2.1, 14.8)


@pytest.mark.parametrize("name", ["Prothrombin Time - INR", "PT (INR)", "PT INR", "INR"])
def test_inr_name_variants_reach_the_critical_threshold(name):
    rows = _engine(f"{name} 5.1 0.8-1.2", "M", 65)
    assert rows[0].analyte_key == "inr" and rows[0].flag == "CRITICAL_HIGH"


def test_prothrombin_time_in_seconds_is_not_inr():
    rows = _engine("Prothrombin Time 14.2 sec 11-13.5", "M", 65)
    assert rows[0].analyte_key == "pt" and rows[0].flag == "HIGH"


def test_unreported_urinalysis_slots_are_not_shown_as_normal_results():
    from app.pipeline import _urine_entities_to_rows

    rows = _urine_entities_to_rows({
        "dipstick": {"protein": {"value": "Nil", "flag": "normal"}, "glucose": {"value": ""}, "ketones": {}},
        "microscopy": {"pus_cells": {"value": None}, "casts": {"granular": "", "hyaline": "present"}},
    })
    assert {r["test"] for r in rows} == {"protein", "hyaline"}


# ── Round-5 review fixes ──────────────────────────────────────────────────────


def test_serology_with_urine_routine_is_not_routed_away_from_the_lab_engine(tmp_path, monkeypatch):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    path = _text_pdf(tmp_path / "sero_urine.pdf", [
        "SEROLOGY", "HIV I & II Antibody (by CLIA) Reactive", "HBsAg Non-Reactive",
        "URINE ROUTINE EXAMINATION", "Colour Pale yellow", "Protein Nil", "Glucose Nil", "Pus cells 1-2 /hpf",
    ])
    result = pipeline.run_report_pipeline(path, report_type="lab", gender="female", age=26)
    flags = {row["test"]: row["flag"] for row in result.lab_values}
    assert any("HIV" in name and flag == "POSITIVE" for name, flag in flags.items())
    assert result.risk_label not in ("low", "unknown")


def test_no_critical_headline_without_a_critical_value(tmp_path, monkeypatch):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    path = _text_pdf(tmp_path / "elderly.pdf", [
        "Haemoglobin 12.4 g/dL 13.0-17.0", "Serum Creatinine 1.4 mg/dL 0.7-1.3",
        "Serum Sodium 134 mmol/L 135-145", "TSH 5.8 uIU/mL 0.4-4.5",
    ])
    result = pipeline.run_report_pipeline(path, report_type="lab", gender="male", age=88)
    assert not any(str(r["flag"]).startswith("CRITICAL") for r in result.lab_values)
    assert result.risk_label in ("moderate", "high") and result.risk_score < 90


def test_ocr_angle_classifier_is_disabled():
    """Per-crop angle classification flipped digits (99 -> 66) at 0.999 confidence."""
    import inspect

    from app.services import ocr_service

    src = inspect.getsource(ocr_service)
    assert "use_angle_cls=False" in src and "cls=True" not in src
