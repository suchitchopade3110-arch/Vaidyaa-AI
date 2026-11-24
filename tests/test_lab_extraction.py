"""Regression tests for lab extraction / unit-handling defects (no network, no DB).

Each group pins a silent-wrong-answer bug:
  1. equivalent unit spellings (mmol/L vs mEq/L, gm% vs g/dL, cells/cumm vs
     /uL ...) were treated as unit mismatches and dropped from scoring;
  2. the table regex lost PDFs that emit one table cell per line;
  3. the registry layer stamped a hard-coded unit on name variants
     ("Blood Glucose 7.9 mmol/L" -> "7.9 mg/dL LOW") and duplicated rows;
  4. CSV ingestion guessed at columns (a range's low end was read as the
     result) and mis-parsed decimal commas;
  5. de-duplication merged different analytes by substring (Direct vs
     Indirect Bilirubin, LDL vs VLDL, Albumin vs Microalbumin).
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from clinical_reference import convert_to_reference_unit, units_equivalent  # noqa: E402
from app.services import preprocessor  # noqa: E402

LV = preprocessor.LabValue


# ── helpers ──────────────────────────────────────────────────────────────────


def _pipeline_rows(tmp_path, monkeypatch, text: str, gender: str = "male", age=None):
    """Run the real report pipeline on fixed OCR text (OCR/validation/RAG stubbed)."""
    import app.pipeline as pipeline

    class _Ocr:
        raw_text = text
        confidence = 1.0
        pages = [text]

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    monkeypatch.setattr(pipeline, "_run_ocr_safe", lambda path: _Ocr())
    monkeypatch.setattr(pipeline, "_validate_file_safe", lambda path: pipeline._ValidationResult(valid=True, reason=""))
    path = tmp_path / "report.txt"
    path.write_text(text)
    result = pipeline.run_report_pipeline(str(path), report_type="lab", gender=gender, age=age)
    return result.lab_values, result


def _csv_rows(tmp_path, monkeypatch, body: str, name: str = "labs.csv", gender: str = "male"):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    result = pipeline.run_report_pipeline(str(path), report_type="lab", gender=gender)
    return result.lab_values, result


def _csv_extract(tmp_path, body: str, name: str = "labs.csv"):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return preprocessor.extract_all_lab_values(preprocessor.run_ocr(str(path)).raw_text)


# ── 1. unit equivalence ──────────────────────────────────────────────────────


@pytest.mark.parametrize("test,have,want", [
    ("potassium", "mmol/L", "mEq/L"),
    ("sodium", "mEq/L", "mmol/L"),
    ("chloride", "mmol/L", "mEq/L"),
    ("bicarbonate", "mmol/L", "mEq/L"),
    ("alt", "IU/L", "U/L"),
    ("tsh", "uIU/mL", "mIU/L"),
    ("tsh", "µIU/mL", "mIU/L"),
    ("iron", "mcg/dL", "µg/dL"),
    ("iron", "ug/dL", "µg/dL"),
    ("ferritin", "µg/L", "ng/mL"),
    ("hemoglobin", "gm%", "g/dL"),
    ("hemoglobin", "g%", "g/dL"),
    ("hemoglobin", "gm/dL", "g/dL"),
    ("wbc", "cells/cumm", "/µL"),
    ("wbc", "/cumm", "/µL"),
    ("wbc", "/mm3", "/µL"),
    ("wbc", "cells/µL", "/µL"),
    ("wbc", "/cmm", "/µL"),
    ("wbc", "thousand/µL", "10^3/µL"),
])
def test_equivalent_unit_spellings_are_not_mismatches(test, have, want):
    value, ok = convert_to_reference_unit(test, 5.0, have, want)
    assert ok is True and value == 5.0
    assert units_equivalent(have, want, test)


def test_calcium_and_magnesium_are_not_mmol_meq_equivalent():
    # divalent: 1 mmol/L == 2 mEq/L, so these must stay a mismatch.
    assert convert_to_reference_unit("calcium", 2.4, "mmol/L", "mEq/L") == (None, False)
    assert convert_to_reference_unit("magnesium", 0.8, "mmol/L", "mEq/L") == (None, False)
    assert not units_equivalent("mmol/L", "mEq/L", "calcium")


def test_count_units_are_scaled_not_just_relabelled():
    assert convert_to_reference_unit("platelets", 2.5, "lakhs/cumm", "/µL") == (250000.0, True)
    assert convert_to_reference_unit("platelets", 250000, "/cumm", "/µL") == (250000.0, True)
    assert convert_to_reference_unit("rbc", 4.9, "million/cumm", "million/µL") == (4.9, True)
    assert convert_to_reference_unit("rbc", 4900000, "cells/cumm", "million/µL") == (4.9, True)


def test_truly_incompatible_units_still_mismatch():
    assert convert_to_reference_unit("ferritin", 40, "pmol/L", "ng/mL") == (None, False)
    assert convert_to_reference_unit("tsh", 2.0, "mIU/mL", "mIU/L") == (None, False)  # 1000x apart
    assert convert_to_reference_unit("wbc", 7.0, "mg/dL", "/µL") == (None, False)


def test_reported_unit_spellings_are_scored_end_to_end(tmp_path, monkeypatch):
    text = "\n".join([
        "Potassium 6.8 mmol/L 3.5-5.1",
        "Sodium 118 mmol/L 135-145",
        "Chloride 85 mmol/L 98-107",
        "Hb 6.1 gm% 13-17",
        "WBC 1500 cells/cumm 4000-11000",
        "ALT 250 IU/L 7-40",
        "TSH 12.5 uIU/mL 0.4-4.0",
        "Iron 20 mcg/dL 60-170",
    ])
    rows, result = _pipeline_rows(tmp_path, monkeypatch, text)
    by = {r["test"]: r for r in rows}
    # The row keeps the name as printed ("Hb"), and values past a critical
    # limit are now flagged CRITICAL_* (same direction as before).
    expected = {"Potassium": "CRITICAL_HIGH", "Sodium": "CRITICAL_LOW", "Chloride": "LOW", "Hb": "CRITICAL_LOW",
                "WBC": "CRITICAL_LOW", "ALT": "HIGH", "TSH": "HIGH", "Iron": "LOW"}
    assert {k: by[k]["flag"] for k in expected} == expected
    assert not [r for r in rows if r["value_type"] == "unit_mismatch"]
    directions = {a["field"]: a["direction"] for a in result.anomalies}
    assert directions["Potassium"] == "HIGH" and directions["Sodium"] == "LOW" and directions["Chloride"] == "LOW"


# ── 2. layer-1 regex: cell-per-line tables, date guard ───────────────────────

CELL_PER_LINE = (
    "City Diagnostics - Biochemistry Report\nPatient ref 12345    Visit 2026-01-04\n"
    "Test\nResult\nUnit\nReference Range\n"
    "Potassium\n6.8\nmEq/L\n3.5 - 5.1\n"
    "Sodium\n118\nmEq/L\n135 - 145\n"
    "Creatinine\n4.2\nmg/dL\n0.7 - 1.3\n"
    "Hemoglobin\n6.1\ng/dL\n13.0 - 17.0\n"
)


def test_table_with_one_cell_per_line_is_extracted():
    got = {v.name: (v.value, v.unit, v.ref_low, v.ref_high) for v in preprocessor.extract_all_lab_values(CELL_PER_LINE)}
    assert got["Potassium"] == (6.8, "mEq/L", 3.5, 5.1)
    assert got["Sodium"] == (118.0, "mEq/L", 135.0, 145.0)
    assert got["Creatinine"] == (4.2, "mg/dL", 0.7, 1.3)
    assert got["Hemoglobin"] == (6.1, "g/dL", 13.0, 17.0)
    assert len(got) == 4


@pytest.mark.parametrize("text", [
    "Patient ref 12345\nVisit 2026-01-04\n",
    "Patient ref 12345 Visit 2026-01-04",
    "Order 1500\nDate 2026-01-04\n",
    "Report No 77\nVisit 12-34\n",        # even a range-shaped pair: 'Visit' is not a unit
])
def test_dates_and_words_are_not_read_as_value_unit_range(text):
    assert preprocessor.extract_lab_table_from_pdf(text) == []
    assert preprocessor.extract_all_lab_values(text) == []


def test_unit_must_look_like_a_unit():
    assert preprocessor.extract_lab_table_from_pdf("Sample Visit 5 Visit 3-9") == []
    got = preprocessor.extract_lab_table_from_pdf("Sample Visit 5 mm 3-9")
    assert len(got) == 1 and got[0].unit == "mm"


def test_stated_count_unit_is_kept_not_guessed():
    got = preprocessor.extract_lab_table_from_pdf("WBC Count 1500 cells/cumm 4000-11000")[0]
    assert (got.value, got.unit, got.ref_low, got.ref_high) == (1500.0, "cells/cumm", 4000.0, 11000.0)
    assert got.flag.startswith("⬇️")


# ── 3. registry layer vs name variants / SI units ────────────────────────────


@pytest.mark.parametrize("line,test,lo,hi,flag,unit", [
    ("Blood Glucose 7.9 mmol/L", "Glucose", 141.5, 143.0, "HIGH", "mg/dL"),
    ("Fasting Glucose 7.9 mmol/L", "Glucose", 141.5, 143.0, "HIGH", "mg/dL"),
    ("Blood Glucose 7.9 mmol/L 3.9-5.5", "Blood Glucose", 141.5, 143.0, "HIGH", "mg/dL"),
    ("Fasting Glucose 7.9 mmol/L 3.9-5.5", "Fasting Glucose", 141.5, 143.0, "HIGH", "mg/dL"),
    ("Serum Creatinine 150 umol/L", "Creatinine", 1.6, 1.8, "HIGH", "mg/dL"),
    ("Serum Creatinine 150 umol/L 60-110", "Creatinine", 1.6, 1.8, "HIGH", "mg/dL"),
    ("Haemoglobin 90 g/L", "Hemoglobin", 8.9, 9.1, "LOW", "g/dL"),
    ("Haemoglobin 90 g/L 120-160", "Hemoglobin", 8.9, 9.1, "LOW", "g/dL"),
])
def test_name_variant_with_si_unit_gives_one_converted_row(tmp_path, monkeypatch, line, test, lo, hi, flag, unit):
    # age is supplied: a haemoglobin with no printed range needs it (never assumed)
    rows, _ = _pipeline_rows(tmp_path, monkeypatch, line, age=40)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["test"].lower().replace("haemo", "hemo") .endswith(test.lower().replace("haemo", "hemo"))
    assert lo < row["result"] < hi
    assert row["unit"] == unit and row["flag"] == flag


def test_registry_takes_unit_from_the_text():
    # The registry-only extraction layer is gone: one tokenizer reads every row.
    # The unit is the one printed; a missing unit stays missing here and is
    # resolved (or refused) by the lab engine when the row is scored.
    (lv,) = preprocessor.extract_all_lab_values("Blood Glucose 7.9 mmol/L")
    assert (lv.value, lv.unit) == (7.9, "mmol/L")
    (lv,) = preprocessor.extract_all_lab_values("Hemoglobin 13.5")
    assert (lv.value, lv.unit) == (13.5, "")


def test_registry_hit_for_a_test_layer1_already_read_is_dropped():
    text = "Haemoglobin 90 g/L 120-160"
    got = preprocessor.extract_all_lab_values(text)
    assert [(v.name, v.value, v.unit, v.ref_low) for v in got] == [("Haemoglobin", 90.0, "g/L", 120.0)]


def test_dedup_prefers_row_with_report_unit_and_range():
    bare = LV(name="Hemoglobin", value=9.0, unit="g/dL")
    ranged = LV(name="Haemoglobin Hb", value=9.0, unit="g/dL", ref_low=12.0, ref_high=16.0)
    for order in ([bare, ranged], [ranged, bare]):
        (kept,) = preprocessor.deduplicate_lab_values(order)
        assert kept.ref_low == 12.0


# ── 4. CSV / TSV ingestion ───────────────────────────────────────────────────


def test_decimal_comma_semicolon_csv_is_converted_and_flagged(tmp_path, monkeypatch):
    rows, _ = _csv_rows(tmp_path, monkeypatch, "Glucose;7,9;mmol/L;3,9-5,5\n")
    (g,) = rows
    assert g["test"] == "Glucose" and g["unit"] == "mg/dL"
    assert 141.5 < g["result"] < 143.0 and g["flag"] == "HIGH"


def test_decimal_comma_values_and_ranges_parse_exactly(tmp_path):
    (v,) = _csv_extract(tmp_path, "Test;Result;Unit;Range\nCreatinine;1,7;mg/dL;0,7-1,3\n")
    assert (v.value, v.unit, v.ref_low, v.ref_high) == (1.7, "mg/dL", 0.7, 1.3)
    assert v.flag.endswith("HIGH")


def test_header_columns_are_mapped_by_name_not_position(tmp_path, monkeypatch):
    body = "test,reference_range,result,unit\nGlucose,70-99,142,mg/dL\nHb,12.0-16.0,9.1,g/dL\n"
    rows, _ = _csv_rows(tmp_path, monkeypatch, body)
    by = {r["test"]: r for r in rows}
    assert by["Glucose"]["result"] == 142.0 and by["Glucose"]["flag"] == "HIGH"
    assert by["Hb"]["result"] == 9.1 and by["Hb"]["flag"] == "LOW"  # not the range's 12.0
    values = {v.name: v for v in _csv_extract(tmp_path, body)}
    assert values["Hb"].value == 9.1 and (values["Hb"].ref_low, values["Hb"].ref_high) == (12.0, 16.0)


@pytest.mark.parametrize("delim,name", [(",", "a.csv"), (";", "a.csv"), ("\t", "a.tsv"), ("|", "a.csv")])
def test_delimiters_are_sniffed(tmp_path, delim, name):
    rows = ["Investigation", "Observed Value", "Units", "Biological Reference Interval", "Flag"]
    body = delim.join(rows) + "\n" + delim.join(["Sodium", "128", "mmol/L", "135-145", "L"]) + "\n"
    (v,) = _csv_extract(tmp_path, body, name)
    assert (v.name, v.value, v.unit, v.ref_low, v.ref_high) == ("Sodium", 128.0, "mmol/L", 135.0, 145.0)


def test_thousands_separator_in_quoted_comma_csv(tmp_path):
    body = 'Parameter,Result,Unit,Reference\nPlatelet Count,"150,000",/cumm,"150,000-450,000"\n'
    (v,) = _csv_extract(tmp_path, body)
    assert (v.value, v.ref_low, v.ref_high) == (150000.0, 150000.0, 450000.0)


def test_one_sided_range_and_less_than_result(tmp_path):
    body = "Test,Result,Unit,Range\nHbA1c,6.9,%,<5.7\nTSH,<0.01,uIU/mL,0.4-4.0\n"
    by = {v.name: v for v in _csv_extract(tmp_path, body)}
    assert (by["HbA1c"].ref_low, by["HbA1c"].ref_high) == (None, 5.7) and by["HbA1c"].flag.endswith("HIGH")
    assert by["TSH"].prefix == "<" and by["TSH"].value == 0.01 and by["TSH"].flag.startswith("⬇️")


def test_header_unit_hint_and_missing_columns(tmp_path):
    (v,) = _csv_extract(tmp_path, "Test,Result (mg/dL)\nGlucose,142\n")
    assert (v.value, v.unit, v.ref_low) == (142.0, "mg/dL", None)


def test_names_with_digits_and_missing_unit_or_range_survive(tmp_path):
    body = "Test,Result,Unit,Range\nVitamin B12,450,pg/mL,200-900\n25(OH) Vitamin D,18,,\nT3,1.2,ng/mL,\n"
    by = {v.name: v for v in _csv_extract(tmp_path, body)}
    assert by["Vitamin B12"].value == 450.0
    assert by["25(OH) Vitamin D"].value == 18.0 and by["25(OH) Vitamin D"].unit == ""
    assert by["T3"].unit == "ng/mL" and by["T3"].ref_low is None


def test_headerless_csv_uses_positional_order_when_second_cell_numeric(tmp_path):
    vals = _csv_extract(tmp_path, "Sodium,128,mmol/L,135-145\nHemoglobin,9.1,g/dL,12-16\n")
    assert [(v.name, v.value, v.unit) for v in vals] == [("Sodium", 128.0, "mmol/L"), ("Hemoglobin", 9.1, "g/dL")]


def test_headerless_csv_with_non_numeric_second_cell_yields_nothing(tmp_path):
    assert _csv_extract(tmp_path, "Sodium,mmol/L,128,135-145\nNotes,all fine today\n") == []


def test_unmappable_csv_returns_no_rows_and_the_pipeline_says_insufficient(tmp_path, monkeypatch):
    rows, result = _csv_rows(tmp_path, monkeypatch, "Patient ref,12345\nVisit,2026-01-04\n")
    assert rows == [] and result.uncertainty_flag is True and result.risk_label == "unknown"


def test_run_ocr_still_returns_readable_text_for_csv(tmp_path):
    path = tmp_path / "labs.csv"
    path.write_text("Test;Result;Unit;Range\nGlucose;7,9;mmol/L;3,9-5,5\nPatient ref;12345;;\n")
    text = preprocessor.run_ocr(str(path)).raw_text
    assert "Glucose 7.9 mmol/L 3.9-5.5" in text


def test_plain_text_report_is_not_treated_as_structured(tmp_path):
    path = tmp_path / "labs.txt"
    path.write_text("Hemoglobin 9.1 g/dL 12-16\n")
    (v,) = preprocessor.extract_all_lab_values(preprocessor.run_ocr(str(path)).raw_text)
    assert v.value == 9.1 and v.flag.endswith("LOW")


# ── 5. same-test predicate / de-duplication ──────────────────────────────────


@pytest.mark.parametrize("a,b", [
    ("Direct Bilirubin", "Indirect Bilirubin"),
    ("Total Bilirubin", "Direct Bilirubin"),
    ("LDL", "VLDL"),
    ("HDL Cholesterol", "LDL Cholesterol"),
    ("Albumin", "Microalbumin"),
    ("Free T4", "T4"),
    ("Calcium", "Ionised Calcium"),
    ("Fasting Glucose", "Post Prandial Glucose"),
    ("Fasting Glucose", "Random Glucose"),
    ("Hemoglobin", "HbA1c"),
    ("Hemoglobin", "MCHC"),
])
def test_qualified_tests_are_distinct(a, b):
    assert not preprocessor._same_lab_test(a, b)


@pytest.mark.parametrize("a,b", [
    ("Hemoglobin Hb", "Hemoglobin"),
    ("Haemoglobin", "Hb"),
    ("HbA1c", "Glycated Hemoglobin"),
    ("HbA1c", "Glycosylated Haemoglobin"),
    ("FBS", "Fasting Blood Sugar"),
    ("Fasting Glucose", "Glucose (Fasting)"),
    ("SGPT", "ALT"),
    ("SGOT", "AST"),
    ("WBC", "Total Leukocyte Count"),
    ("TLC", "WBC Count"),
    ("Platelets", "Platelet Count"),
    ("PLT", "Platelets"),
    ("S. Creatinine", "Serum Creatinine"),
    ("Blood Glucose", "Glucose"),
])
def test_synonyms_are_the_same_test(a, b):
    assert preprocessor._same_lab_test(a, b)


def test_dedup_keeps_direct_and_indirect_bilirubin():
    merged = preprocessor.deduplicate_lab_values([
        LV(name="Direct Bilirubin", value=0.4, unit="mg/dL"),
        LV(name="Indirect Bilirubin", value=0.4, unit="mg/dL"),
    ])
    assert [v.name for v in merged] == ["Direct Bilirubin", "Indirect Bilirubin"]


def test_dedup_keeps_ldl_and_vldl():
    text = "LDL 30 mg/dL 0-100\nVLDL 30 mg/dL 5-40"
    assert [v.name for v in preprocessor.extract_all_lab_values(text)] == ["LDL", "VLDL"]


def test_dedup_keeps_albumin_and_microalbumin():
    merged = preprocessor.deduplicate_lab_values([
        LV(name="Albumin", value=4.0, unit="g/dL"),
        LV(name="Microalbumin", value=4.0, unit="mg/L"),
    ])
    assert len(merged) == 2


def test_dedup_requires_equivalent_units_when_both_present():
    assert len(preprocessor.deduplicate_lab_values([
        LV(name="Albumin", value=4.0, unit="g/dL"), LV(name="Albumin", value=4.0, unit="mg/L")])) == 2
    assert len(preprocessor.deduplicate_lab_values([
        LV(name="Hemoglobin", value=9.0, unit="g/dL"), LV(name="Hb", value=9.0, unit="gm%")])) == 1
    assert len(preprocessor.deduplicate_lab_values([
        LV(name="Hemoglobin", value=9.0, unit=""), LV(name="Hb", value=9.0, unit="g/dL")])) == 1


def test_dedup_requires_the_same_value():
    assert len(preprocessor.deduplicate_lab_values([
        LV(name="Hemoglobin", value=9.0, unit="g/dL"), LV(name="Hb", value=13.0, unit="g/dL")])) == 2
