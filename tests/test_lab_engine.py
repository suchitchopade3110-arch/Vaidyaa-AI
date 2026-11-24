"""Lab engine tests: units, analyte matching, tokenizer, evaluation, risk floor,
and an acceptance suite over the case files in tests/fixtures/lab_cases and the
PDFs in tests/fixtures/lab_pdfs (no network, no DB, no model files).

The acceptance suite pins, for EVERY row of EVERY case, the analyte as printed,
the canonical value, the unit and the flag, plus the minimum risk label. A
dropped row, wrong unit, wrong range or wrong direction fails here.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

from clinical_reference import CRITICAL_THRESHOLDS, LAB_RANGES  # noqa: E402
from app.services.lab_engine import (  # noqa: E402
    REGISTRY,
    analyze_lab_rows,
    apply_risk_floor,
    canon_tokens,
    decorate_explanation,
    match_analyte,
    parse_lab_text,
    parse_line,
    parse_report_text,
    resolve_unit,
    to_canonical,
    unit_key,
    units_equivalent,
)
from app.services.lab_engine.tokenizer import parse_range_text  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
CASE_DIR = FIXTURES / "lab_cases"
PDF_DIR = FIXTURES / "lab_pdfs"

RANK = {"unknown": 0, "low": 0, "normal": 0, "moderate": 1, "high": 2, "critical": 3}


def _analyse(text, sex=None, age=None):
    rows, unparsed, warns = parse_report_text(text)
    return analyze_lab_rows(rows, sex, age, unparsed, tuple(warns))


def _by_name(analysis):
    return {r.display_name: r for r in analysis.rows}


# ── units ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("a,b,key", [
    ("mmol/L", "mEq/L", "potassium"),
    ("gm%", "g/dL", "hemoglobin"),
    ("g%", "g/dL", "hemoglobin"),
    ("mg%", "mg/dL", "glucose"),
    ("IU/L", "U/L", "alt"),
    ("cells/cumm", "/µL", "wbc"),
    ("x10^3/uL", "10^9/L", "wbc"),
    ("mm/hour", "mm/hr", "esr"),
    ("mcg/dL", "ug/dL", "iron"),
    ("uIU/mL", "mIU/L", "tsh"),
])
def test_equivalent_unit_spellings(a, b, key):
    assert units_equivalent(a, b, key)


@pytest.mark.parametrize("a,b,key", [
    ("mmol/L", "mEq/L", "calcium"),      # divalent: x2
    ("mg/dL", "mmol/L", "glucose"),
    ("", "mg/dL", "glucose"),            # empty never matches
    ("mIU/mL", "mIU/L", "tsh"),          # 1000x apart
])
def test_non_equivalent_units(a, b, key):
    assert not units_equivalent(a, b, key)


def _canon(name, value, unit, lo=None, hi=None):
    analyte = match_analyte(name)
    assert analyte is not None, name
    out, u, note, ok = to_canonical(analyte, value, unit, lo, hi)
    return out, u, ok


@pytest.mark.parametrize("name,value,unit,expect,canon_unit", [
    ("Glucose", 22.0, "mmol/L", 396.35, "mg/dL"),
    ("Creatinine", 450, "umol/L", 5.089, "mg/dL"),
    ("Hemoglobin", 65, "g/L", 6.5, "g/dL"),
    ("HbA1c", 75, "mmol/mol", 9.01, "%"),
    ("Urea", 12, "mmol/L", 72.07, "mg/dL"),
    ("BUN", 5, "mmol/L", 14.0, "mg/dL"),
    ("Calcium", 3.6, "mmol/L", 14.43, "mg/dL"),
    ("Platelets", 15, "x10^3/uL", 15000, "/µL"),
    ("Platelets", 0.15, "lakhs/cumm", 15000, "/µL"),
    ("WBC", 7.2, "10^3/uL", 7200, "/µL"),
    ("WBC", 25.4, "x10^9/L", 25400, "/µL"),
    ("RBC", 4.9, "million/cumm", 4.9, "million/µL"),
])
def test_unit_conversion_values(name, value, unit, expect, canon_unit):
    out, u, ok = _canon(name, value, unit)
    assert ok and out == pytest.approx(expect, rel=0.01)
    assert unit_key(u) == unit_key(canon_unit)


def test_ferritin_in_pmol_is_refused_not_guessed():
    out, _, ok = _canon("Ferritin", 40, "pmol/L")
    assert not ok and out is None


def test_unit_missing_is_inferred_from_range_or_refused():
    glucose = match_analyte("Glucose")
    # range 3.9-5.5 can only be mmol/L: inferred and converted
    res = resolve_unit(glucose, "", 7.9, 3.9, 5.5)
    assert res.kind == "inferred" and res.ok and res.fn is not None
    # range 70-99 matches mg/dL
    assert resolve_unit(glucose, "", 120, 70, 99).kind == "inferred"
    # a range that matches nothing: refuse
    assert not resolve_unit(glucose, "", 120, 1.0, 2.0).ok
    # ambiguous value, no range (7.9 is plausible as mmol/L and mg/dL? no: only mmol/L) -> either way never silent
    res = resolve_unit(glucose, "", 8.0)
    assert (not res.ok) or res.kind == "inferred"


# ── analyte matching (exact canonical tokens; no substrings) ─────────────────


@pytest.mark.parametrize("name,key", [
    ("Haemoglobin", "hemoglobin"), ("Hb", "hemoglobin"), ("HGB", "hemoglobin"),
    ("Serum Potassium", "potassium"), ("K", "potassium"), ("Na", "sodium"), ("Cl", "chloride"),
    ("Platelet Count", "platelets"), ("PLT", "platelets"), ("Platelets", "platelets"),
    ("Total Leucocyte Count", "wbc"), ("TLC", "wbc"), ("Total WBC Count", "wbc"),
    ("SGPT", "alt"), ("ALT", "alt"), ("SGOT", "ast"),
    ("S. Creatinine", "creatinine"), ("Serum Creatinine", "creatinine"),
    ("Blood Urea", "urea"), ("Urea", "urea"), ("BUN", "bun"), ("Blood Urea Nitrogen", "bun"),
    ("HbA1c", "hba1c"), ("Hemoglobin A1c", "hba1c"), ("Glycosylated Hemoglobin (HbA1c)", "hba1c"),
    ("Fasting Glucose", "glucose_fasting"), ("FBS", "glucose_fasting"),
    ("Random Blood Sugar", "glucose_random"), ("PP Glucose", "glucose_pp"),
    ("Post Prandial Blood Sugar", "glucose_pp"),
    ("Direct Bilirubin", "bilirubin_direct"), ("Bilirubin Direct", "bilirubin_direct"),
    ("Indirect Bilirubin", "bilirubin_indirect"), ("Total Bilirubin", "bilirubin_total"),
    ("LDL Cholesterol", "ldl"), ("VLDL Cholesterol", "vldl"), ("HDL Cholesterol", "hdl"),
    ("Non-HDL Cholesterol", "non_hdl"), ("Albumin", "albumin"), ("Microalbumin", "microalbumin"),
    ("Troponin I", "troponin_i"), ("hs Troponin I", "troponin_i"), ("Troponin T", "troponin_t"), ("Hb S", "hb_s"),
    ("Free T4", "free_t4"), ("T4 Total", "t4"), ("Ionized Calcium", "ionized_calcium"),
    ("MCHC", "mchc"), ("Mean Corpuscular Hemoglobin Concentration", "mchc"),
    ("Absolute Neutrophil Count", "neutrophils_abs"), ("Neutrophils", "neutrophils_pct"),
    ("Glucose (Method: GOD-POD)", "glucose_unspecified"), ("Creatinine, Serum (Jaffe)", "creatinine"),
    ("SGPT (ALT) - IFCC", "alt"), ("Sodium (Na+)", "sodium"),
])
def test_names_resolve_to_the_right_analyte(name, key):
    analyte = match_analyte(name)
    assert analyte is not None and analyte.key == key, (name, analyte and analyte.key)


@pytest.mark.parametrize("name", [
    "Urea (BUN)", "Patient ID", "Visit", "Bed",
    "Hemoglobin Electrophoresis Pattern", "Bilirubin Conjugated Fraction Zzz",
])
def test_ambiguous_or_unknown_names_do_not_match(name):
    assert match_analyte(name) is None


@pytest.mark.parametrize("name,key", [
    ("Cholesterol (HDL)", "hdl"), ("Cholesterol, HDL", "hdl"), ("Cholesterol - LDL", "ldl"),
    ("Cholesterol (Total)", "cholesterol"), ("Cholesterol - VLDL", "vldl"),
])
def test_cholesterol_with_a_qualifier_is_that_fraction_never_total_cholesterol(name, key):
    # changed expectation (round 5): "Cholesterol (HDL)" used to be refused as ambiguous. The qualifier is now
    # part of the identity ("cholesterol hdl" -> HDL), which is what the report means; the safety property that
    # matters -- the qualifier is never dropped so the row can never become TOTAL cholesterol -- is asserted here.
    analyte = match_analyte(name)
    assert analyte is not None and analyte.key == key


def test_distinct_analytes_never_collapse():
    pairs = [("Direct Bilirubin", "Total Bilirubin"), ("HDL Cholesterol", "Non-HDL Cholesterol"),
             ("LDL Cholesterol", "VLDL Cholesterol"), ("Albumin", "Microalbumin"),
             ("Urea", "BUN"), ("Troponin I", "Troponin T"), ("Hemoglobin", "Hb S"), ("Free T4", "T4 Total"),
             ("Fasting Glucose", "PP Glucose"), ("Neutrophils", "Absolute Neutrophil Count")]
    for a, b in pairs:
        ma, mb = match_analyte(a), match_analyte(b)
        assert ma is not None and mb is not None and ma.key != mb.key, (a, b)


def test_no_two_registry_analytes_share_a_name():
    seen = {}
    for key, analyte in REGISTRY.items():
        for name in [analyte.display] + list(analyte.synonyms):
            tokens = canon_tokens(name)
            assert seen.setdefault(tokens, key) == key, (name, key, seen[tokens])


def test_every_legacy_range_and_critical_threshold_is_covered_by_the_registry():
    for key, cfg in LAB_RANGES.items():
        for name in [key] + list(cfg.get("aliases", [])):
            assert match_analyte(name) is not None, f"LAB_RANGES name {name!r} not in the lab-engine registry"
    for entry in CRITICAL_THRESHOLDS:
        analyte = match_analyte(entry["test"])
        assert analyte is not None, entry["test"]
        assert any(c.direction == entry["direction"] and c.limit == pytest.approx(entry["value"])
                   for c in analyte.criticals), f"{entry['test']} {entry['direction']} {entry['value']} missing"


# ── tokenizer layouts ────────────────────────────────────────────────────────


def _one(line):
    row = parse_line(line)
    assert row is not None, line
    return row


@pytest.mark.parametrize("line,name,value,unit,lo,hi,flag", [
    ("Hemoglobin 9.2 g/dL 13.0 - 17.5", "Hemoglobin", 9.2, "g/dL", 13.0, 17.5, None),
    ("Hemoglobin: 9.2 g/dL (Ref: 13.0-17.5) LOW", "Hemoglobin", 9.2, "g/dL", 13.0, 17.5, "L"),
    ("Hemoglobin 9.2 L g/dL 13.0 - 17.0", "Hemoglobin", 9.2, "g/dL", 13.0, 17.0, "L"),
    ("Hemoglobin 9.2* g/dL 13.0-17.0", "Hemoglobin", 9.2, "g/dL", 13.0, 17.0, "ABN"),
    ("Potassium 6.8mmol/L 3.5-5.1", "Potassium", 6.8, "mmol/L", 3.5, 5.1, None),
    ("Creatinine 4.2 mg/dL (0.7-1.3)", "Creatinine", 4.2, "mg/dL", 0.7, 1.3, None),
    ("Platelet Count 2,50,000 /cumm 1,50,000 - 4,10,000", "Platelet Count", 250000.0, "/cumm", 150000.0, 410000.0, None),
    ("Neutrophils 85% 40-70", "Neutrophils", 85.0, "%", 40.0, 70.0, None),
    ("Bilirubin Direct 0.9 mg/dL .0-0.3", "Bilirubin Direct", 0.9, "mg/dL", 0.0, 0.3, None),
    ("K 6.9 mmol/L 3.5 - 5.1", "K", 6.9, "mmol/L", 3.5, 5.1, None),
])
def test_line_layouts(line, name, value, unit, lo, hi, flag):
    row = _one(line)
    assert (row.name, row.value, row.unit, row.ref_low, row.ref_high, row.printed_flag) == (
        name, value, unit, lo, hi, flag)


@pytest.mark.parametrize("line,lo,hi,strict_lo,strict_hi", [
    ("HbA1c 9.4 % <5.7", None, 5.7, False, True),
    ("LDL Cholesterol 190 mg/dL < 100", None, 100.0, False, True),
    ("HDL Cholesterol 25 mg/dL > 40", 40.0, None, True, False),
    ("SGPT 400 U/L Up to 40", None, 40.0, False, False),
    ("Triglycerides 650 mg/dL Up to 150", None, 150.0, False, False),
])
def test_one_sided_ranges(line, lo, hi, strict_lo, strict_hi):
    row = _one(line)
    assert (row.ref_low, row.ref_high, row.ref_strict_low, row.ref_strict_high) == (lo, hi, strict_lo, strict_hi)


def test_qualifier_values():
    row = _one("Troponin I >50 ng/mL <0.04")
    assert (row.qualifier, row.value, row.ref_high) == (">", 50.0, 0.04)
    row = _one("Troponin I <0.01 ng/mL 0.00-0.04")
    assert (row.qualifier, row.value) == ("<", 0.01)


def test_sex_labelled_printed_range():
    rng = parse_range_text("Male: 3.4 - 7.0 Female: 2.4 - 6.0")
    assert rng["sex_ranges"] == {"M": (3.4, 7.0), "F": (2.4, 6.0)}
    row = _one("Uric Acid 9.8 mg/dL Male: 3.4 - 7.0 Female: 2.4 - 6.0")
    assert row.sex_ranges == {"M": (3.4, 7.0), "F": (2.4, 6.0)}


def test_cell_per_line_pdf_text():
    text = "Investigation\nResult\nFlag\nUnit\nBio. Ref. Interval\nHaemoglobin\n9.2\nL\ng/dL\n13.0 - 17.0\nPotassium\n6.8\nH\nmmol/L\n3.5 - 5.1\n"
    rows, unparsed = parse_lab_text(text)
    assert [(r.name, r.value, r.unit, r.printed_flag) for r in rows] == [
        ("Haemoglobin", 9.2, "g/dL", "L"), ("Potassium", 6.8, "mmol/L", "H")]
    assert unparsed == 0


def test_qualitative_results():
    rows, _ = parse_lab_text("HIV 1&2 Reactive\nHBsAg Non Reactive\nUrine Protein Nil")
    assert [(r.name, r.text_value) for r in rows][:2] == [("HIV 1&2", "Reactive"), ("HBsAg", "Non Reactive")]


@pytest.mark.parametrize("text", [
    "Patient ref 12345\nVisit 2026-01-04",
    "Age 54 Yrs",
    "Ward 7 Bed 12 (Ref: 3-9)",
    "Date 12/03/2026",
    "Order 1500\nDate 2026-01-04",
    "Report No 77\nVisit 12-34",
    "Patient Age 45 Years Sex Male",
    "Sample Visit 5 Visit 3-9",
])
def test_guard_lines_produce_no_rows(text):
    rows, unparsed = parse_lab_text(text)
    assert rows == [] and unparsed == 0


def test_a_real_unit_rescues_a_header_like_name():
    assert [r.unit for r in parse_lab_text("Sample Visit 5 mm 3-9")[0]] == ["mm"]


def test_unreadable_numeric_lines_are_counted_not_dropped():
    rows, unparsed = parse_lab_text("Potassium 6.8 mmol/L 3.5-5.1\nMystery Marker 55\nSodium 140 mmol/L 135-145")
    assert len(rows) == 2 and unparsed == 1


def test_inverted_range_gets_a_warning():
    analysis = _analyse("Ferritin 40 ng/mL 300-20")
    assert analysis.warnings


# ── range precedence, sex/age, flags ─────────────────────────────────────────


def test_printed_range_beats_registry():
    # registry Hb range for an adult male is 13-17.5: the printed 11-14 (child) must win
    analysis = _analyse("Hemoglobin 11.5 g/dL 11.0 - 14.0", sex="M", age=4)
    row = analysis.rows[0]
    assert row.flag == "NORMAL" and row.range_source == "report" and (row.ref_low, row.ref_high) == (11.0, 14.0)


def test_sex_is_never_assumed():
    analysis = _analyse("Hemoglobin 12.4 g/dL", sex=None, age=None)
    row = analysis.rows[0]
    assert row.flag == "UNKNOWN" and row.range_source == "none"
    assert any("no reference range" in w for w in analysis.warnings)
    label, score, uncertain = apply_risk_floor(analysis, "low", 5.0)
    assert label == "unknown" and score is None and uncertain


def test_printed_range_is_used_when_sex_is_unknown():
    row = _analyse("Hemoglobin 12.4 g/dL 12.0 - 15.5", sex=None).rows[0]
    assert row.flag == "NORMAL" and row.range_source == "report"


def test_registry_range_used_when_sex_and_age_are_known():
    male = _analyse("Hemoglobin 12.4 g/dL", sex="M", age=40).rows[0]
    female = _analyse("Hemoglobin 12.4 g/dL", sex="F", age=40).rows[0]
    assert male.flag == "LOW" and female.flag == "NORMAL" and male.range_source == "registry"


def test_sex_labelled_printed_range_needs_the_patients_sex():
    text = "Uric Acid 6.5 mg/dL Male: 3.4 - 7.0 Female: 2.4 - 6.0"
    assert _analyse(text, sex="M").rows[0].flag == "NORMAL"
    assert _analyse(text, sex="F").rows[0].flag == "HIGH"
    unknown = _analyse(text, sex=None)
    assert unknown.rows[0].flag == "UNKNOWN" and unknown.warnings


@pytest.mark.parametrize("line,flag", [
    ("HbA1c 5.7 % <5.7", "HIGH"),            # strict: 5.7 itself is not normal
    ("HbA1c 5.6 % <5.7", "NORMAL"),
    ("HDL Cholesterol 40 mg/dL >40", "LOW"),
    ("Troponin I <0.01 ng/mL 0.00-0.04", "NORMAL"),
    ("Troponin I >50 ng/mL <0.04", "CRITICAL_HIGH"),
    ("TSH >100 uIU/mL 0.4-4.0", "HIGH"),
    ("CRP <5 mg/L 0-10", "NORMAL"),
])
def test_qualifier_and_strict_flags(line, flag):
    assert _analyse(line, sex="M", age=40).rows[0].flag == flag


def test_qualifier_that_cannot_decide_is_unknown():
    # "<20" against a lower bound of 30: the true value could be anywhere below 20 -> LOW is certain,
    # but ">20" against 0.5-... style ambiguity must not be called normal
    row = _analyse("D-Dimer <20 ug/mL 0-0.5").rows[0]
    assert row.flag in ("UNKNOWN", "NORMAL") and row.flag != "HIGH"
    row = _analyse("Ferritin <300 ng/mL 20-250").rows[0]
    assert row.flag == "UNKNOWN"


def test_qualitative_results_are_not_normal_by_default():
    analysis = _analyse("HIV 1&2 Reactive\nHBsAg Non Reactive\nBlood Group Interesting")
    by = _by_name(analysis)
    assert by["HIV 1&2"].flag == "POSITIVE"
    assert by["HBsAg"].flag == "NEGATIVE"
    unknown = [r for r in analysis.rows if r.flag == "UNKNOWN"]
    assert not any(r.flag == "NORMAL" for r in analysis.rows)
    assert unknown == [] or analysis.warnings


def test_printed_flag_opposite_to_computed_is_flagged_unknown_with_warning():
    row = _analyse("Hemoglobin 9.2 H g/dL 13.0-17.0").rows[0]
    assert row.flag == "UNKNOWN" and row.discrepancy


def test_printed_flag_with_in_range_value_is_never_silently_normal():
    row = _analyse("Potassium 4.2 H mmol/L 3.5-5.1").rows[0]
    assert row.flag != "NORMAL" and row.discrepancy


def test_critical_thresholds_fire_independent_of_range_source_and_name():
    for line in ["K 6.9", "Serum Potassium 7.2 mmol/L", "Potassium 6.9 mmol/L 3.5-8.0",
                 "Platelet Count 12000 /cumm", "TLC 0.8 thousand/uL", "Hb 6.1 g/dL"]:
        analysis = _analyse(line, sex="M", age=40)
        assert analysis.rows[0].flag in ("CRITICAL_HIGH", "CRITICAL_LOW"), line
        assert analysis.critical_alerts


def test_unit_mismatch_row_is_unknown_not_scored():
    # no printed range to compare with: a unit the registry cannot convert is never scored
    analysis = _analyse("Ferritin 40 pmol/L")
    row = analysis.rows[0]
    assert row.value_type == "unit_mismatch" and row.flag == "UNKNOWN" and analysis.warnings
    # a range printed in a different unit than the result cannot be used either
    analysis = _analyse("Ferritin 40 pmol/L 30-400 ng/mL")
    assert analysis.rows[0].flag == "UNKNOWN" and analysis.warnings


def test_unconvertible_unit_with_a_range_in_the_same_unit_is_scored_on_the_printed_range_only():
    # changed expectation (round 5, NEW-8): "Ferritin 40 pmol/L 30-400" used to be UNKNOWN. The report printed its
    # range in the very unit of the result, so the comparison is valid without any conversion. The row keeps
    # value_type "unit_mismatch" (excluded from the canonical feature dict), shows a warning and registry
    # criticals are NOT evaluated (the unit is unknown).
    row = _analyse("Ferritin 40 pmol/L 30-400").rows[0]
    assert row.flag == "NORMAL" and row.value_type == "unit_mismatch" and row.range_source == "report"
    assert row.unit == "pmol/L" and row.critical is None
    analysis = _analyse("Ferritin 12 pmol/L 30-400")
    assert analysis.rows[0].flag == "LOW" and analysis.warnings


def test_dedupe_same_analyte_same_value_but_keep_different_values_with_a_warning():
    analysis = _analyse("Direct Bilirubin 1.0 mg/dL 0.0-0.3\nBilirubin Direct 1.0 mg/dL 0.0-0.3")
    assert len(analysis.rows) == 1
    analysis = _analyse("Urea 40 mg/dL 15-45\nBlood Urea 60 mg/dL 15-45")
    assert len(analysis.rows) == 2 and analysis.warnings


def test_severity_by_percent_deviation():
    mild = _analyse("Albumin 3.3 g/dL 3.5-5.0").rows[0]
    high = _analyse("ALT 400 U/L 7-56").rows[0]
    assert mild.severity == "MILD" and high.severity == "HIGH"


# ── risk floor ───────────────────────────────────────────────────────────────


def test_risk_floor_critical_high_moderate_normal():
    crit = _analyse("Potassium 6.9 mmol/L 3.5-5.1")
    assert apply_risk_floor(crit, "low", 3.0)[:2] == ("critical", 90.0)
    assert apply_risk_floor(crit, "critical", 99.0)[:2] == ("critical", 99.0)
    high = _analyse("ALT 400 U/L 7-56")
    assert apply_risk_floor(high, "low", 3.0)[:2] == ("high", 70.0)
    mod = _analyse("Albumin 3.3 g/dL 3.5-5.0")
    assert apply_risk_floor(mod, "low", 3.0)[:2] == ("moderate", 40.0)
    ok = _analyse("Hemoglobin 14 g/dL 13-17")
    assert apply_risk_floor(ok, "low", 3.0) == ("low", 3.0, False)


def test_unassessed_rows_never_read_as_low():
    unknown = _analyse("Ferritin 40 pmol/L 30-400")
    label, score, uncertain = apply_risk_floor(unknown, "low", 5.0)
    assert (label, score, uncertain) == ("unknown", None, True)
    unread = _analyse("Potassium 4.0 mmol/L 3.5-5.1\nMystery Marker 55")
    label, score, uncertain = apply_risk_floor(unread, "low", 5.0)
    assert label != "low" and uncertain


def test_explanation_starts_with_critical_alerts_and_ends_with_not_assessed():
    analysis = _analyse("Potassium 6.9 mmol/L 3.5-5.1\nFerritin 40 pmol/L 30-400")
    text = decorate_explanation("Model summary.", analysis)
    assert text.startswith("CRITICAL: Potassium")
    assert text.rstrip().splitlines()[-1].startswith("Not assessed:")
    assert decorate_explanation("Model summary.", _analyse("Hemoglobin 14 g/dL 13-17")) == "Model summary."


# ── acceptance: every row of every case ──────────────────────────────────────


def _load_cases():
    cases = []
    for path in sorted(CASE_DIR.glob("*.txt")):
        for chunk in path.read_text(encoding="utf-8").split("\n---\n"):
            head, _, body = chunk.strip("\n").partition("\n")
            kind, _, title = head.partition("|")
            cases.append((f"{path.stem}|{title}", kind.strip().lower(), body))
    return cases


CASES = _load_cases()
SEX = {"male": "M", "female": "F"}
SEX_ARG = {"male": "male", "female": "female"}


def _sex_age(key, kind):
    """Header kinds: 'male', 'female', 'csv', 'tsv', or 'sex,age' (either may be 'none').
    The round-4 reviewer's harness assumed age 40 when none was given; older cases pass no age."""
    sex, _, age = kind.partition(",")
    default = 40 if key.startswith("review4_") else None
    age_n = None if age.strip() == "none" else (int(age) if age.strip() else default)
    return SEX.get(sex.strip()), SEX_ARG.get(sex.strip()), age_n


