"""Specimen inference and the S5 floor: SP-01..SP-07 (plans/P2.md section 6.5, 7.3, 12.3).

Clinical basis: spot-urine sodium of 38 mmol/L is normal physiology, never hyponatraemia (serum Na critical below
about 120); CSF glucose of 28 mg/dL is low but the serum hypoglycaemia critical limit does not describe CSF.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.services.lab_engine import analyze_lab_rows, match_analyte, parse_lab_text
from app.services.lab_engine.models import ParsedRow
from app.services.review.specimen import assign_specimens, infer_specimen, specimen_from_section


def _rows(text):
    rows, _ = parse_lab_text(text)
    return rows


def test_sp01_urine_spot_electrolytes_is_urine():
    assert specimen_from_section("URINE SPOT ELECTROLYTES") == "urine"
    rows = _rows("URINE SPOT ELECTROLYTES\nSodium 38 mmol/L")
    assert assign_specimens(rows) == ["urine"]


def test_sp02_csf_analysis():
    assert specimen_from_section("CSF ANALYSIS") == "csf"
    assert specimen_from_section("Cerebrospinal fluid examination") == "csf"
    assert specimen_from_section("PLEURAL FLUID") == "fluid"
    assert specimen_from_section("STOOL ROUTINE") == "other"


def test_sp03_haematology_is_serum():
    assert specimen_from_section("HAEMATOLOGY") == "serum"
    assert specimen_from_section("kidney function test") == "serum"
    assert specimen_from_section("SPECIAL TESTS") is None


def test_sp04_blood_only_report_without_headings_is_serum():
    rows = _rows("Haemoglobin 14.2 g/dL 13.0 - 17.0\nSerum Potassium 4.2 mmol/L 3.5 - 5.1")
    assert assign_specimens(rows) == ["serum", "serum"]


def test_sp05_mixed_report_row_before_any_heading_is_unknown():
    rows = _rows("Haemoglobin 14.2 g/dL 13.0 - 17.0\nURINE ROUTINE EXAMINATION\nSpecific Gravity 1.020\nProtein Nil")
    got = assign_specimens(rows)
    assert got[0] == "unknown"
    assert got[1:] == ["urine", "urine"]


def test_sp05b_heading_after_blood_heading_keeps_serum():
    rows = _rows("HAEMATOLOGY\nHaemoglobin 14.2 g/dL 13.0 - 17.0\nURINE ROUTINE EXAMINATION\nSpecific Gravity 1.020")
    assert assign_specimens(rows) == ["serum", "urine"]


def test_sp06_urine_analyte_key_is_urine_even_without_heading():
    assert match_analyte("Urine pH").key == "urine_ph"
    row = ParsedRow(name="Urine pH", value=6.0, section="")
    assert infer_specimen(row, mixed=True) == "urine"
    sg = ParsedRow(name="Specific Gravity", value=1.02, section="HAEMATOLOGY")
    assert infer_specimen(sg, mixed=False) == "urine"      # never a blood analyte, whatever the heading says


def _analyse(row, sex="male", age=45):
    return analyze_lab_rows([row], sex, age).rows[0]


def test_sp07_unset_serum_and_unknown_leave_engine_output_unchanged():
    text = ("Haemoglobin 6.1 g/dL 13.0 - 17.0\nSerum Potassium 6.9 mmol/L 3.5 - 5.1\nSerum Sodium 112 mmol/L 135 - 145\n"
            "Platelet Count 12000 /cumm 150000 - 450000\nGlucose Fasting 92 mg/dL 70 - 100\npH 7.40 7.35 - 7.45")
    base = analyze_lab_rows(_rows(text), "male", 45)
    for spec in ("serum", "unknown"):
        rows = _rows(text)
        for r in rows:
            r.specimen = spec
        other = analyze_lab_rows(rows, "male", 45)
        strip = lambda a: [dataclasses.replace(x, parsed=None) for x in a.rows]  # noqa: E731
        assert strip(other) == strip(base)
        assert other.critical_alerts == base.critical_alerts
    assert any(r.flag.startswith("CRITICAL") for r in base.rows)    # the control actually has criticals


def test_floor_urine_sodium_38_is_not_hyponatraemia():
    row = ParsedRow(name="Sodium", value=38.0, unit="mmol/L", specimen="urine", section="urine spot electrolytes")
    scored = _rows("Sodium 38 mmol/L")[0]
    assert match_analyte("Sodium") is not None                       # precondition: a blood analyte would match
    blood = _analyse(dataclasses.replace(scored, specimen=None))
    assert blood.flag != "NORMAL"                                    # scored as serum sodium 38 it is alarming
    s = _analyse(row)
    assert s.analyte_key is None
    assert not s.flag.startswith("CRITICAL") and s.critical is None
    assert "blood reference ranges and critical limits not applied" in s.note


def test_floor_csf_glucose_low_not_critical():
    base = ParsedRow(name="Glucose", value=28.0, unit="mg/dL", ref_low=40.0, ref_high=70.0, ref_text="40 - 70")
    assert _analyse(dataclasses.replace(base, specimen=None)).flag == "CRITICAL_LOW"   # serum 28 mg/dL is critical
    csf = _analyse(dataclasses.replace(base, specimen="csf"))
    assert csf.flag == "LOW" and csf.analyte_key is None


def test_floor_serum_potassium_still_critical():
    row = ParsedRow(name="Serum Potassium", value=6.9, unit="mmol/L", ref_low=3.5, ref_high=5.1, specimen="serum")
    assert _analyse(row).flag == "CRITICAL_HIGH"


def test_floor_generic_ph_on_urine_row_is_urine_ph():
    row = ParsedRow(name="pH", value=6.0, specimen="urine")
    assert _analyse(row).analyte_key == "urine_ph"


@pytest.mark.parametrize("spec", ["fluid", "other"])
def test_floor_applies_to_fluid_and_other(spec):
    row = ParsedRow(name="Serum Potassium", value=6.9, unit="mmol/L", ref_low=3.5, ref_high=5.1, specimen=spec)
    s = _analyse(row)
    assert s.analyte_key is None and s.critical is None