def _case_file(tmp_path, kind, body):
    ext = {"csv": ".csv", "tsv": ".tsv"}.get(kind, ".txt")
    path = tmp_path / f"case{ext}"
    path.write_text(body, encoding="utf-8")
    return path


def _run_case(monkeypatch, tmp_path, kind, body, gender=None, age=None):
    """Real pipeline run. CSV/TSV go through the real file path; plain-text
    cases stand in for OCR output (the pipeline only accepts pdf/csv/tsv/images)."""
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    path = _case_file(tmp_path, kind, body)
    if path.suffix == ".txt":
        class _Ocr:
            raw_text = body
            confidence = 1.0
            pages = [body]

        monkeypatch.setattr(pipeline, "_run_ocr_safe", lambda p: _Ocr())
        monkeypatch.setattr(pipeline, "_validate_file_safe", lambda p: pipeline._ValidationResult(valid=True, reason=""))
    return pipeline.run_report_pipeline(str(path), report_type="lab", gender=gender, age=age)


MERGED_URINE_CASES = {"review4_a4|C3 mixed blood + urine routine"}


def _anomaly_count(rows):
    return sum(1 for _, _, _, flag in rows if flag not in ("NORMAL", "UNKNOWN", "NEGATIVE", "INFO"))


def test_every_case_file_entry_has_an_expectation_and_vice_versa():
    assert {c[0] for c in CASES} == set(EXPECTED)


@pytest.mark.parametrize("key,kind,body", CASES, ids=[c[0] for c in CASES])
def test_acceptance_engine_rows(tmp_path, key, kind, body):
    """Every printed row: name as printed, canonical value (+-1%), unit, flag; minimum risk label."""
    from app.services.preprocessor import run_ocr

    label, expected_rows = EXPECTED[key]
    text = run_ocr(str(_case_file(tmp_path, kind, body))).raw_text
    sex, _, age = _sex_age(key, kind)
    analysis = _analyse(text, sex=sex, age=age)
    got = [(r.display_name, r.value if r.value is not None else r.text_value, r.unit, r.flag) for r in analysis.rows]
    assert len(got) == len(expected_rows), (key, got)
    for (name, value, unit, flag), (e_name, e_value, e_unit, e_flag) in zip(got, expected_rows):
        assert name == e_name, key
        assert flag == e_flag, (key, name, flag, e_flag)
        assert unit_key(unit) == unit_key(e_unit), (key, name, unit, e_unit)
        if isinstance(e_value, str):
            assert value == e_value, (key, name)
        else:
            assert value == pytest.approx(e_value, rel=0.01, abs=1e-6), (key, name, value, e_value)
    floor_label, _, _ = apply_risk_floor(analysis, "low", 5.0)
    assert RANK[floor_label] >= RANK[label], (key, floor_label, label)


@pytest.mark.parametrize("key,kind,body", CASES, ids=[c[0] for c in CASES])
def test_acceptance_through_the_pipeline(tmp_path, monkeypatch, key, kind, body):
    """Same cases through run_report_pipeline: response rows, anomalies and the risk floor."""
    label, expected_rows = EXPECTED[key]
    _, gender, age = _sex_age(key, kind)
    result = _run_case(monkeypatch, tmp_path, kind, body, gender=gender, age=age)
    assert result.status == "completed", result.error
    got_flags = [r["flag"] for r in result.lab_values]
    got_names = [r["test"] for r in result.lab_values]
    if key in MERGED_URINE_CASES:  # the pipeline appends the urinalysis rows after the blood rows
        got_flags, got_names = got_flags[:len(expected_rows)], got_names[:len(expected_rows)]
    assert got_flags == [flag for _, _, _, flag in expected_rows], key
    assert got_names == [name for name, _, _, _ in expected_rows], key
    assert len(result.anomalies) == _anomaly_count(expected_rows), key
    if label == "unknown":
        assert result.risk_label == "unknown" and result.risk_score is None and result.uncertainty_flag
    else:
        assert RANK[str(result.risk_label).lower()] >= RANK[label], (key, result.risk_label)
    if label == "critical":
        assert str(result.risk_label).lower() == "critical" and result.risk_score >= 90, key
        assert (result.explanation or "").startswith("CRITICAL:"), key
    if label == "high":
        assert result.risk_score is not None and result.risk_score >= 70, key
    assert isinstance(result.warnings, list)


def test_c4_every_critical_case_is_critical_with_score_at_least_90(tmp_path, monkeypatch):
    c4 = [c for c in CASES if c[0].startswith("c4|") and "normal" not in c[0]]
    assert len(c4) == 6
    for key, kind, body in c4:
        result = _run_case(monkeypatch, tmp_path, kind, body, gender="male")
        assert result.risk_label == "critical" and result.risk_score >= 90, key
        assert result.lab_values and all(r["flag"].startswith("CRITICAL") for r in result.lab_values), key


# ── PDFs ─────────────────────────────────────────────────────────────────────

PDF_EXPECTED = {
    "labs_narrow.pdf": {"Potassium": "CRITICAL_HIGH", "Sodium": "CRITICAL_LOW", "Creatinine": "HIGH",
                        "Hemoglobin": "CRITICAL_LOW"},
    "land_conv.pdf": {"Haemoglobin": "CRITICAL_LOW", "Serum Potassium": "CRITICAL_HIGH",
                      "Platelet Count": "CRITICAL_LOW", "Serum Creatinine": "HIGH", "Troponin I": "CRITICAL_HIGH",
                      "HIV": "POSITIVE"},
    "land_noref.pdf": {"Glucose": "CRITICAL_LOW", "Potassium": "CRITICAL_HIGH", "Sodium": "CRITICAL_LOW"},
    "land_si.pdf": {"Glucose": "CRITICAL_HIGH", "Creatinine": "HIGH", "Hemoglobin": "CRITICAL_LOW",
                    "Potassium": "NORMAL"},
    "land_si_normal.pdf": {"Glucose": "NORMAL", "Creatinine": "NORMAL", "Hemoglobin": "NORMAL",
                           "Cholesterol": "NORMAL"},
    "port_3col.pdf": {"Potassium": "CRITICAL_HIGH", "Sodium": "CRITICAL_LOW", "Creatinine": "HIGH",
                      "Haemoglobin": "CRITICAL_LOW", "Platelet Count": "CRITICAL_LOW"},
    "port_3col_b.pdf": {"Potassium": "CRITICAL_HIGH", "Sodium": "CRITICAL_LOW", "Calcium": "CRITICAL_LOW",
                        "Urea": "HIGH"},
    "port_flag.pdf": {"Haemoglobin": "LOW", "Potassium": "CRITICAL_HIGH", "Sodium": "LOW",
                      "Glucose Fasting": "CRITICAL_HIGH", "Chloride": "NORMAL"},
    "port_std.pdf": {"Haemoglobin": "CRITICAL_LOW", "Total Leucocyte Count": "CRITICAL_LOW",
                     "Platelet Count": "CRITICAL_LOW", "Serum Potassium": "CRITICAL_HIGH",
                     "Serum Creatinine": "HIGH", "INR": "CRITICAL_HIGH", "Free T4": "LOW", "Vitamin B12": "LOW"},
}


def _run_pdf(monkeypatch, name, gender="male", age=None):
    import app.pipeline as pipeline

    monkeypatch.setattr(pipeline, "_rag_service", lambda: None)
    return pipeline.run_report_pipeline(str(PDF_DIR / name), report_type="lab", gender=gender, age=age)


@pytest.mark.parametrize("name", sorted(PDF_EXPECTED))
def test_pdf_rows_are_flagged_in_the_right_direction(monkeypatch, name):
    pytest.importorskip("fitz")
    result = _run_pdf(monkeypatch, name)
    assert result.status == "completed", result.error
    assert {r["test"]: r["flag"] for r in result.lab_values} == PDF_EXPECTED[name]
    any_abnormal = any(f != "NORMAL" for f in PDF_EXPECTED[name].values())
    if any_abnormal:
        assert result.risk_label in ("moderate", "high", "critical")
        assert result.anomalies
    else:
        assert result.risk_label == "low" and not result.anomalies
    if any(f.startswith("CRITICAL") for f in PDF_EXPECTED[name].values()):
        assert result.risk_label == "critical" and result.risk_score >= 90


# Round-4 reviewer PDFs (tests/fixtures/lab_pdfs/review4_*.pdf), run as a 40-year-old male like the reviewer's harness.
# Rows are (name, flag) in printed order; the pipeline may append merged urinalysis rows after them.
REVIEW4_PDF_EXPECTED = {
    "review4_cumul.pdf": ("critical", [("Potassium", "CRITICAL_HIGH"), ("Haemoglobin", "CRITICAL_LOW"), ("Sodium", "NORMAL")]),
    "review4_cumul_named.pdf": ("critical", [("Potassium", "CRITICAL_HIGH"), ("Haemoglobin", "CRITICAL_LOW"), ("Sodium", "NORMAL")]),
    "review4_cumul_scan.pdf": ("critical", [("Potassium", "CRITICAL_HIGH"), ("Haemoglobin", "CRITICAL_LOW"), ("Sodium", "NORMAL")]),
    "review4_oneline_cumul.pdf": ("critical", [("Potassium", "CRITICAL_HIGH"), ("Haemoglobin", "CRITICAL_LOW"), ("Sodium", "NORMAL")]),
    "review4_diab.pdf": ("critical", [("Fasting Blood Sugar", "CRITICAL_HIGH"), ("HbA1c", "HIGH"), ("Serum Creatinine", "HIGH"),
                                      ("Urine Microalbumin", "HIGH"), ("Urine Albumin/Creatinine Ratio", "HIGH")]),
    "review4_interp.pdf": ("high", [("25-OH Vitamin D", "LOW"), ("eGFR", "LOW"), ("HDL Cholesterol", "LOW"), ("Creatinine", "NORMAL")]),
    "review4_oneline_interp.pdf": ("high", [("eGFR", "LOW"), ("Vitamin D (25-OH)", "LOW"), ("Creatinine", "NORMAL")]),
    "review4_mixed_urine.pdf": ("critical", [("Haemoglobin", "CRITICAL_LOW"), ("Platelet Count", "CRITICAL_LOW"),
                                             ("Serum Potassium", "CRITICAL_HIGH"), ("Specific Gravity", "NORMAL"), ("pH", "NORMAL"),
                                             ("Urine Protein", "NEGATIVE"), ("Pus Cells", "NORMAL")]),
    "review4_oneline_serology.pdf": ("high", [("Haemoglobin", "NORMAL"), ("HBsAg", "POSITIVE"), ("Anti HCV", "POSITIVE"),
                                              ("HIV 1 & 2 Antibodies", "NEGATIVE")]),
    "review4_oneline_two.pdf": ("critical", [("Sodium", "NORMAL"), ("Potassium", "CRITICAL_HIGH"), ("Chloride", "NORMAL"),
                                             ("Haemoglobin", "CRITICAL_LOW"), ("Urea", "NORMAL"), ("Calcium", "NORMAL")]),
    "review4_twopanel.pdf": ("critical", [("Sodium", "NORMAL"), ("Haemoglobin", "CRITICAL_LOW"), ("Chloride", "NORMAL"),
                                          ("Platelet Count", "CRITICAL_LOW"), ("Urea", "NORMAL"), ("Potassium", "CRITICAL_HIGH")]),
    "review4_twopanel_scan.pdf": ("critical", [("Sodium", "NORMAL"), ("Haemoglobin", "CRITICAL_LOW"), ("Chloride", "NORMAL"),
                                               ("Platelet Count", "CRITICAL_LOW"), ("Urea", "NORMAL"), ("Potassium", "CRITICAL_HIGH")]),
    "review4_real_normal.pdf": ("low", [("Haemoglobin", "NORMAL"), ("Total Leucocyte Count", "NORMAL"), ("Platelet Count", "NORMAL"),
                                        ("Serum Creatinine", "NORMAL"), ("Serum Potassium", "NORMAL"), ("HbA1c", "NORMAL")]),
}


def test_every_review4_pdf_has_an_expectation():
    assert {p.name for p in PDF_DIR.glob("review4_*.pdf")} == set(REVIEW4_PDF_EXPECTED)


@pytest.mark.parametrize("name", sorted(REVIEW4_PDF_EXPECTED))
def test_review4_pdf(monkeypatch, name):
    pytest.importorskip("fitz")
    if name.endswith("_scan.pdf"):
        from app.services.preprocessor import run_ocr

        try:
            text = run_ocr(str(PDF_DIR / name)).raw_text
        except Exception:  # OCR engine not installed in this environment
            text = ""
        if "Potassium" not in text and "Sodium" not in text:
            pytest.skip("scan OCR is not available here")
    label, rows = REVIEW4_PDF_EXPECTED[name]
    result = _run_pdf(monkeypatch, name, gender="male", age=40)
    assert result.status == "completed", result.error
    got = [(r["test"], r["flag"]) for r in result.lab_values][:len(rows)]
    assert got == rows, (name, [(r["test"], r["flag"]) for r in result.lab_values])
    if label == "low":
        assert result.risk_label == "low" and not result.anomalies
    else:
        assert RANK[str(result.risk_label).lower()] >= RANK[label], (name, result.risk_label)
    if label == "critical":
        assert result.risk_label == "critical" and result.risk_score >= 90 and result.explanation.startswith("CRITICAL:")
    # no merged urinalysis row may be scored as an abnormality. P2 (plans/P2.md 7.4/7.7): the legacy urinalysis
    # reader's rows are now engine-scored confirmed rows (they used to be unscored dicts with flag NORMAL and no
    # value_type), so the safety intent is kept as "never an anomaly flag", and each such row is marked as
    # coming from the legacy reader (provenance), which is why a clinician reviews it.
    extra = result.lab_values[len(rows):]
    assert all(r["flag"] in ("NORMAL", "NEGATIVE", "INFO", "UNKNOWN") and r.get("origin") == "legacy_urine"
               for r in extra), (name, extra)


def test_pdf_qualitative_and_qualified_results(monkeypatch):
    pytest.importorskip("fitz")
    result = _run_pdf(monkeypatch, "land_conv.pdf")
    rows = {r["test"]: r for r in result.lab_values}
    assert rows["HIV"]["flag"] == "POSITIVE" and rows["HIV"]["value_type"] == "qualitative"
    assert rows["Troponin I"]["flag"] == "CRITICAL_HIGH" and rows["Troponin I"]["qualifier"] == ">"
    assert any(a["direction"] == "POSITIVE" for a in result.anomalies)


def test_pdf_normal_si_report_has_no_phantom_conditions(monkeypatch):
    pytest.importorskip("fitz")
    result = _run_pdf(monkeypatch, "land_si_normal.pdf")
    rows = {r["test"]: r for r in result.lab_values}
    assert rows["Creatinine"]["result"] == pytest.approx(0.90, abs=0.01)
    assert rows["Creatinine"]["unit"] == "mg/dL" and rows["Creatinine"]["flag"] == "NORMAL"
    assert "Kidney Dysfunction" not in (result.extracted_entities or {}).get("conditions", [])
    assert result.anomalies == [] and result.risk_label == "low"


def test_pdf_si_report_is_converted(monkeypatch):
    pytest.importorskip("fitz")
    rows = {r["test"]: r for r in _run_pdf(monkeypatch, "land_si.pdf", gender="female").lab_values}
    assert rows["Glucose"]["result"] == pytest.approx(396.35, rel=0.01) and rows["Glucose"]["unit"] == "mg/dL"
    assert rows["Creatinine"]["result"] == pytest.approx(5.09, rel=0.01)
    assert rows["Hemoglobin"]["result"] == pytest.approx(6.5, rel=0.01)


# ── sex/age unknown, guards, API surface ─────────────────────────────────────


def _txt_pipeline(tmp_path, monkeypatch, text, gender=None, age=None):
    return _run_case(monkeypatch, tmp_path, "male", text, gender=gender, age=age)


def test_unknown_sex_without_range_is_unknown_with_a_warning(tmp_path, monkeypatch):
    result = _txt_pipeline(tmp_path, monkeypatch, "Hemoglobin 12.4 g/dL", gender=None)
    (row,) = result.lab_values
    assert row["flag"] == "UNKNOWN" and row["range_source"] == "none"
    assert result.warnings and result.uncertainty_flag
    assert result.risk_label == "unknown" and result.risk_score is None
    assert not result.anomalies


def test_unknown_sex_with_printed_range_uses_the_range(tmp_path, monkeypatch):
    result = _txt_pipeline(tmp_path, monkeypatch, "Hemoglobin 12.4 g/dL 12.0 - 15.5", gender=None)
    (row,) = result.lab_values
    assert row["flag"] == "NORMAL" and row["range_source"] == "report"
    # the missing sex/age is still disclosed
    assert any("sex" in w for w in result.warnings)


def test_gender_and_age_are_optional_everywhere():
    import inspect

    import app.pipeline as pipeline
    from app.workers import pipeline_tasks

    sig = inspect.signature(pipeline.run_report_pipeline).parameters
    assert sig["gender"].default is None and sig["age"].default is None
    task = pipeline_tasks.analyze_report_task
    task_sig = inspect.signature(task.__wrapped__ if hasattr(task, "__wrapped__") else task.run).parameters
    assert task_sig["gender"].default is None and task_sig["age"].default is None
    source = Path(pipeline_tasks.__file__).parent.parent.joinpath("api/v1/routes/reports.py").read_text()
    assert "gender: Optional[str] = Form(None)" in source and "age: Optional[int] = Form(None)" in source


def test_critical_value_without_any_sex_age_or_range_still_alerts(tmp_path, monkeypatch):
    result = _txt_pipeline(tmp_path, monkeypatch, "Potassium 6.9\nHemoglobin 6.1", gender=None)
    flags = {r["test"]: r["flag"] for r in result.lab_values}
    assert flags["Potassium"] == "CRITICAL_HIGH" and flags["Hemoglobin"] == "CRITICAL_LOW"
    assert result.risk_label == "critical" and result.risk_score >= 90


@pytest.mark.parametrize("text", [
    "Patient ref 12345\nVisit 2026-01-04",
    "Age 54 Yrs",
    "Ward 7 Bed 12 (Ref: 3-9)",
    "Date 12/03/2026",
])
def test_guard_texts_yield_the_insufficient_evidence_result(tmp_path, monkeypatch, text):
    result = _txt_pipeline(tmp_path, monkeypatch, text, gender="male")
    assert result.lab_values == [] and result.anomalies == []
    assert result.risk_label == "unknown" and result.risk_score is None and result.uncertainty_flag
    assert "no lab values could be extracted" in result.explanation


def test_unread_numeric_line_is_reported_not_swallowed(tmp_path, monkeypatch):
    result = _txt_pipeline(tmp_path, monkeypatch, "Potassium 4.2 mmol/L 3.5-5.1\nMystery Marker 55")
    assert result.risk_label == "unknown" and result.uncertainty_flag
    assert any("could not be read" in w for w in result.warnings)
    assert [r["test"] for r in result.lab_values] == ["Potassium"]


def test_fixture_directories_are_populated():
    assert len(list(CASE_DIR.glob("*.txt"))) == 14
    assert len(list(PDF_DIR.glob("*.pdf"))) >= 22
    assert len(list(GEN_DIR.glob("*.txt"))) >= 8


# ── generalisation set: our own layouts/analytes per rule (tests/fixtures/lab_cases/generalisation) ───────────────

GEN_DIR = CASE_DIR / "generalisation"


def _load_generalisation():
    items = []
    for path in sorted(GEN_DIR.glob("*.txt")):
        for chunk in path.read_text(encoding="utf-8").split("\n@@ "):
            chunk = chunk.strip("\n")
            if not chunk or not chunk.startswith(("@@", "R")) and "|" not in chunk.splitlines()[0]:
                continue
            if chunk.startswith("@@ "):
                chunk = chunk[3:]
            lines = chunk.splitlines()
            head = [h.strip() for h in lines[0].split("|", 3)]
            if len(head) < 4 or not head[0].startswith("R"):
                continue
            rule, sex, age, label = head
            body, expect = [], []
            for ln in lines[1:]:
                (expect if ln.startswith("=> ") else body).append(ln[3:] if ln.startswith("=> ") else ln)
            items.append((rule, None if sex == "-" else sex, None if age == "-" else int(age), label, "\n".join(body), expect))
    return items


GEN_ITEMS = _load_generalisation()


def _gen_check(sex, age, body, expect, label):
    analysis = _analyse(body, sex=sex, age=age)
    rows = analysis.rows
    row_expect = [e for e in expect if "|" in e]
    flags = [e for e in expect if "|" not in e]
    got = [(r.display_name, r.flag, r.value if r.value is not None else r.text_value) for r in rows]
    assert len(rows) == len(row_expect), (label, got)
    for row, e in zip(rows, row_expect):
        parts = [x.strip() for x in e.split("|")]
        name, flag = parts[0], parts[1]
        assert name.lower() in row.display_name.lower(), (label, row.display_name, name)
        assert row.flag == flag, (label, row.display_name, row.flag, flag, row.note, analysis.warnings)
        if len(parts) > 2 and parts[2]:
            assert row.value == pytest.approx(float(parts[2]), rel=0.01, abs=1e-6), (label, row.display_name, row.value, parts[2])
    for f in flags:
        if f == "warn":
            assert analysis.warnings, label
        elif f == "nowarn":
            assert not analysis.warnings, (label, analysis.warnings)
        elif f.startswith("risk"):
            op, want = ("=", f[5:]) if f.startswith("risk=") else (">=", f[6:])
            label_out, _, _ = apply_risk_floor(analysis, "low", 5.0)
            if op == "=":
                assert label_out == want or (want == "low" and label_out in ("low", "normal")), (label, label_out, want)
            else:
                assert RANK[label_out] >= RANK[want], (label, label_out, want)


def test_generalisation_set_is_big_enough_per_rule():
    counts = {}
    for rule, *_ in GEN_ITEMS:
        counts[rule] = counts.get(rule, 0) + 1
    assert set(counts) == {"R2", "R3", "R4", "R5", "R6", "R7", "R8", "R10"}, counts
    assert all(n >= 30 for n in counts.values()), counts


@pytest.mark.parametrize("rule,sex,age,label,body,expect", GEN_ITEMS,
                         ids=[f"{g[0]}-{i}-{g[3][:40]}" for i, g in enumerate(GEN_ITEMS)])
def test_generalisation(rule, sex, age, label, body, expect):
    _gen_check(sex, age, body, expect, f"{rule}: {label}")


# ── expected values (every row of every case) ────────────────────────────────

EXPECTED = {
    'c1|F1 units': (
        'critical',
        [
            ('Sodium', 118.0, 'mEq/L', 'CRITICAL_LOW'),
            ('Potassium', 6.9, 'mEq/L', 'CRITICAL_HIGH'),
            ('Chloride', 80.0, 'mmol/L', 'LOW'),
            ('Bicarbonate', 10.0, 'mEq/L', 'LOW'),
            ('ALT', 400.0, 'IU/L', 'HIGH'),
            ('TSH', 25.0, 'µIU/mL', 'HIGH'),
            ('Hemoglobin', 6.5, 'g%', 'CRITICAL_LOW'),
            ('WBC', 25000.0, 'cells/cumm', 'HIGH'),
            ('Iron', 20.0, 'mcg/dL', 'LOW'),
        ],
    ),
    'c1|F3 SI': (
        'critical',
        [
            ('Blood Glucose', 396.352, 'mg/dL', 'CRITICAL_HIGH'),
            ('Serum Creatinine', 5.0893, 'mg/dL', 'HIGH'),
            ('Haemoglobin', 6.5, 'g/dL', 'CRITICAL_LOW'),
        ],
    ),
    'c1|F3 SI b': (
        'high',
        [
            ('Fasting Glucose', 163.946, 'mg/dL', 'HIGH'),
        ],
    ),
    'c1|F9 dedup': (
        'moderate',
        [
            ('Direct Bilirubin', 0.3, 'mg/dL', 'NORMAL'),
            ('Indirect Bilirubin', 0.3, 'mg/dL', 'NORMAL'),
            ('LDL Cholesterol', 30.0, 'mg/dL', 'NORMAL'),
            ('VLDL Cholesterol', 30.0, 'mg/dL', 'NORMAL'),
            ('Albumin', 3.0, 'g/dL', 'LOW'),
            ('Microalbumin', 30.0, 'mg/L', 'NORMAL'),
        ],
    ),
    'c1|F2 cell-per-line': (
        'critical',
        [
            ('Hemoglobin', 6.5, 'g/dL', 'CRITICAL_LOW'),
            ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Glucose', 450.0, 'mg/dL', 'CRITICAL_HIGH'),
        ],
    ),
    'c1|authors (a) layout': (
        'critical',
        [
            ('Hemoglobin', 9.2, 'g/dL', 'LOW'),
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
            ('Creatinine', 4.2, 'mg/dL', 'HIGH'),
            ('Glucose', 320.0, 'mg/dL', 'CRITICAL_HIGH'),
        ],
    ),
    'c2|WBC thousands normal': (
        'low',
        [
            ('WBC', 7200.0, '/µL', 'NORMAL'),
            ('Platelets', 250000.0, '/µL', 'NORMAL'),
            ('Hemoglobin', 14.5, 'g/dL', 'NORMAL'),
        ],
    ),
    'c2|WBC 900 /uL true leukopenia': (
        'critical',
        [
            ('WBC', 900.0, '/µL', 'CRITICAL_LOW'),
        ],
    ),
    'c2|ANC 500 neutropenia': (
        'critical',
        [
            ('Neutrophils Absolute', 500.0, '/µL', 'CRITICAL_LOW'),
            ('Lymphocytes Absolute', 600.0, '/µL', 'LOW'),
        ],
    ),
    'c2|Platelets 15000 /uL': (
        'critical',
        [
            ('Platelet Count', 15000.0, '/µL', 'CRITICAL_LOW'),
        ],
    ),
    'c2|Platelets 15 x10^3': (
        'critical',
        [
            ('Platelet Count', 15000.0, '/µL', 'CRITICAL_LOW'),
        ],
    ),
    'c2|Platelets lakhs': (
        'critical',
        [
            ('Platelet Count', 15000.0, '/µL', 'CRITICAL_LOW'),
        ],
    ),
    'c2|VLDL high': (
        'high',
        [
            ('VLDL Cholesterol', 95.0, 'mg/dL', 'HIGH'),
            ('Non-HDL Cholesterol', 210.0, 'mg/dL', 'HIGH'),
        ],
    ),
    'c2|urea vs BUN': (
        'high',
        [
            ('Blood Urea', 60.0, 'mg/dL', 'HIGH'),
            ('Urea', 72.072, 'mg/dL', 'HIGH'),
        ],
    ),
    'c2|PP glucose': (
        'high',
        [
            ('PP Glucose', 135.0, 'mg/dL', 'NORMAL'),
            ('Post Prandial Blood Sugar', 240.0, 'mg/dL', 'HIGH'),
        ],
    ),
    'c2|free thyroid': (
        'high',
        [
            ('Free T4', 0.4, 'ng/dL', 'LOW'),
            ('Free T3', 1.0, 'pg/mL', 'LOW'),
            ('T3 Total', 40.0, 'ng/dL', 'LOW'),
            ('T4 Total', 2.0, 'µg/dL', 'LOW'),
        ],
    ),
    'c2|calcium mmol': (
        'critical',
        [
            ('Calcium', 14.4288, 'mg/dL', 'CRITICAL_HIGH'),
            ('Magnesium', 0.9724, 'mg/dL', 'CRITICAL_LOW'),
        ],
    ),
    'c2|troponin': (
        'critical',
        [
            ('Troponin I', 2.5, 'ng/mL', 'CRITICAL_HIGH'),
            ('Troponin T', 0.5, 'ng/mL', 'CRITICAL_HIGH'),
            ('hs Troponin I', 0.85, 'ng/mL', 'CRITICAL_HIGH'),
        ],
    ),
    'c2|INR and PT': (
        'critical',
        [
            ('INR', 6.5, '', 'CRITICAL_HIGH'),
            ('PT', 45.0, 'sec', 'HIGH'),
        ],
    ),
    'c2|HbA1c IFCC': (
        'high',
        [
            ('HbA1c', 9.0125, '%', 'HIGH'),
        ],
    ),
    'c2|creatinine critical variants': (
        'critical',
        [
            ('S. Creatinine', 12.5, 'mg/dL', 'CRITICAL_HIGH'),
            ('Serum Potassium', 7.1, 'mmol/L', 'CRITICAL_HIGH'),
            ('Serum Sodium', 112.0, 'mmol/L', 'CRITICAL_LOW'),
            ('Random Blood Sugar', 520.0, 'mg/dL', 'CRITICAL_HIGH'),
        ],
    ),
    'c2|vit D': (
        'high',
        [
            ('25-OH Vitamin D', 8.0, 'ng/mL', 'LOW'),
            ('Vitamin D', 8.0128, 'ng/mL', 'LOW'),
            ('Vitamin B12', 121.95, 'pg/mL', 'LOW'),
        ],
    ),
    'c3|flags H/L between value and unit': (
        'critical',
        [
            ('Hemoglobin', 9.2, 'g/dL', 'LOW'),
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
            ('Glucose', 320.0, 'mg/dL', 'CRITICAL_HIGH'),
        ],
    ),
    'c3|flags star, units glued': (
        'critical',
        [
            ('Hemoglobin', 9.2, 'g/dL', 'LOW'),
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
            ('Creatinine', 4.2, 'mg/dL', 'HIGH'),
        ],
    ),
    'c3|Bio Ref Interval layout': (
        'high',
        [
            ('Haemoglobin', 9.2, 'g/dL', 'LOW'),
            ('Total Leucocyte Count', 2100.0, '/µL', 'LOW'),
            ('Platelet Count', 45000.0, '/µL', 'LOW'),
            ('Serum Creatinine', 4.2, 'mg/dL', 'HIGH'),
        ],
    ),
    'c3|one-sided ranges': (
        'critical',
        [
            ('HbA1c', 9.4, '%', 'HIGH'),
            ('LDL Cholesterol', 190.0, 'mg/dL', 'HIGH'),
            ('HDL Cholesterol', 25.0, 'mg/dL', 'LOW'),
            ('Triglycerides', 650.0, 'mg/dL', 'CRITICAL_HIGH'),
            ('SGPT', 400.0, 'U/L', 'HIGH'),
        ],
    ),
    'c3|qualifiers': (
        'high',
        [
            ('Troponin I', 0.01, 'ng/mL', 'NORMAL'),
            ('TSH', 100.0, 'µIU/mL', 'HIGH'),
            ('CRP', 5.0, 'mg/L', 'NORMAL'),
            ('D-Dimer', 20.0, 'µg/mL', 'HIGH'),
        ],
    ),
    'c3|range before unit': (
        'critical',
        [
            ('Hemoglobin', 9.2, 'g/dL', 'LOW'),
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
        ],
    ),
    'c3|range with unit repeated / brackets': (
        'critical',
        [
            ('Hemoglobin', 9.2, 'g/dL', 'LOW'),
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
            ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
        ],
    ),
    'c3|no ranges at all': (
        'critical',
        [
            ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
            ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
            ('Creatinine', 9.8, 'mg/dL', 'HIGH'),
            ('Platelet Count', 12000.0, '/cumm', 'CRITICAL_LOW'),
        ],
    ),
    'c3|two-letter names': (
        'critical',
        [
            ('Na', 118.0, 'mmol/L', 'CRITICAL_LOW'),
            ('K', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Hb', 6.1, 'g/dL', 'CRITICAL_LOW'),
            ('Cl', 80.0, 'mmol/L', 'LOW'),
        ],
    ),
    'c3|date/noise lines': (
        'low',
        [
            ('Hemoglobin', 14.2, 'g/dL', 'NORMAL'),
        ],
    ),
    'c3|age-specific pediatric not handled?': (
        'low',
        [
            ('Hemoglobin', 11.2, 'g/dL', 'NORMAL'),
        ],
    ),
    'c3|percent glued and differential': (
        'high',
        [
            ('Neutrophils', 85.0, '%', 'HIGH'),
            ('Lymphocytes', 8.0, '%', 'LOW'),
            ('Eosinophils', 1.0, '%', 'NORMAL'),
        ],
    ),
    'c3|value 0 and decimals leading dot': (
        'critical',
        [
            ('Basophils', 0.0, '%', 'NORMAL'),
            ('Bilirubin Direct', 0.9, 'mg/dL', 'HIGH'),
            ('Bilirubin Total', 12.5, 'mg/dL', 'CRITICAL_HIGH'),
        ],
    ),
    'c4|variant names (six critical values)': (
        'critical',
        [
            ('Haemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
            ('Platelet Count', 12000.0, '/cumm', 'CRITICAL_LOW'),
            ('Serum Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Serum Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
            ('Blood Glucose', 480.0, 'mg/dL', 'CRITICAL_HIGH'),
            ('Total Bilirubin', 14.0, 'mg/dL', 'CRITICAL_HIGH'),
        ],
    ),
    'c4|canonical names (same values)': (
        'critical',
        [
            ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
            ('Platelets', 12000.0, '/cumm', 'CRITICAL_LOW'),
            ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
            ('Glucose', 480.0, 'mg/dL', 'CRITICAL_HIGH'),
            ('Bilirubin', 14.0, 'mg/dL', 'CRITICAL_HIGH'),
        ],
    ),
    'c4|single: Platelet Count 12000': (
        'critical',
        [
            ('Platelet Count', 12000.0, '/cumm', 'CRITICAL_LOW'),
        ],
    ),
    'c4|single: Serum Potassium 7.2': (
        'critical',
        [
            ('Serum Potassium', 7.2, 'mmol/L', 'CRITICAL_HIGH'),
        ],
    ),
    'c4|single: Potassium 7.2': (
        'critical',
        [
            ('Potassium', 7.2, 'mmol/L', 'CRITICAL_HIGH'),
        ],
    ),
    'c4|single: TLC 0.8 thousand': (
        'critical',
        [
            ('Total Leucocyte Count', 800.0, '/µL', 'CRITICAL_LOW'),
        ],
    ),
    'c4|normal CBC in thousands': (
        'low',
        [
            ('Hemoglobin', 14.8, 'g/dL', 'NORMAL'),
            ('WBC', 6500.0, '/µL', 'NORMAL'),
            ('Platelets', 250000.0, '/µL', 'NORMAL'),
        ],
    ),
    'c5|direct bilirubin word order': (
        'high',
        [
            ('Bilirubin Direct', 1.0, 'mg/dL', 'HIGH'),
            ('Bilirubin Indirect', 0.4, 'mg/dL', 'NORMAL'),
        ],
    ),
    'c5|MCHC spelled out': (
        'low',
        [
            ('Mean Corpuscular Hemoglobin Concentration', 33.0, 'g/dL', 'NORMAL'),
            ('Mean Corpuscular Volume', 88.0, 'fL', 'NORMAL'),
        ],
    ),
    'c5|ionized calcium': (
        'low',
        [
            ('Ionized Calcium', 1.2, 'mmol/L', 'NORMAL'),
        ],
    ),
    'c5|urine creatinine and ratios': (
        'low',
        [
            ('Urine Creatinine', 150.0, 'mg/dL', 'NORMAL'),
            ('BUN/Creatinine Ratio', 15.0, 'ratio', 'NORMAL'),
        ],
    ),
    'c5|WBC cells/cumm 900': (
        'critical',
        [
            ('Total WBC Count', 900.0, 'cells/cumm', 'CRITICAL_LOW'),
            ('Absolute Neutrophil Count', 300.0, 'cells/cumm', 'CRITICAL_LOW'),
        ],
    ),
    'c5|glucose random normal': (
        'low',
        [
            ('Glucose (Random)', 128.0, 'mg/dL', 'NORMAL'),
        ],
    ),
    'c5|hba1c bracket name': (
        'high',
        [
            ('Glycosylated Hemoglobin (HbA1c)', 9.4, '%', 'HIGH'),
        ],
    ),
    'c5|CK': (
        'high',
        [
            ('CK', 5200.0, 'U/L', 'HIGH'),
            ('LDH', 900.0, 'U/L', 'HIGH'),
            ('GGT', 300.0, 'U/L', 'HIGH'),
        ],
    ),
    'c5|electrolytes short with unit': (
        'critical',
        [
            ('K', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Na', 118.0, 'mmol/L', 'CRITICAL_LOW'),
        ],
    ),
    'c5|ph text': (
        'critical',
        [
            ('pH', 7.62, 'units', 'CRITICAL_HIGH'),
            ('pCO2', 22.0, 'mmHg', 'LOW'),
        ],
    ),
    'c6|female patient, UI sends no gender (API default male)': (
        'high',
        [
            ('Hemoglobin', 12.4, 'g/dL', 'NORMAL'),
            ('Creatinine', 1.25, 'mg/dL', 'HIGH'),
            ('Testosterone', 250.0, 'ng/dL', 'HIGH'),
            ('SGPT', 28.0, 'U/L', 'HIGH'),
        ],
    ),
    'c6|same with gender=female': (
        'high',
        [
            ('Hemoglobin', 12.4, 'g/dL', 'NORMAL'),
            ('Creatinine', 1.25, 'mg/dL', 'HIGH'),
            ('Testosterone', 250.0, 'ng/dL', 'HIGH'),
            ('SGPT', 28.0, 'U/L', 'HIGH'),
        ],
    ),
    'c6|child 4y Hb (age ignored in ranges?)': (
        'low',
        [
            ('Hemoglobin', 11.5, 'g/dL', 'NORMAL'),
            ('Alkaline Phosphatase', 320.0, 'U/L', 'NORMAL'),
            ('WBC', 13500.0, '/µL', 'NORMAL'),
        ],
    ),
    'c7|thousands separators in text': (
        'low',
        [
            ('Platelet Count', 250000.0, '/cumm', 'NORMAL'),
            ('Total WBC Count', 7200.0, '/cumm', 'NORMAL'),
        ],
    ),
    'c7|sex-specific printed ranges': (
        'high',
        [
            ('Uric Acid', 9.8, 'mg/dL', 'HIGH'),
            ('Ferritin', 8.0, 'ng/mL', 'LOW'),
            ('ESR', 85.0, 'mm/hr', 'HIGH'),
        ],
    ),
    'c7|mixed realistic report (H/L flags, one-sided, qualifiers)': (
        'critical',
        [
            ('Haemoglobin', 13.9, 'g/dL', 'NORMAL'),
            ('Serum Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Serum Sodium', 121.0, 'mmol/L', 'CRITICAL_LOW'),
            ('Troponin I', 50.0, 'ng/mL', 'CRITICAL_HIGH'),
            ('Triglycerides', 880.0, 'mg/dL', 'CRITICAL_HIGH'),
            ('INR', 7.2, '', 'CRITICAL_HIGH'),
            ('Chloride', 101.0, 'mmol/L', 'NORMAL'),
        ],
    ),
    'csv1|flag column + status': (
        'critical',
        [
            ('Hemoglobin', 9.2, 'g/dL', 'LOW'),
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
            ('Troponin I', 50.0, 'ng/mL', 'CRITICAL_HIGH'),
            ('HbA1c', 9.4, '%', 'HIGH'),
        ],
    ),
    'csv1|value with H glued and qualitative': (
        'critical',
        [
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
            ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
            ('HIV 1&2', 'Reactive', '', 'POSITIVE'),
            ('Glucose', 1250.0, 'mg/dL', 'CRITICAL_HIGH'),
        ],
    ),
    'csv1|headers Observed Value / Biological Reference Interval': (
        'critical',
        [
            ('Haemoglobin', 6.1, 'gm/dL', 'CRITICAL_LOW'),
            ('Platelet Count', 12000.0, '/µL', 'CRITICAL_LOW'),
            ('Total WBC Count', 25400.0, 'cells/cumm', 'HIGH'),
            ('Serum Creatinine', 9.8, 'mg/dL', 'HIGH'),
        ],
    ),
    'csv1|no header, extra leading id column': (
        'critical',
        [
            ('Hemoglobin', 9.2, 'g/dL', 'LOW'),
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
            ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
        ],
    ),
    'csv1|patient/date columns per row (long format)': (
        'critical',
        [
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
            ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
            ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
        ],
    ),
    'csv1|wide format (one row per patient)': (
        'critical',
        [
            ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
            ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
            ('Glucose', 480.0, 'mg/dL', 'CRITICAL_HIGH'),
        ],
    ),
    'csv1|two results columns previous/current': (
        'critical',
        [
            ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
        ],
    ),
    'csv1|semicolon decimal comma thousands dot': (
        'critical',
        [
            ('Kalium', 6.8, 'mmol/l', 'CRITICAL_HIGH'),
            ('Thrombozyten', 12000.0, '/µl', 'CRITICAL_LOW'),
            ('Hämoglobin', 6.1, 'g/dl', 'CRITICAL_LOW'),
        ],
    ),
    'csv1|result unit in one cell and range with unit': (
        'critical',
        [
            ('Potassium', 6.8, 'mmol/L', 'CRITICAL_HIGH'),
            ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
            ('WBC', 25400.0, '/µL', 'HIGH'),
            ('Platelets', 12000.0, '/µL', 'CRITICAL_LOW'),
        ],
    ),
    'csv1|negative and sci': (
        'critical',
        [
            ('Base Excess', -12.5, 'mmol/L', 'LOW'),
            ('WBC', 25400.0, '/uL', 'HIGH'),
            ('pH', 7.05, '', 'CRITICAL_LOW'),
        ],
    ),
    'csv2|short names no unit': (
        'critical',
        [
            ('K', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Na', 118.0, 'mmol/L', 'CRITICAL_LOW'),
            ('pH', 7.62, '', 'CRITICAL_HIGH'),
            ('Ca', 5.9, 'mg/dL', 'CRITICAL_LOW'),
            ('INR', 6.5, '', 'CRITICAL_HIGH'),
        ],
    ),
    'csv2|HbA1c long name no unit': (
        'high',
        [
            ('Hemoglobin A1c', 9.4, '%', 'HIGH'),
            ('Direct Bilirubin', 1.0, 'mg/dL', 'HIGH'),
        ],
    ),
    'csv2|header with Low/High columns and unit hint': (
        'critical',
        [
            ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
            ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
        ],
    ),
    # ---- round-4/5 reviewer cases (tests/fixtures/lab_cases/review4_a*.txt); the reviewer's harness used age 40 ----
    'review4_a1|A1 two tests on one line (crit in 2nd col)': ('critical', [
        ('Sodium', 138.0, 'mmol/L', 'NORMAL'),
        ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
        ('Platelet Count', 12000.0, '/cumm', 'CRITICAL_LOW'),
    ]),
    'review4_a1|A1b two tests one line no ranges': ('critical', [
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
        ('WBC', 900.0, '/uL', 'CRITICAL_LOW'),
    ]),
    'review4_a1|A2 section headers with numbers': ('low', [
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
        ('WBC', 7200.0, '/uL', 'NORMAL'),
        ('Total Cholesterol', 180.0, 'mg/dL', 'NORMAL'),
    ]),
    'review4_a1|A3 method/sample notes': ('low', [
        ('Creatinine', 1.0, 'mg/dL', 'NORMAL'),
        ('Glucose Fasting (Hexokinase)', 92.0, 'mg/dL', 'NORMAL'),
    ]),
    'review4_a1|A3b method in name w/ critical': ('critical', [
        ('Creatinine (Method: Jaffe Kinetic)', 12.5, 'mg/dL', 'CRITICAL_HIGH'),
        ('Hemoglobin (Hb)', 6.1, 'g/dL', 'CRITICAL_LOW'),
        ('Glucose - Fasting (GOD-POD)', 38.0, 'mg/dL', 'CRITICAL_LOW'),
        ('Potassium (Method: ISE Direct)', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
    ]),
    'review4_a1|A4 footnotes': ('low', [
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
    ]),
    'review4_a1|A5 HbA1c interpretive table': ('low', [
        ('HbA1c', 5.2, '%', 'NORMAL'),
        ('Estimated Average Glucose', 103.0, 'mg/dL', 'NORMAL'),
    ]),
    'review4_a1|A5b interpretive list on the row: Vit D deficiency': ('high', [
        ('Vitamin D', 12.0, 'ng/mL', 'LOW'),
    ]),
    'review4_a1|A5c HDL low with interpretive list': ('moderate', [
        ('HDL Cholesterol', 25.0, 'mg/dL', 'LOW'),
    ]),   # WARN: HDL Cholesterol: the printed ranges are interpretation categories (e.g. deficient / borderline), not a reference range
    'review4_a1|A5d interpretive lines after row': ('high', [
        ('25-OH Vitamin D', 12.0, 'ng/mL', 'LOW'),
    ]),
    'review4_a1|A5e cholesterol categories': ('high', [
        ('Total Cholesterol', 260.0, 'mg/dL', 'HIGH'),
        ('LDL Cholesterol', 190.0, 'mg/dL', 'HIGH'),
        ('Triglycerides', 180.0, 'mg/dL', 'HIGH'),
    ]),
    'review4_a1|A5f eGFR stages': ('high', [
        ('eGFR', 12.0, 'mL/min/1.73m2', 'LOW'),
    ]),
    'review4_a1|A5g Vit B12 / ferritin with category words first': ('high', [
        ('Vitamin B12', 90.0, 'pg/mL', 'LOW'),
        ('TSH', 25.0, 'uIU/mL', 'HIGH'),
    ]),
    'review4_a2|A6 child 5y, no printed ranges': ('moderate', [
        ('Creatinine', 1.0, 'mg/dL', 'UNKNOWN'),
        ('Hemoglobin', 10.2, 'g/dL', 'LOW'),
        ('WBC', 14000.0, '/uL', 'UNKNOWN'),
        ('Alkaline Phosphatase', 320.0, 'U/L', 'NORMAL'),
    ]),   # WARN: Creatinine: no reference range (adult reference range only; none for age 5); WBC: no reference range (adult reference range only; none for age 5)
    'review4_a2|A6b newborn': ('critical', [
        ('Hemoglobin', 12.0, 'g/dL', 'UNKNOWN'),
        ('Total Bilirubin', 12.0, 'mg/dL', 'CRITICAL_HIGH'),
        ('Glucose', 45.0, 'mg/dL', 'CRITICAL_LOW'),
    ]),   # WARN: Hemoglobin: no reference range (no reference range for age 0); Total Bilirubin: no reference range (adult reference range only; none for age 0); Glucose: no reference range (adult reference range only
    'review4_a2|A6c infant 1y': ('unknown', [
        ('Hemoglobin', 9.0, 'g/dL', 'UNKNOWN'),
        ('Creatinine', 0.9, 'mg/dL', 'UNKNOWN'),
        ('Lymphocytes', 62.0, '%', 'UNKNOWN'),
    ]),   # WARN: Hemoglobin: no reference range (no reference range for age 1); Creatinine: no reference range (adult reference range only; none for age 1); Lymphocytes: no reference range (adult reference range only;
    'review4_a2|A8 units zoo (crit)': ('critical', [
        ('RBC', 2.1, 'mill/cumm', 'LOW'),
        ('MCV', 62.0, 'fl', 'LOW'),
        ('MCH', 19.0, 'pg', 'LOW'),
        ('Haemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
        ('Platelets', 12000.0, '/µL', 'CRITICAL_LOW'),
        ('WBC', 800.0, '/µL', 'CRITICAL_LOW'),
    ]),
    'review4_a2|A8b units zoo 2': ('critical', [
        ('Platelet Count', 12000.0, '/µL', 'CRITICAL_LOW'),
        ('Total Leucocyte Count', 800.0, 'cells/cumm', 'CRITICAL_LOW'),
        ('Absolute Neutrophil Count', 300.0, '/µL', 'CRITICAL_LOW'),
    ]),
    'review4_a2|A8c labcorp units': ('critical', [
        ('WBC', 25400.0, '/µL', 'HIGH'),
        ('Platelets', 12000.0, '/µL', 'CRITICAL_LOW'),
        ('RBC', 2.1, 'x10^6/uL', 'LOW'),
        ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
    ]),
    'review4_a2|A8d K/uL and 10*9': ('critical', [
        ('Platelets', 12000.0, '/µL', 'CRITICAL_LOW'),
        ('White cell count', 25400.0, '/µL', 'HIGH'),
    ]),
    'review4_a2|A8e lakh variants': ('critical', [
        ('Platelet Count', 12000.0, '/µL', 'CRITICAL_LOW'),
    ]),
    'review4_a2|A8f IU/mL etc unmatched w/ range': ('high', [
        ('Anti-TPO', 250.0, 'IU/mL', 'HIGH'),
        ('CA-125', 95.0, 'U/mL', 'HIGH'),
        ('RA Factor', 64.0, 'IU/mL', 'HIGH'),
        ('IgE', 450.0, 'IU/mL', 'HIGH'),
    ]),
    'review4_a2|A8g unit mismatch': ('critical', [
        ('Hemoglobin', 6.1218, 'g/dL', 'CRITICAL_LOW'),
        ('Calcium', 14.4288, 'mg/dL', 'CRITICAL_HIGH'),
        ('Creatinine', 5.08935, 'mg/dL', 'HIGH'),
        ('T3 Total', 40.0, 'ng/dL', 'LOW'),
        ('Vitamin B12', 90.0, 'ng/L', 'LOW'),
        ('MCHC', 28.0, '%', 'LOW'),
        ('Haematocrit', 19.0, '%', 'LOW'),
    ]),
    'review4_a2|A8h SI crit no range': ('critical', [
        ('Creatinine', 12.4406, 'mg/dL', 'CRITICAL_HIGH'),
        ('Bilirubin', 14.6199, 'mg/dL', 'CRITICAL_HIGH'),
        ('Glucose', 37.8336, 'mg/dL', 'CRITICAL_LOW'),
        ('Calcium', 4.8096, 'mg/dL', 'CRITICAL_LOW'),
        ('Troponin I', 0.85, 'ng/mL', 'CRITICAL_HIGH'),
        ('Troponin T', 0.5, 'µg/L', 'CRITICAL_HIGH'),
    ]),
    'review4_a2|A8i NHS style': ('critical', [
        ('Haemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
        ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
        ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
        ('Creatinine', 5.08935, 'mg/dL', 'HIGH'),
        ('Urea', 210.21, 'mg/dL', 'HIGH'),
        ('CRP', 250.0, 'mg/L', 'HIGH'),
        ('HbA1c', 9.01248, '%', 'HIGH'),
        ('Bilirubin', 14.6199, 'mg/dL', 'CRITICAL_HIGH'),
        ('ALT', 400.0, 'U/L', 'HIGH'),
        ('Albumin', 2.5, 'g/dL', 'LOW'),
        ('Adjusted calcium', 14.4288, 'mg/dL', 'CRITICAL_HIGH'),
        ('Neutrophils', 300.0, '/µL', 'CRITICAL_LOW'),
        ('TSH', 25.0, 'mU/L', 'HIGH'),
        ('Free T4', 0.3108, 'ng/dL', 'LOW'),
        ('Troponin T', 0.5, 'ng/mL', 'CRITICAL_HIGH'),
    ]),
    'review4_a2|A8j Quest style': ('critical', [
        ('CHOLESTEROL, TOTAL', 260.0, 'mg/dL', 'HIGH'),
        ('HDL CHOLESTEROL', 25.0, 'mg/dL', 'LOW'),
        ('GLUCOSE', 320.0, 'mg/dL', 'CRITICAL_HIGH'),
        ('EGFR', 12.0, 'mL/min/1.73m2', 'LOW'),
        ('TSH', 25.0, 'mIU/L', 'HIGH'),
        ('POTASSIUM', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
    ]),
    'review4_a2|A8k Quest style no flags': ('high', [
        ('HDL CHOLESTEROL', 25.0, 'mg/dL', 'LOW'),
        ('EGFR', 12.0, 'mL/min/1.73m2', 'LOW'),
    ]),
    'review4_a3|B1 single line two tests only': ('critical', [
        ('Sodium', 138.0, 'mmol/L', 'NORMAL'),
        ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
    ]),
    'review4_a3|B1b two tests one line with separators': ('critical', [
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
        ('Platelet Count', 12000.0, '/cumm', 'CRITICAL_LOW'),
    ]),
    'review4_a3|B2 cumulative prev first (text)': ('critical', [
        ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
        ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
    ]),
    'review4_a3|B3 x 10^3 with space': ('critical', [
        ('WBC', 7200.0, '/µL', 'NORMAL'),
        ('Platelet Count', 12000.0, '/µL', 'CRITICAL_LOW'),
    ]),
    'review4_a3|B4 digits in names': ('moderate', [
        ('Vitamin D 25 Hydroxy', 150.0, 'ng/mL', 'HIGH'),
        ('VITAMIN D, 25 - HYDROXY, SERUM', 120.192, 'ng/mL', 'HIGH'),
        ('Glucose 2 hr post prandial', 135.0, 'mg/dL', 'NORMAL'),
        ('Vitamin B 12', 500.0, 'pg/mL', 'NORMAL'),
    ]),   # WARN: VITAMIN D, 25 - HYDROXY, SERUM: reported more than once with different values
    'review4_a3|B5 serology': ('high', [
        ('HIV 1 & 2 Antibodies', 'Reactive', '', 'POSITIVE'),
        ('HIV I & II', 'Reactive', '', 'POSITIVE'),
        ('Hepatitis B Surface Antigen (HBsAg)', 'Reactive', '', 'POSITIVE'),
        ('Anti HCV', 'Non Reactive', '', 'NEGATIVE'),
        ('Dengue NS1 Antigen', 'Positive', '', 'POSITIVE'),
        ('VDRL', 'Non-Reactive', '', 'NEGATIVE'),
    ]),
    'review4_a3|B5b blood group': ('low', [
        ('Blood Group B', 'Positive', '', 'INFO'),
        ('Rh Factor', 'Positive', '', 'INFO'),
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
    ]),
    'review4_a3|B5c serology status wording': ('high', [
        ('HIV Status', 'Reactive', '', 'POSITIVE'),
        ('Malaria Parasite', 'Detected', '', 'POSITIVE'),
        ('Widal Test', 'Negative', '', 'NEGATIVE'),
        ('HBsAg', 'POSITIVE', '', 'POSITIVE'),
        ('Urine Protein', '++', '', 'POSITIVE'),
        ('Urine Glucose', 'Nil', '', 'NEGATIVE'),
    ]),
    'review4_a3|B6 parenthetical qualifiers critical': ('critical', [
        ('Calcium (Total)', 5.9, 'mg/dL', 'CRITICAL_LOW'),
        ('Glucose (Fasting)', 38.0, 'mg/dL', 'CRITICAL_LOW'),
        ('Bilirubin (Total)', 12.5, 'mg/dL', 'CRITICAL_HIGH'),
        ('Potassium (K+)', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
        ('Sodium (Na+)', 118.0, 'mmol/L', 'CRITICAL_LOW'),
        ('Platelet Count (PLT)', 12000.0, '/cumm', 'CRITICAL_LOW'),
    ]),
    'review4_a3|B6b dash qualifiers critical': ('critical', [
        ('Bilirubin - Total', 12.5, 'mg/dL', 'CRITICAL_HIGH'),
        ('Cholesterol - HDL', 25.0, 'mg/dL', 'LOW'),
        ('Glucose - Random', 520.0, 'mg/dL', 'CRITICAL_HIGH'),
        ('Calcium - Total', 5.9, 'mg/dL', 'CRITICAL_LOW'),
        ('Corrected Calcium', 14.4, 'mg/dL', 'CRITICAL_HIGH'),
    ]),   # WARN: Corrected Calcium: reported more than once with different values
    'review4_a3|B7 pH urine alone': ('low', [
        ('pH', 6.0, '', 'NORMAL'),
        ('Specific Gravity', 1.02, '', 'NORMAL'),
    ]),
    'review4_a3|B7b Reaction (pH)': ('low', [
        ('Reaction (pH)', 6.0, '', 'NORMAL'),
    ]),
    'review4_a3|B8 negatives': ('high', [
        ('Glucose', -320.0, 'mg/dL', 'UNKNOWN'),
        ('Base Excess', -12.5, 'mmol/L', 'LOW'),
        ('Anion Gap', 28.0, 'mmol/L', 'HIGH'),
    ]),   # WARN: Glucose: negative value -320 is not physiologically possible; not scored
    'review4_a3|B9 range annotations': ('high', [
        ('Creatinine', 4.2, 'mg/dL', 'HIGH'),
        ('Hemoglobin', 12.0, 'g/dL', 'LOW'),
        ('TSH', 6.5, 'uIU/mL', 'HIGH'),
        ('ALP', 300.0, 'U/L', 'HIGH'),
        ('Hemoglobin A2', 6.5, '%', 'HIGH'),
        ('ESR', 60.0, 'mm/hr', 'HIGH'),
    ]),   # WARN: Hemoglobin A2: none of the printed age-specific ranges covers age 40
    'review4_a3|B10 no-unit bare criticals': ('critical', [
        ('Creatinine', 12.5, 'mg/dL', 'CRITICAL_HIGH'),
        ('Troponin I', 2.5, 'ng/mL', 'CRITICAL_HIGH'),
        ('Glucose', 38.0, 'mg/dL', 'CRITICAL_LOW'),
        ('Triglycerides', 880.0, 'mg/dL', 'CRITICAL_HIGH'),
    ]),   # WARN: Creatinine: unit not printed; assumed mg/dL (could also be umol/l); Troponin I: unit not printed; assumed ng/mL (could also be ng/l); Glucose: unit not printed; assumed mg/dL (could also be mmol/l); T
    'review4_a3|B11 by/patient words': ('critical', [
        ('eGFR', 12.0, 'mL/min/1.73m²', 'LOW'),
        ('Patient INR', 6.5, '', 'CRITICAL_HIGH'),
        ('PT - Patient', 45.0, 'Sec', 'HIGH'),
    ]),
    'review4_a3|B12 PCT plateletcrit': ('unknown', [
        ('PCT', 0.25, '', 'UNKNOWN'),
        ('PCT', 0.25, '%', 'NORMAL'),
    ]),   # WARN: PCT: ambiguous abbreviation (procalcitonin or plateletcrit) with no unit or blood-count context; not scored
    'review4_a3|B13 hs troponin T normal': ('low', [
        ('hs Troponin T', 12.0, 'ng/L', 'NORMAL'),
    ]),
    'review4_a3|B14 range then unit, flag words': ('critical', [
        ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
        ('Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
        ('Sodium', 118.0, 'mmol/L', 'CRITICAL_LOW'),
    ]),
    'review4_a3|B15 value after range': ('unknown', [
    ]),   # WARN: 1 numeric line(s) could not be read
    'review4_a4|C1 by-method serology': ('high', [
        ('HBsAg', 'Reactive', '', 'POSITIVE'),
        ('Anti HCV', 'Reactive', '', 'POSITIVE'),
        ('Dengue NS1 Antigen', 'Positive', '', 'POSITIVE'),
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
    ]),
    'review4_a4|C1b HIV numbered alone': ('high', [
        ('HIV 1 & 2 Antibodies', 'Reactive', '', 'POSITIVE'),
    ]),
    'review4_a4|C2 Lal names': ('critical', [
        ('GLUCOSE, POST PRANDIAL (PP), 2 HOURS, PLASMA', 135.0, 'mg/dL', 'NORMAL'),
        ('VITAMIN B12 CYANOCOBALAMIN, SERUM', 90.0, 'pg/mL', 'LOW'),
        ('GLUCOSE, FASTING (F), PLASMA', 320.0, 'mg/dL', 'CRITICAL_HIGH'),
        ('HbA1c (GLYCOSYLATED HEMOGLOBIN), BLOOD', 9.4, '%', 'HIGH'),
        ('THYROID STIMULATING HORMONE (TSH), ULTRASENSITIVE', 25.0, 'µIU/mL', 'HIGH'),
        ('TOTAL IRON BINDING CAPACITY (TIBC)', 520.0, 'µg/dL', 'HIGH'),
    ]),
    'review4_a4|C3 mixed blood + urine routine': ('critical', [
        ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
        ('Platelet Count', 12000.0, '/cumm', 'CRITICAL_LOW'),
        ('Serum Potassium', 6.9, 'mmol/L', 'CRITICAL_HIGH'),
        ('Specific Gravity', 1.02, '', 'NORMAL'),
        ('pH', 6.0, '', 'NORMAL'),
        ('Urine Protein', 'Nil', '', 'NEGATIVE'),
        ('Urine Glucose', 'Nil', '', 'NEGATIVE'),
        ('Pus Cells', 3.0, '/hpf', 'NORMAL'),
        ('Epithelial Cells', 2.0, '/hpf', 'NORMAL'),
        ('Leukocyte Esterase', 'Negative', '', 'NEGATIVE'),
        ('Nitrite', 'Negative', '', 'NEGATIVE'),
    ]),
    'review4_a4|C4 page / header lines only + normal rows': ('low', [
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
        ('WBC', 7200.0, '/uL', 'NORMAL'),
    ]),
    'review4_a4|C4b header': ('low', [
        ('Total Cholesterol', 180.0, 'mg/dL', 'NORMAL'),
    ]),
    'review4_a4|C4c header': ('low', [
        ('Creatinine', 1.0, 'mg/dL', 'NORMAL'),
    ]),
    'review4_a4|C5 cell per line interpretive': ('high', [
        ('25-OH Vitamin D', 12.0, 'ng/mL', 'LOW'),
        ('eGFR', 12.0, 'mL/min/1.73m2', 'LOW'),
    ]),   # WARN: eGFR: the printed ranges are interpretation categories (e.g. deficient / borderline), not a reference range
    'review4_a4|C6 normal full': ('low', [
        ('Hemoglobin', 14.5, 'g/dL', 'NORMAL'),
        ('WBC', 7200.0, '/uL', 'NORMAL'),
        ('Platelet Count', 250000.0, '/uL', 'NORMAL'),
        ('Sodium', 140.0, 'mmol/L', 'NORMAL'),
        ('Potassium', 4.2, 'mmol/L', 'NORMAL'),
        ('Creatinine', 1.0, 'mg/dL', 'NORMAL'),
    ]),
    'review4_a4|C7 mild only': ('moderate', [
        ('Hemoglobin', 12.8, 'g/dL', 'LOW'),
        ('Sodium', 140.0, 'mmol/L', 'NORMAL'),
    ]),
    'review4_a4|C8 no sex/age no ranges': ('unknown', [
        ('Hemoglobin', 12.4, 'g/dL', 'UNKNOWN'),
        ('Creatinine', 1.25, 'mg/dL', 'UNKNOWN'),
        ('Sodium', 140.0, 'mmol/L', 'NORMAL'),
    ]),   # WARN: Hemoglobin: no reference range (age not provided); Creatinine: no reference range (sex not provided)
    'review4_a4|C8b sex, no age, no ranges': ('unknown', [
        ('Hemoglobin', 9.4, 'g/dL', 'UNKNOWN'),
        ('Sodium', 140.0, 'mmol/L', 'NORMAL'),
    ]),   # WARN: Hemoglobin: no reference range (age not provided)
    'review4_a4|C8c no sex/age: normal electrolytes only': ('low', [
        ('Sodium', 140.0, 'mmol/L', 'NORMAL'),
        ('Potassium', 4.2, 'mmol/L', 'NORMAL'),
    ]),
    'review4_a4|C9 unreadable garbage': ('unknown', [
    ]),
    'review4_a4|C9b numeric garbage': ('unknown', [
    ]),   # WARN: 2 numeric line(s) could not be read
    'review4_a4|C10 unknown analyte abnormal by printed flag only': ('high', [
        ('Lactate', 8.5, 'mmol/L', 'HIGH'),
        ('Ammonia', 220.0, 'umol/L', 'UNKNOWN'),
        ('Digoxin', 4.2, 'ng/mL', 'HIGH'),
    ]),   # WARN: Lactate: no reference range (not in reference table); Ammonia: no reference range (not in reference table)
    'review4_a4|C11 qualified criticals': ('critical', [
        ('Glucose', 20.0, 'mg/dL', 'CRITICAL_LOW'),
        ('Potassium', 8.0, 'mmol/L', 'CRITICAL_HIGH'),
        ('Platelet Count', 10000.0, '/µL', 'CRITICAL_LOW'),
        ('Hemoglobin', 5.0, 'g/dL', 'CRITICAL_LOW'),
    ]),
    'review4_a4|C12 border values': ('high', [
        ('Potassium', 6.0, 'mmol/L', 'HIGH'),
        ('Hemoglobin', 7.0, 'g/dL', 'LOW'),
        ('Glucose', 50.0, 'mg/dL', 'LOW'),
        ('Sodium', 125.0, 'mmol/L', 'LOW'),
        ('INR', 3.0, '', 'HIGH'),
    ]),
    'review4_a4|C13 OCR-ish spacing & colon': ('critical', [
        ('Hemoglobin', 6.1, 'g/dL', 'CRITICAL_LOW'),
        ('S.Potassium', 6.9, 'mEq/L', 'CRITICAL_HIGH'),
        ('Sr. Creatinine', 12.5, 'mg/dl', 'CRITICAL_HIGH'),
        ('B. Urea', 210.0, 'mg/dl', 'HIGH'),
        ('T. Bilirubin', 12.5, 'mg/dl', 'CRITICAL_HIGH'),
        ('S.G.P.T', 400.0, 'IU/L', 'HIGH'),
        ('S.G.O.T.', 380.0, 'IU/L', 'HIGH'),
        ('TLC', 800.0, '/cmm', 'CRITICAL_LOW'),
    ]),
    'review4_a5|E1 prose sentence': ('low', [
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
    ]),
    'review4_a5|E2 blood group only': ('low', [
        ('Blood Group O', 'Positive', '', 'INFO'),
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
    ]),
    'review4_a5|E3 serology dropped, no numeric line': ('high', [
        ('HBsAg', 'Reactive', '', 'POSITIVE'),
        ('Hemoglobin', 14.1, 'g/dL', 'NORMAL'),
    ]),
    'review4_a5|E4 hsTnT normal alone': ('low', [
        ('hs Troponin T', 12.0, 'ng/L', 'NORMAL'),
    ]),
    'review4_a5|E5 Hb% alone': ('critical', [
        ('Hb%', 6.1, 'gm%', 'CRITICAL_LOW'),
    ]),
}


# ── round-5 targeted unit tests, one block per rule ─────────────────────────────────────────────────────────────


def _flags(text, sex=None, age=None):
    return [(r.display_name, r.flag) for r in _analyse(text, sex=sex, age=age).rows]


def _risk(text, sex=None, age=None):
    return apply_risk_floor(_analyse(text, sex=sex, age=age), "low", 5.0)[0]


# NEW-2: multi-value lines ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("header", [
    "Test   Previous   Current   Unit   Range",
    "Test Prior Latest Unit Range",
    "Test Previous Result Current Result Unit Range",
    "Test 12/01/2026 12/03/2026 Unit Range",
    "Test 12-Jan-2026 12-Mar-2026 Unit Range",
])
def test_cumulative_header_picks_the_current_column(header):
    analysis = _analyse(header + "\nPotassium 4.2 6.9 mmol/L 3.5-5.1")
    (row,) = analysis.rows
    assert row.value == 6.9 and row.flag == "CRITICAL_HIGH"
    assert row.note and "result columns" in row.note


@pytest.mark.parametrize("header", ["Test Current Previous Unit Range", "Test Today Last Unit Range",
                                    "Test 12/03/2026 12/01/2026 Unit Range"])
def test_cumulative_header_current_first(header):
    (row,) = _analyse(header + "\nPotassium 6.9 4.2 mmol/L 3.5-5.1").rows
    assert row.value == 6.9 and row.flag == "CRITICAL_HIGH"


def test_two_numbers_without_a_column_header_are_unknown_with_a_warning_never_low():
    analysis = _analyse("Potassium 4.2 6.9 mmol/L 3.5-5.1")
    (row,) = analysis.rows
    assert row.flag == "UNKNOWN" and row.value is None and analysis.warnings
    assert _risk("Potassium 4.2 6.9 mmol/L 3.5-5.1") != "low"
    assert _flags("Potassium 4.2 6.9 7.1 mmol/L 3.5-5.1") == [("Potassium", "UNKNOWN")]


def test_two_tests_on_one_line_become_two_rows():
    analysis = _analyse("Sodium 138 mmol/L 135-145   Potassium 6.9 mmol/L 3.5-5.1")
    assert [(r.display_name, r.flag) for r in analysis.rows] == [("Sodium", "NORMAL"), ("Potassium", "CRITICAL_HIGH")]
    assert _flags("Potassium 6.9 mmol/L 3.5-5.1 Sodium 138 mmol/L 135-145 Chloride 101 mmol/L 98-107") == [
        ("Potassium", "CRITICAL_HIGH"), ("Sodium", "NORMAL"), ("Chloride", "NORMAL")]


def test_two_tests_on_one_line_without_ranges_or_units_keep_their_criticals():
    got = dict(_flags("Hemoglobin 14.1 g/dL  WBC 900 /uL", sex="M", age=40))
    assert got == {"Hemoglobin": "NORMAL", "WBC": "CRITICAL_LOW"}
    got = dict(_flags("Glucose Fasting 92 Glucose PP 410"))
    assert got["Glucose PP"] == "CRITICAL_HIGH"


def test_two_panels_side_by_side_with_three_space_column_gaps():
    text = ("Test   Result   Unit   Ref   Test   Result   Unit   Ref\n"
            "Sodium   138   mmol/L   135-145   Haemoglobin   6.1   g/dL   13-17")
    assert _flags(text) == [("Sodium", "NORMAL"), ("Haemoglobin", "CRITICAL_LOW")]


def test_history_annotation_is_not_a_second_row_and_does_not_replace_the_result():
    (row,) = _analyse("Potassium 6.9 mmol/L 3.5-5.1 (prev 4.1 on 12/01/2026)").rows
    assert row.value == 6.9 and row.flag == "CRITICAL_HIGH"


# NEW-3: labelled ranges -------------------------------------------------------------------------------------------


def test_category_labels_are_not_reference_ranges():
    (row,) = _analyse("Vitamin D 12 ng/mL Deficiency: <20 Insufficiency: 20-29 Sufficiency: 30-100").rows
    assert row.flag == "LOW"  # 12 is below the sufficient (normal-like) band, never "normal" because it is "<20"-adjacent
    assert _flags("eGFR 12 mL/min/1.73m2 Kidney failure: <15 Severe: 15-29 Normal: >=90") == [("eGFR", "LOW")]


def test_category_only_ranges_fall_back_to_the_registry_range_with_a_warning():
    analysis = _analyse("HDL Cholesterol 25 mg/dL Low: <40 High: >60", sex="M", age=40)
    (row,) = analysis.rows
    assert row.flag == "LOW" and analysis.warnings


def test_category_only_ranges_without_a_registry_range_are_unknown():
    analysis = _analyse("Mystery Marker 25 U/L Low: <40 High: >60")
    assert all(r.flag == "UNKNOWN" for r in analysis.rows)
    assert _risk("Mystery Marker 25 U/L Low: <40 High: >60") != "low"


def test_adult_label_needs_a_matching_age_and_normal_like_label_is_taken():
    assert _flags("Alkaline Phosphatase 90 U/L Adult: 40-129", sex="M", age=40) == [("Alkaline Phosphatase", "NORMAL")]
    assert _flags("Alkaline Phosphatase 90 U/L Adult: 40-129", sex="M", age=None)[0][1] == "UNKNOWN"
    assert _flags("Hemoglobin 12.4 g/dL Men: 13.0-17.0 Women: 12.0-15.5", sex="F", age=40) == [("Hemoglobin", "NORMAL")]
    assert _flags("Hemoglobin 12.4 g/dL Men: 13.0-17.0 Women: 12.0-15.5", sex="M", age=40) == [("Hemoglobin", "LOW")]


def test_age_band_is_a_label_not_a_range():
    analysis = _analyse("Alkaline Phosphatase 320 U/L 0-14 yrs: 130-560 15-18 yrs: 50-200 Adult: 40-129", sex="M", age=9)
    (row,) = analysis.rows
    assert row.flag in ("NORMAL", "UNKNOWN")  # a 9-year-old: the 0-14 band (130-560) applies, never the adult one
    assert row.flag == "NORMAL"


def test_interpretation_lines_are_not_unread_rows():
    analysis = _analyse("Deficiency: <20\nInsufficiency: 20-29\nPotassium 4.0 mmol/L 3.5-5.1")
    assert [r.display_name for r in analysis.rows] == ["Potassium"] and not analysis.unparsed_numeric_lines
    assert _risk("Deficiency: <20\nInsufficiency: 20-29\nPotassium 4.0 mmol/L 3.5-5.1") == "low"


# NEW-4: demographic words must not drop real rows ------------------------------------------------------------------


@pytest.mark.parametrize("line,name", [
    ("Creatinine by Jaffe method 1.0 mg/dL 0.7-1.3", "Creatinine"),
    ("Hemoglobin Method: SLS 14.1 g/dL 13.0-17.0", "Hemoglobin"),
    ("Sample Glucose 92 mg/dL 70-100", "Glucose"),
    ("Glucose Fasting Status 92 mg/dL 70-100", "Glucose"),
    ("Hemoglobin Patient Value 14.1 g/dL 13.0-17.0", "Hemoglobin"),
])
def test_method_and_demographic_words_do_not_remove_real_rows(line, name):
    analysis = _analyse(line)
    assert len(analysis.rows) == 1 and name.lower() in analysis.rows[0].display_name.lower()
    assert analysis.rows[0].flag == "NORMAL"


def test_serology_by_method_keeps_the_qualitative_result():
    rows = _analyse("HBsAg by ELISA Reactive\nAnti HCV by CMIA Reactive").rows
    assert [(r.display_name, r.flag) for r in rows] == [("HBsAg", "POSITIVE"), ("Anti HCV", "POSITIVE")]


def test_pure_method_or_sample_lines_are_not_rows_and_not_unread():
    analysis = _analyse("Method: Photometric\nSample: EDTA\nPotassium 4.0 mmol/L 3.5-5.1")
    assert [r.display_name for r in analysis.rows] == ["Potassium"] and not analysis.unparsed_numeric_lines


def test_a_result_like_line_dropped_as_demographic_is_counted_unread():
    analysis = _analyse("Patient Status Hemolysed 12.5 g/dL\nPotassium 4.0 mmol/L 3.5-5.1")
    names = [r.display_name for r in analysis.rows]
    assert "Potassium" in names
    # either the line is read as a real row, or it is reported - never silently lost
    assert any("hemo" in n.lower() or "status" in n.lower() for n in names) or analysis.unparsed_numeric_lines


# NEW-5: digits inside names ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("line,value,name_part", [
    ("2 HOURS PP Glucose 140 mg/dL 70-140", 140, "glucose"),
    ("25 - HYDROXY Vitamin D 12 ng/mL 30-100", 12, "vitamin d"),
    ("Vitamin B 12 250 pg/mL 200-900", 250, "b 12"),
    ("Glucose 2 Hours Post Meal 150 mg/dL 70-140", 150, "glucose"),
    ("Apolipoprotein A1 140 mg/dL 110-205", 140, "apolipoprotein"),
    ("Lipoprotein (a) 25 mg/dL <30", 25, "lipoprotein"),
])
def test_digits_inside_names_are_not_the_result(line, value, name_part):
    (row,) = _analyse(line).rows
    assert row.value == pytest.approx(value, rel=0.01) and name_part in row.display_name.lower()


def test_several_numeric_candidates_are_unknown_with_a_warning():
    analysis = _analyse("Glucose 5 6 mg/dL")
    (row,) = analysis.rows
    assert row.flag == "UNKNOWN" and analysis.warnings


# NEW-6: normalize_line replacement bug ---------------------------------------------------------------------------


@pytest.mark.parametrize("unit", [
    "x 10^3/uL", "x10^3/uL", "X 10^3/µL", "x 10³/uL", "x 10*3/uL", "X 10^3 / uL", "10^3/uL", "x10^3/μL",
    "x 10^3 cells/uL", "x10E3/uL", "thou/uL", "K/uL", "x 10^3/mm3", "x10^3/cumm", "×10^3/µL",
])
def test_every_thousand_per_microlitre_spelling_is_converted(unit):
    (row,) = _analyse(f"WBC 7.2 {unit} 4-11").rows
    assert row.value == pytest.approx(7200, rel=0.001) and row.flag == "NORMAL", unit
    assert unit_key(row.unit) == unit_key("/uL")


@pytest.mark.parametrize("unit,expect", [
    ("x 10^9/L", 250000), ("x10^9/L", 250000), ("10^9/L", 250000), ("x 10⁹/L", 250000), ("x 10*9/L", 250000),
    ("x10E9/L", 250000), ("x 10^9 / L", 250000),
])
def test_every_billion_per_litre_spelling_is_converted(unit, expect):
    (row,) = _analyse(f"Platelets 250 {unit} 150-410").rows
    assert row.value == pytest.approx(expect, rel=0.001) and row.flag == "NORMAL", unit


def test_normalize_line_never_turns_a_group_reference_plus_digit_into_an_octal_escape():
    from app.services.lab_engine.tokenizer import normalize_line

    for text in ("WBC 7.2 x 10^3/uL", "WBC 7.2 x10^3/uL", "WBC 7.2 X 10^6/uL", "RBC 4.5 x 10^12/L", "x 10^3"):
        assert "\x08" not in normalize_line(text) and "H^" not in normalize_line(text), text
    assert "10^3" in normalize_line("WBC 7.2 x 10^3/uL")


def test_no_re_sub_replacement_in_the_engine_uses_a_group_reference_followed_by_a_digit():
    """Audit of every replacement string: r'\\1' + digit is an octal escape (\\10 -> chr(8)); use \\g<1>."""
    import re

    engine = Path(__file__).resolve().parent.parent / "app" / "services" / "lab_engine"
    offenders = []
    for path in engine.glob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(r"""\.sub\(.*,\s*r?["'][^"']*\\[1-9][0-9]""", line):
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, offenders


# NEW-7: false criticals / highs ----------------------------------------------------------------------------------


def test_bare_ph_is_not_blood_ph_without_a_blood_gas_context():
    analysis = _analyse("pH 6.9")
    (row,) = analysis.rows
    assert row.flag == "UNKNOWN" and analysis.warnings
    assert _flags("pH 5.5 4.6-8.0") == [("pH", "NORMAL")]
    assert _flags("pCO2 40 mmHg 35-45\npO2 90 mmHg 80-100\npH 7.40 7.35-7.45") == [
        ("pCO2", "NORMAL"), ("pO2", "NORMAL"), ("pH", "NORMAL")]
    assert dict(_flags("pCO2 40 mmHg 35-45\npH 7.10 7.35-7.45"))["pH"] in ("LOW", "CRITICAL_LOW")


def test_hs_troponin_t_in_ng_per_l_is_not_critical_at_a_small_rise():
    for text in ("Troponin T hs 20 ng/L <14", "Troponin T hs 20 ng/L"):
        assert _flags(text) == [("Troponin T hs", "HIGH")], text


def test_critical_does_not_fire_on_an_assay_cutoff_inside_the_printed_range():
    # troponin: 0.04 is the assay's own decision limit; a laboratory that prints 0-0.5 for its assay is not "critical" at 0.3
    for text in ("Troponin I 0.3 ng/mL 0-0.5", "Troponin I 0.3 ng/mL <0.5"):
        analysis = _analyse(text)
        (row,) = analysis.rows
        assert row.flag == "NORMAL" and analysis.warnings, text
    # without any printed range the registry cut-off still applies
    assert _flags("Troponin I 0.3 ng/mL") == [("Troponin I", "CRITICAL_HIGH")]
    # a genuine panic value (K 6.9) stays critical against a generous printed range, and the clash is disclosed
    analysis = _analyse("Potassium 6.9 mmol/L 3.5-8.0")
    assert [r.flag for r in analysis.rows] == ["CRITICAL_HIGH"] and analysis.warnings
    assert _flags("Potassium 9.5 mmol/L 3.5-12.0")[0][1].startswith("CRITICAL")


@pytest.mark.parametrize("text", ["Blood Group O Positive", "Blood Group: AB Positive", "Rh Factor Positive", "Rh Type: Negative",
                                  "ABO Group B Positive"])
def test_blood_group_and_rh_are_informational_never_abnormal(text):
    (row,) = _analyse(text).rows
    assert row.flag == "INFO"
    assert _risk(text) == "low"


@pytest.mark.parametrize("text", ["HBsAg Positive", "HIV Reactive", "Anti HCV Positive", "Dengue NS1 Antigen Positive"])
def test_positive_serology_is_still_abnormal(text):
    (row,) = _analyse(text).rows
    assert row.flag == "POSITIVE" and _risk(text) in ("high", "critical", "moderate")


def test_bare_pct_is_not_guessed():
    assert _flags("PCT 0.3") == [("PCT", "UNKNOWN")]
    assert _risk("PCT 0.3") != "low"
    assert _flags("PCT 0.3 ng/mL")[0][1] == "HIGH"
    assert _flags("Procalcitonin 0.3 ng/mL <0.5") == [("Procalcitonin", "NORMAL")]
    cbc = _flags("Platelet Count 250000 /cumm 150000-450000\nPCT 0.25 %")
    assert cbc == [("Platelet Count", "NORMAL"), ("PCT", "NORMAL")]


@pytest.mark.parametrize("text", ["Hemoglobin -5 g/dL 13-17", "Potassium -4.0 mmol/L", "Glucose -90 mg/dL"])
def test_negative_concentrations_are_rejected_with_a_warning(text):
    analysis = _analyse(text)
    (row,) = analysis.rows
    assert row.flag == "UNKNOWN" and analysis.warnings
    assert _risk(text) != "low"


# NEW-8: under-calls ---------------------------------------------------------------------------------------------


def test_no_adult_registry_range_for_a_child():
    analysis = _analyse("Creatinine 1.0 mg/dL", sex="M", age=5)
    (row,) = analysis.rows
    assert row.flag == "UNKNOWN" and analysis.warnings
    assert _risk("Creatinine 1.0 mg/dL", sex="M", age=5) != "low"
    # the printed range wins
    assert _flags("Creatinine 1.0 mg/dL 0.3-0.7", sex="M", age=5) == [("Creatinine", "HIGH")]


def test_age_rules_let_children_be_scored_where_they_exist():
    assert _flags("Hemoglobin 10.2 g/dL", sex="M", age=5) == [("Hemoglobin", "LOW")]
    assert _flags("Alkaline Phosphatase 320 U/L", sex="M", age=5) == [("Alkaline Phosphatase", "NORMAL")]


@pytest.mark.parametrize("name", ["Serum Creat", "Creatinine (Serum)", "S. Creatinine", "Creatinine, Serum", "SERUM CREATININE"])
def test_name_variants_still_get_the_registry_criticals(name):
    assert _flags(f"{name} 12.0 mg/dL", sex="M", age=40)[0][1] == "CRITICAL_HIGH", name


def test_unknown_unit_with_a_printed_range_is_scored_on_the_printed_range_but_not_critical():
    analysis = _analyse("Ferritin 40 pmol/L 30-400")
    (row,) = analysis.rows
    assert row.flag != "CRITICAL_HIGH" and row.flag != "CRITICAL_LOW"


def test_bare_value_far_above_the_upper_limit_is_evaluated_for_criticals_with_an_assumed_unit_warning():
    for text, key in [("Glucose 1200", "Glucose"), ("Potassium 69", "Potassium"), ("Hemoglobin 6.1", "Hemoglobin")]:
        analysis = _analyse(text, sex="M", age=40)
        (row,) = analysis.rows
        assert row.flag.startswith("CRITICAL"), text
        assert any("assumed" in w for w in analysis.warnings), text
        assert _risk(text, sex="M", age=40) == "critical"


def test_bare_value_that_fits_two_units_is_unknown_not_guessed():
    analysis = _analyse("Hemoglobin 61", sex="M", age=40)
    assert [r.flag for r in analysis.rows] == ["UNKNOWN"] and analysis.warnings


# NEW-10: stray numeric lines -------------------------------------------------------------------------------------


@pytest.mark.parametrize("stray", [
    "LIPID PROFILE (8 parameters)", "Page 1 of 2", "Complete Blood Count - 2 tests", "Test 3 of 5", "Panel 2 results",
    "Kidney Function Test 2", "Page 2/3", "Report No. 4471", "Printed on 12/03/2026 14:40", "Ref. No: 2026/0045",
])
def test_stray_numeric_lines_are_neither_rows_nor_unread(stray):
    analysis = _analyse(f"{stray}\nPotassium 4.0 mmol/L 3.5-5.1\nSodium 140 mmol/L 135-145")
    assert [r.display_name for r in analysis.rows] == ["Potassium", "Sodium"], stray
    assert not analysis.unparsed_numeric_lines, stray
    assert _risk(f"{stray}\nPotassium 4.0 mmol/L 3.5-5.1") == "low", stray


def test_a_result_like_line_that_cannot_be_read_still_makes_the_report_unknown():
    analysis = _analyse("Potassium 4.0 mmol/L 3.5-5.1\nZorbitol Quotient 55 zz/q")
    assert analysis.unparsed_numeric_lines or any(r.flag == "UNKNOWN" for r in analysis.rows)
    assert _risk("Potassium 4.0 mmol/L 3.5-5.1\nZorbitol Quotient 55 zz/q") != "low"


# urine microscopy remap (urinalysis path) ------------------------------------------------------------------------


def test_microscopy_rows_use_urine_keys_not_the_blood_wbc_rbc():
    rows = _analyse("Pus Cells 2-3 /hpf 0-5\nRBC 0-1 /hpf 0-2\nEpithelial Cells 1-2 /hpf 0-5").rows
    assert [r.analyte_key for r in rows] == ["urine_pus_cells", "urine_rbc", "urine_epithelial"]
    assert all(r.flag == "NORMAL" for r in rows)
    blood = _analyse("WBC 7200 /uL 4000-11000\nRBC 4.8 million/uL 4.5-5.9").rows
    assert [r.analyte_key for r in blood] == ["wbc", "rbc"]
