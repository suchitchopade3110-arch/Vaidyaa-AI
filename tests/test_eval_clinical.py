"""Clinical source tables and GT derivation (P1 plan section 11). Expectations come from Table C,
printed ranges and standard conversion factors, never from generator output."""

from __future__ import annotations

import ast
import pathlib

import pytest

from tools.eval import critical_table as ct
from tools.eval import units
from tools.eval.catalogue import CATALOGUE

CRIT_CASES = [
    # CL-01
    ("potassium", 6.8, "mmol/L", "CRITICAL_HIGH"),
    ("potassium", 5.6, "mmol/L", None),
    # CL-03
    ("potassium", 2.3, "mmol/L", "CRITICAL_LOW"),
    ("potassium", 3.2, "mmol/L", None),
    # CL-04
    ("sodium", 116, "mmol/L", "CRITICAL_LOW"),
    ("sodium", 129, "mmol/L", None),
    ("sodium", 150, "mmol/L", None),
    ("sodium", 163, "mmol/L", "CRITICAL_HIGH"),
    # CL-05
    ("glucose_fasting", 36, "mg/dL", "CRITICAL_LOW"),
    ("glucose_fasting", 2.0, "mmol/L", "CRITICAL_LOW"),
    ("glucose_fasting", 560, "mg/dL", "CRITICAL_HIGH"),
    ("glucose_fasting", 31.5, "mmol/L", "CRITICAL_HIGH"),
    ("glucose_pp", 268, "mg/dL", None),
    ("glucose_random", 290, "mg/dL", None),
    # CL-08
    ("hemoglobin", 5.8, "g/dL", "CRITICAL_LOW"),
    ("hemoglobin", 58, "g/L", "CRITICAL_LOW"),
    ("hemoglobin", 9.0, "g/dL", None),
    ("hemoglobin", 21.6, "g/dL", "CRITICAL_HIGH"),
    # CL-09
    ("platelets", 14000, "/µL", "CRITICAL_LOW"),
    ("platelets", 14, "x10^3/uL", "CRITICAL_LOW"),
    ("platelets", 0.14, "lakh/cumm", "CRITICAL_LOW"),
    ("platelets", 60, "10^3/µL", None),
    ("platelets", 1200, "10^3/µL", "CRITICAL_HIGH"),
    # CL-10
    ("wbc", 0.8, "10^3/µL", "CRITICAL_LOW"),
    ("wbc", 55, "10^3/µL", "CRITICAL_HIGH"),
    ("wbc", 25, "10^3/µL", None),
    ("wbc", 7.2, "10^3/µL", None),
    # CL-11
    ("neutrophils_abs", 0.3, "10^3/µL", "CRITICAL_LOW"),
    ("neutrophils_abs", 1.4, "10^3/µL", None),
    # CL-12
    ("calcium", 5.8, "mg/dL", "CRITICAL_LOW"),
    ("calcium", 1.40, "mmol/L", "CRITICAL_LOW"),
    ("calcium", 14.0, "mg/dL", "CRITICAL_HIGH"),
    ("calcium", 11.2, "mg/dL", None),
    # CL-13
    ("ionized_calcium", 0.70, "mmol/L", "CRITICAL_LOW"),
    ("magnesium", 0.8, "mg/dL", "CRITICAL_LOW"),
    ("magnesium", 0.30, "mmol/L", "CRITICAL_LOW"),
    ("phosphorus", 0.8, "mg/dL", "CRITICAL_LOW"),
    # CL-14
    ("inr", 6.2, "", "CRITICAL_HIGH"),
    ("inr", 2.8, "", None),
    # CL-15
    ("blood_ph", 7.10, "", "CRITICAL_LOW"),
    ("blood_ph", 7.68, "", "CRITICAL_HIGH"),
    ("pco2", 72, "mmHg", "CRITICAL_HIGH"),
    ("po2", 38, "mmHg", "CRITICAL_LOW"),
    ("bicarbonate", 8, "mmol/L", "CRITICAL_LOW"),
    # CL-16
    ("creatinine", 11.2, "mg/dL", "CRITICAL_HIGH"),
    ("creatinine", 3.8, "mg/dL", None),
    ("creatinine", 990, "µmol/L", "CRITICAL_HIGH"),
    # CL-17
    ("hs_troponin_t", 250, "ng/L", "CRITICAL_HIGH"),
    ("troponin_i", 0.01, "ng/mL", None),
]


@pytest.mark.parametrize("key,value,unit,expected", CRIT_CASES)
def test_cl_table_c_criticals(key, value, unit, expected):  # CL-01, 03-05, 08-17 (critical part)
    assert ct.critical_flag(key, value, unit) == expected
    assert ct.is_critical(key, value, unit) is (expected is not None)


def test_cl02_potassium_high_below_guard_not_critical():
    assert not ct.is_critical("potassium", 5.6, "mmol/L")


def test_cl30_non_blood_never_critical():  # S5
    assert ct.critical_direction("urine_sodium", 100.0, "urine") is None
    assert ct.critical_direction("csf_glucose", 15.0, "csf") is None
    assert ct.critical_direction("potassium", 9.0, "urine") is None
    assert ct.critical_flag("potassium", 9.0, "mmol/L", "csf") is None


@pytest.mark.parametrize(
    "key,value,from_unit,expected,to_unit",
    [
        ("glucose_fasting", 1.0, "mmol/L", 18.016, "mg/dL"),
        ("creatinine", 88.42, "umol/L", 1.0, "mg/dL"),
        ("calcium", 1.0, "mmol/L", 4.008, "mg/dL"),
        ("hba1c", 53.0, "mmol/mol", 7.0, "%"),
        ("urea", 1.0, "mmol/L", 6.006, "mg/dL"),
        ("bilirubin_total", 17.1, "umol/L", 1.0, "mg/dL"),
    ],
)
def test_cl32_conversions(key, value, from_unit, expected, to_unit):
    got, unit = units.to_canonical(key, value, from_unit)
    assert unit == to_unit
    assert got == pytest.approx(expected, rel=0.002)


def test_cl32_hba1c_forward():
    assert units.from_canonical("hba1c", 7.0, "mmol/mol") == pytest.approx(53.0, rel=0.002)


def test_catalogue_registry_namespace():
    """Namespace check only: every key marked registry=True exists in the product registry."""
    from app.services.lab_engine import REGISTRY

    missing = [k for k, a in CATALOGUE.items() if a.registry and k not in REGISTRY]
    assert not missing


def test_catalogue_consensus_spot_checks():
    """Hand-checked against plan section 3.4."""
    hb = CATALOGUE["hemoglobin"].consensus
    assert (hb.male.low, hb.male.high, hb.female.low, hb.female.high) == (13.0, 17.0, 12.0, 15.0)
    assert CATALOGUE["potassium"].consensus.unisex.high == 5.1
    assert CATALOGUE["hba1c"].consensus.unisex.kind == "upper"
    assert CATALOGUE["psa"].sex_only == "M"
    assert CATALOGUE["urine_sodium"].specimen == "urine"
    assert CATALOGUE["widal_o"].value_type == "titre" and CATALOGUE["widal_o"].key_optional


def test_table_c_guard_edges():
    assert ct.CRITICAL["potassium"].high_guard == (6.0, 6.5)
    assert ct.CRITICAL["hemoglobin"].low_guard == (6.0, 8.0)
    assert ct.in_guard("potassium", 6.2) and not ct.in_guard("potassium", 5.9)
    assert ct.CAPS["triglycerides"] == (None, 499.0)


def test_gt_side_imports_nothing_from_app():
    root = pathlib.Path(__file__).resolve().parents[1] / "tools" / "eval"
    bad = []
    for name in ("catalogue", "critical_table", "units", "gt_schema", "clinical", "scenarios", "config"):
        tree = ast.parse((root / f"{name}.py").read_text())
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                if m.split(".")[0] in ("app", "clinical_reference") or m.startswith("tools.eval.adapters"):
                    bad.append((name, m))
    assert not bad


# ======================================================================== T03: GT derivation rules
from tools.eval import clinical as cl  # noqa: E402
from tools.eval.clinical import OptionRange as Opt  # noqa: E402
from tools.eval.clinical import PrintedRange as PR  # noqa: E402


def rng(lo=None, hi=None):
    kind = "two_sided" if lo is not None and hi is not None else "lower" if lo is not None else "upper"
    return PR(kind, lo, hi)


FLAG_CASES = [
    ("potassium", 6.8, "mmol/L", rng(3.5, 5.1), "CRITICAL_HIGH", True),  # CL-01
    ("potassium", 5.6, "mmol/L", rng(3.5, 5.1), "HIGH", False),  # CL-02
    ("potassium", 2.3, "mmol/L", rng(3.5, 5.1), "CRITICAL_LOW", True),  # CL-03
    ("potassium", 3.2, "mmol/L", rng(3.5, 5.1), "LOW", False),
    ("sodium", 116, "mmol/L", rng(135, 145), "CRITICAL_LOW", True),  # CL-04
    ("sodium", 129, "mmol/L", rng(135, 145), "LOW", False),
    ("sodium", 150, "mmol/L", rng(135, 145), "HIGH", False),
    ("sodium", 163, "mmol/L", rng(135, 145), "CRITICAL_HIGH", True),
    ("glucose_fasting", 36, "mg/dL", rng(70, 100), "CRITICAL_LOW", True),  # CL-05
    ("glucose_fasting", 2.0, "mmol/L", rng(3.9, 5.6), "CRITICAL_LOW", True),
    ("glucose_fasting", 560, "mg/dL", rng(70, 100), "CRITICAL_HIGH", True),
    ("glucose_fasting", 31.5, "mmol/L", rng(3.9, 5.6), "CRITICAL_HIGH", True),
    ("glucose_pp", 268, "mg/dL", rng(hi=140), "HIGH", False),
    ("glucose_random", 290, "mg/dL", rng(70, 140), "HIGH", False),
    ("hemoglobin", 5.8, "g/dL", rng(12.0, 15.5), "CRITICAL_LOW", True),  # CL-08
    ("hemoglobin", 58, "g/L", rng(120, 155), "CRITICAL_LOW", True),
    ("hemoglobin", 9.0, "g/dL", rng(12.0, 15.5), "LOW", False),
    ("hemoglobin", 21.6, "g/dL", rng(13.0, 17.0), "CRITICAL_HIGH", True),
    ("platelets", 14000, "/µL", rng(150000, 450000), "CRITICAL_LOW", True),  # CL-09
    ("platelets", 14, "10^3/µL", rng(150, 450), "CRITICAL_LOW", True),
    ("platelets", 0.14, "lakh/cumm", rng(1.5, 4.5), "CRITICAL_LOW", True),
    ("platelets", 60, "10^3/µL", rng(150, 450), "LOW", False),
    ("platelets", 1200, "10^3/µL", rng(150, 450), "CRITICAL_HIGH", True),
    ("wbc", 0.8, "10^3/µL", rng(4, 11), "CRITICAL_LOW", True),  # CL-10
    ("wbc", 55, "10^3/µL", rng(4, 11), "CRITICAL_HIGH", True),
    ("wbc", 25, "10^3/µL", rng(4, 11), "HIGH", False),
    ("wbc", 7.2, "10^3/µL", rng(4.0, 11.0), "NORMAL", False),
    ("neutrophils_abs", 0.3, "10^3/µL", rng(2.0, 7.0), "CRITICAL_LOW", True),  # CL-11
    ("neutrophils_abs", 1.4, "10^3/µL", rng(2.0, 7.0), "LOW", False),
    ("calcium", 5.8, "mg/dL", rng(8.5, 10.5), "CRITICAL_LOW", True),  # CL-12
    ("calcium", 1.40, "mmol/L", rng(2.12, 2.62), "CRITICAL_LOW", True),
    ("calcium", 14.0, "mg/dL", rng(8.5, 10.5), "CRITICAL_HIGH", True),
    ("calcium", 11.2, "mg/dL", rng(8.5, 10.5), "HIGH", False),
    ("ionized_calcium", 0.70, "mmol/L", rng(1.12, 1.32), "CRITICAL_LOW", True),  # CL-13
    ("magnesium", 0.8, "mg/dL", rng(1.7, 2.4), "CRITICAL_LOW", True),
    ("magnesium", 0.30, "mmol/L", rng(0.7, 1.0), "CRITICAL_LOW", True),
    ("phosphorus", 0.8, "mg/dL", rng(2.5, 4.5), "CRITICAL_LOW", True),
    ("inr", 6.2, "", rng(0.8, 1.2), "CRITICAL_HIGH", True),  # CL-14
    ("inr", 2.8, "", rng(0.8, 1.2), "HIGH", False),
    ("blood_ph", 7.10, "", rng(7.35, 7.45), "CRITICAL_LOW", True),  # CL-15
    ("blood_ph", 7.68, "", rng(7.35, 7.45), "CRITICAL_HIGH", True),
    ("pco2", 72, "mmHg", rng(35, 45), "CRITICAL_HIGH", True),
    ("po2", 38, "mmHg", rng(80, 100), "CRITICAL_LOW", True),
    ("bicarbonate", 8, "mmol/L", rng(22, 29), "CRITICAL_LOW", True),
    ("creatinine", 11.2, "mg/dL", rng(0.7, 1.3), "CRITICAL_HIGH", True),  # CL-16
    ("creatinine", 3.8, "mg/dL", rng(0.7, 1.3), "HIGH", False),
    ("creatinine", 990, "µmol/L", rng(62, 115), "CRITICAL_HIGH", True),
    ("hs_troponin_t", 250, "ng/L", rng(hi=14), "CRITICAL_HIGH", True),  # CL-17
    ("troponin_i", 0.01, "ng/mL", rng(hi=0.04), "NORMAL", False),
]


@pytest.mark.parametrize("key,value,unit,printed,flag,crit", FLAG_CASES)
def test_cl01_17_flags(key, value, unit, printed, flag, crit):
    t = cl.derive_row(key, value=value, unit=unit, printed_range=printed, input_sex="F", input_age=40)
    assert t.flag == flag and t.flag_true == flag
    assert t.is_critical is crit
    assert not t.ambiguity


def test_cl06_urine_rows_never_critical():  # S5 (N3)
    t = cl.derive_row("urine_sodium", value=38, unit="mmol/L", printed_range=rng(40, 220))
    assert (t.flag, t.is_critical) == ("LOW", False)
    assert cl.derive_row("urine_potassium", value=42, unit="mmol/L", printed_range=rng(25, 125)).flag == "NORMAL"
    t = cl.derive_row("urine_creatinine", value=88, unit="mg/dL", printed_range=rng(39, 259), input_sex="M")
    assert (t.flag, t.is_critical) == ("NORMAL", False)
    # urine glucose quantitative variant: 1000 mg/dL against "< 15" -> HIGH (not critical, blood-only rule)
    assert ct.critical_direction("glucose_fasting", 1000.0, "urine") is None


def test_cl07_csf_rows_never_critical():
    t = cl.derive_row("csf_glucose", value=28, unit="mg/dL", printed_range=rng(40, 70))
    assert (t.flag, t.is_critical) == ("LOW", False)
    t = cl.derive_row("csf_protein", value=120, unit="mg/dL", printed_range=rng(15, 45))
    assert (t.flag, t.is_critical) == ("HIGH", False)
    t = cl.derive_row("csf_neutrophils_pct", value=80, unit="%", text_value="80")
    assert (t.flag, t.is_critical) == ("INFO", False)


SEX_OPTS = PR("sex", options=(Opt("M", 13.5, 17.5, sex="M"), Opt("F", 12.0, 15.5, sex="F")), text="M: 13.5-17.5 F: 12.0-15.5")


def test_cl19_sex_ranges_without_sex():
    t = cl.derive_row("hemoglobin", value=13.0, unit="g/dL", printed_range=SEX_OPTS, true_sex="M", true_age=40)
    assert t.flag == "UNKNOWN" and t.ambiguity == ["AMB_SEX_RANGES_NO_SEX"] and t.expected_needs_review
    assert t.flag_true == "LOW"
    t = cl.derive_row("hemoglobin", value=13.0, unit="g/dL", printed_range=SEX_OPTS, true_sex="F", true_age=40)
    assert t.flag == "UNKNOWN" and t.flag_true == "NORMAL"
    t = cl.derive_row("hemoglobin", value=10.5, unit="g/dL", printed_range=SEX_OPTS, true_sex="F", true_age=40)
    assert t.flag == "LOW" and not t.ambiguity and t.flag_true == "LOW"
    # sex known: the matching option applies
    t = cl.derive_row("hemoglobin", value=13.0, unit="g/dL", printed_range=SEX_OPTS, input_sex="F", true_sex="F")
    assert t.flag == "NORMAL" and not t.ambiguity


def test_age_labelled_ranges_without_age():
    opts = PR("age", options=(Opt("Adult", 12.0, 15.5, age_min=18), Opt("Child", 11.0, 14.0, age_min=0, age_max=17)))
    t = cl.derive_row("hemoglobin", value=11.5, unit="g/dL", printed_range=opts, true_age=40, true_sex="F")
    assert t.flag == "UNKNOWN" and t.ambiguity == ["AMB_AGE_RANGES_NO_AGE"] and t.flag_true == "LOW"
    t = cl.derive_row("hemoglobin", value=11.5, unit="g/dL", printed_range=opts, input_age=8, true_age=8, true_sex="F")
    assert t.flag == "NORMAL"


def test_cl20_paediatric_printed_range_wins():
    t = cl.derive_row("hemoglobin", value=11.8, unit="g/dL", printed_range=rng(11.5, 15.5), input_age=6, input_sex="F")
    assert t.flag == "NORMAL" and t.flag_true == "NORMAL"
    # against the adult female range the same value would be LOW
    assert cl.derive_row("hemoglobin", value=11.8, unit="g/dL", printed_range=rng(12.0, 15.5)).flag == "LOW"


def test_cl21_interpretive_only_hba1c():
    t = cl.derive_row("hba1c", value=7.8, unit="%", printed_range=PR("interpretive", text="ADA category table"),
                      input_sex="F", input_age=50, true_sex="F", true_age=50)
    assert t.effective_range_page is None
    assert t.flag == "HIGH" and t.flag_true == "HIGH" and not t.ambiguity


def test_cl22_one_sided_with_printed_flag():
    t = cl.derive_row("hba1c", value=7.8, unit="%", printed_range=rng(hi=5.7), printed_flag="H")
    assert t.flag == "HIGH" and not t.ambiguity


def test_cl23_titres():
    t = cl.derive_row("widal_o", text_value="1:160", titre_cutoff=160)
    assert (t.flag, t.flag_true, t.ambiguity) == ("POSITIVE", "POSITIVE", [])
    t = cl.derive_row("widal_h", text_value="1:40")
    assert t.flag == "UNKNOWN" and t.ambiguity == ["AMB_TITRE_NO_CUTOFF"] and t.flag_true == "NEGATIVE"


def test_cl24_qualitative_and_info():
    assert cl.derive_row("hiv_screen", text_value="Non-Reactive").flag == "NEGATIVE"
    t = cl.derive_row("hbsag", text_value="Reactive")
    assert (t.flag, t.is_critical) == ("POSITIVE", False)
    assert cl.derive_row("blood_group_abo", text_value="B Positive").flag == "INFO"


def test_cl25_qualifiers():
    assert cl.derive_row("tsh", value=0.01, qualifier="<", unit="uIU/mL", printed_range=rng(0.4, 4.0)).flag == "LOW"
    assert cl.derive_row("ferritin", value=1650, qualifier=">", unit="ng/mL", printed_range=rng(11, 307)).flag == "HIGH"
    assert cl.derive_row("crp", value=5, qualifier="<", unit="mg/L", printed_range=rng(0, 6)).flag == "NORMAL"


def test_cl26_printed_flag_conflict():
    t = cl.derive_row("potassium", value=4.2, unit="mmol/L", printed_range=rng(3.5, 5.1), printed_flag="H")
    assert t.flag == "NORMAL" and t.ambiguity == ["AMB_PRINTED_FLAG_CONFLICT"] and t.expected_needs_review


def test_cl27_unitless_counts():
    t = cl.derive_row("wbc", value=7.2, unit="", unit_true="10^3/µL", printed_range=PR())
    assert t.flag == "UNKNOWN" and "AMB_UNITLESS_COUNT" in t.ambiguity and t.flag_true == "NORMAL"
    t = cl.derive_row("wbc", value=0.8, unit="", unit_true="10^3/µL", printed_range=PR())
    assert t.flag == "UNKNOWN" and t.flag_true == "CRITICAL_LOW" and t.is_critical
    t = cl.derive_row("wbc", value=7.2, unit="x10^3/uL", unit_true="10^3/µL", printed_range=PR())
    assert t.flag == "NORMAL" and not t.ambiguity


def test_cl28_headlines():
    def h(*rows):
        return cl.headline([(r.is_critical, r.flag_true) for r in rows])

    k = cl.derive_row("potassium", value=6.8, unit="mmol/L", printed_range=rng(3.5, 5.1))
    alt = cl.derive_row("alt", value=85, unit="U/L", printed_range=rng(7, 56))
    ok = cl.derive_row("sodium", value=140, unit="mmol/L", printed_range=rng(135, 145))
    una = cl.derive_row("urine_sodium", value=38, unit="mmol/L", printed_range=rng(40, 220))
    hbs = cl.derive_row("hbsag", text_value="Reactive")
    amb = cl.derive_row("hemoglobin", value=13.0, unit="g/dL", printed_range=SEX_OPTS, true_sex="F")
    assert h(k) == "critical"
    assert h(alt) == "abnormal"
    assert h(ok, ok) == "normal"
    assert h(una) == "abnormal"
    assert h(hbs) == "abnormal"
    assert h(amb) == "normal"


def test_cl29_urine_qualitatives():
    assert cl.derive_row("urine_protein", text_value="Trace").flag == "POSITIVE"
    assert cl.derive_row("urine_protein", text_value="Nil").flag == "NEGATIVE"
    assert cl.derive_row("urine_urobilinogen", text_value="Normal").flag == "NEGATIVE"
    assert cl.derive_row("urine_protein", text_value="2+").flag == "POSITIVE"


def test_range_absent_sex_dependent_no_sex():
    # Hb 7.0 with no printed range and unknown sex: low for both sexes -> determinable
    t = cl.derive_row("hemoglobin", value=9.0, unit="g/dL", printed_range=PR(), true_sex="M", true_age=40)
    assert t.flag == "LOW" and not t.ambiguity
    t = cl.derive_row("hemoglobin", value=12.5, unit="g/dL", printed_range=PR(), true_sex="F", true_age=40)
    assert t.flag == "UNKNOWN" and t.ambiguity == ["AMB_NO_RANGE_NO_DEMOGRAPHICS"] and t.flag_true == "NORMAL"


def test_unlabelled_multivalue():
    t = cl.derive_row("potassium", value=4.2, unit="mmol/L", printed_range=rng(3.5, 5.1), unlabelled_multivalue=True)
    assert t.flag == "UNKNOWN" and t.ambiguity == ["AMB_UNLABELLED_MULTIVALUE"] and t.flag_true == "NORMAL"


def test_ckd_epi_2021_hand_values():
    assert cl.ckd_epi_2021(1.0, 50, "F") == pytest.approx(68.6, abs=0.3)
    assert cl.ckd_epi_2021(1.0, 60, "M") == pytest.approx(86.2, abs=0.3)


def test_oracle_scorer():
    from types import SimpleNamespace as NS

    rows = [NS(analyte_key="potassium", value_num=6.8, unit_raw="mmol/L", ref_low=3.5, ref_high=5.1, qualifier=None,
               text_value=None, specimen="serum"),
            NS(analyte_key="sodium", value_num=140, unit_raw="mmol/L", ref_low=135, ref_high=145, qualifier=None,
               text_value=None, specimen="serum")]
    s = cl.ClinicalRulesScorer().score(rows, "F", 40)
    assert s.headline == "critical" and s.row_flags == ["CRITICAL_HIGH", "NORMAL"]
    assert cl.ClinicalRulesScorer().score(rows[1:], None, None).headline == "normal"
    unk = [NS(analyte_key=None, value_num=1, unit_raw="", ref_low=None, ref_high=None, qualifier=None, text_value=None,
              specimen="serum")]
    assert cl.ClinicalRulesScorer().score(unk, None, None).headline == "unknown"


def _titre(text, ref_text):
    from types import SimpleNamespace as NS

    return NS(analyte_key="widal_o", value_num=None, unit_raw="", ref_low=None, ref_high=None, ref_text=ref_text,
              qualifier=None, text_value=text, specimen="serum", printed_flag="")


def test_oracle_scorer_titres_hand_derived():
    """Widal O 1:160 against a printed cut-off of 1:80 is positive (160 >= 80); 1:40 is not (40 < 80)."""
    cut = "Significant titre: >= 1:80"
    s = cl.ClinicalRulesScorer()
    pos = s.score([_titre("1:160", cut)], None, None)
    assert pos.row_flags == ["POSITIVE"] and pos.headline == "abnormal"
    neg = s.score([_titre("1:40", cut)], None, None)
    assert neg.row_flags == ["NEGATIVE"] and neg.headline == "normal"
    assert s.score([_titre("1:80", cut)], None, None).row_flags == ["POSITIVE"]  # at the cut-off counts
    # no printed cut-off: the row stays ambiguous (UNKNOWN); the headline reads 1:320 as positive, 1:40 as normal
    amb_hi = s.score([_titre("1:320", None)], None, None)
    assert amb_hi.row_flags == ["UNKNOWN"] and amb_hi.headline == "abnormal"
    amb_lo = s.score([_titre("1:40", "")], None, None)
    assert amb_lo.row_flags == ["UNKNOWN"] and amb_lo.headline == "normal"


# CL-31: targets equal PRD section 3 per rendering (literal percentages, never read back from targets.py)
PRD_TARGETS = {  # metric -> rendering -> (op, percent) ; None = informational
    "M1": {"native": ("<=", "0.5"), "csv": ("<=", "0.5"), "scan": ("<=", "1.5"), "rot90": ("<=", "1.5"),
           "photo": ("<=", "1.5")},
    "M2": dict.fromkeys(("native", "csv", "scan", "rot90", "photo"), ("==", "100")),
    "M3": {"native": (">=", "92"), "csv": (">=", "92"), "scan": (">=", "80"), "rot90": (">=", "80"),
           "photo": (">=", "70")},
    "M4": dict.fromkeys(("native", "csv", "scan", "rot90", "photo"), ("<=", "0.5")),
    "M5": dict.fromkeys(("native", "csv", "scan", "rot90", "photo"), (">=", "98")),
    # PRD M6 names no rendering (only M7 says "native-text"), so 90% applies to every rendering (P1 review LOW-2)
    "M6": dict.fromkeys(("native", "csv", "scan", "rot90", "photo"), (">=", "90")),
    "M7": {"native": ("<=", "15"), "csv": ("<=", "15"), "scan": None, "rot90": None, "photo": None},
}
# Not a PRD section 3 row: S3's final state ("can never END UP not CRITICAL and not needs_review"). After
# confirmation nothing is needs_review and alerts come only from scoring the confirmed rows (FR-1.4), so a GT critical
# must end up CRITICAL: the same 100% as M2, on every rendering (P1 review MED-5).
S3_TARGETS = {"M2_post": dict.fromkeys(("native", "csv", "scan", "rot90", "photo"), ("==", "100"))}


def test_cl31_targets_equal_prd_section_3():
    from fractions import Fraction

    from tools.eval import targets as tg

    assert set(tg.TARGETS) == set(PRD_TARGETS) | set(S3_TARGETS)
    assert set(tg.RATCHETED) == {"M1", "M2", "M2_post", "M3", "M4", "M5"}  # M6 and M7 are target-only (plan 6.9)
    for m, by_r in {**PRD_TARGETS, **S3_TARGETS}.items():
        assert set(tg.TARGETS[m]) == set(by_r)
        for r, want in by_r.items():
            got = tg.TARGETS[m][r]
            if want is None:
                assert got is None, (m, r)
            else:
                assert got["op"] == want[0] and got["value"] == Fraction(want[1]) / 100, (m, r)


def test_cl31_targets_are_immutable():
    from tools.eval import targets as tg

    with pytest.raises(TypeError):
        tg.TARGETS["M1"] = {}
    with pytest.raises(TypeError):
        tg.TARGETS["M1"]["native"] = None
    with pytest.raises(TypeError):
        tg.TARGETS["M1"]["native"]["value"] = 1


def test_cl31_exact_boundaries():
    from tools.eval import targets as tg

    assert tg.passes("M1", "native", 5, 1000) is True and tg.passes("M1", "native", 6, 1000) is False
    assert tg.passes("M1", "scan", 15, 1000) is True and tg.passes("M1", "scan", 16, 1000) is False
    assert tg.passes("M2", "photo", 99, 100) is False and tg.passes("M2", "photo", 100, 100) is True
    assert tg.passes("M3", "photo", 70, 100) is True and tg.passes("M3", "photo", 69, 100) is False
    assert tg.passes("M5", "native", 49, 50) is True and tg.passes("M5", "native", 48, 50) is False
    assert tg.passes("M7", "native", 15, 100) is True and tg.passes("M7", "native", 16, 100) is False
    assert tg.passes("M7", "scan", 99, 100) is None and tg.passes("M2", "native", 0, 0) is None
    assert tg.passes("M2_post", "scan", 35, 36) is False and tg.passes("M2_post", "scan", 36, 36) is True
    assert tg.passes("M6", "photo", 9, 10) is True and tg.passes("M6", "photo", 89, 100) is False


# ======================================================================== T04: sampling invariants
import random  # noqa: E402

from tools.eval import scenarios as sc  # noqa: E402

N_SAMPLES = 2000
# states that Table C's guard bands leave room for, derived by hand (e.g. K 5.1 -> 1.25x = 6.4 > 6.0 guard edge)
MUST_EXIST = {
    ("glucose_fasting", "severe"), ("hemoglobin", "severe"), ("wbc", "severe"), ("platelets", "severe"),
    ("creatinine", "severe"), ("inr", "severe"),
}


@pytest.mark.parametrize("key", [k for k in ct.CRITICAL if k in CATALOGUE])
@pytest.mark.parametrize("state", sc.STATES)
def test_cl18_sampled_values_respect_invariants(key, state):
    a = CATALOGUE[key]
    printed, unit = sc.printed_range_for(a, 0, "M", 40)
    rnd = random.Random(18)
    got = 0
    upper = printed.high
    bounds = [b for b in (printed.low, printed.high) if b is not None]
    for _ in range(N_SAMPLES):
        s = sc.sample_value(key, state, rnd, printed=printed, unit=unit, sex_code="M")
        if s is None:
            continue
        got += 1
        v = float(s.value)
        canon, _ = units.to_canonical(key, v, unit)
        assert not ct.in_guard(key, canon, upper), (key, state, v)
        assert all(v != b for b in bounds), (key, state, v)
        if a.plaus:
            assert a.plaus[0] <= canon <= a.plaus[1]
        assert not ct.violates_cap(key, canon)
        crit = ct.critical_direction(key, canon)
        assert (crit is not None) == (state == "critical"), (key, state, v)
    if state in ("normal", "critical") or (key, state) in MUST_EXIST:
        assert got > N_SAMPLES * 0.9, (key, state, got)


def test_cl18_state_semantics_against_printed_range():
    rnd = random.Random(5)
    r = sc.Rng("two_sided", 12.0, 15.0)
    for _ in range(300):
        n = sc.sample_value("hemoglobin", "normal", rnd, printed=r, unit="g/dL")
        assert 12.0 + 0.3 - 0.05 <= float(n.value) <= 15.0 - 0.3 + 0.05  # middle 80% (+- rounding)
        m = sc.sample_value("hemoglobin", "mild", rnd, printed=r, unit="g/dL", direction="low")
        assert 0.8 * 12.0 - 0.05 <= float(m.value) < 12.0
        h = sc.sample_value("hemoglobin", "mild", rnd, printed=r, unit="g/dL", direction="high")
        assert 15.0 < float(h.value) <= 1.25 * 15.0 + 0.05


def test_sampling_in_si_unit_checks_invariants_after_rounding():
    rnd = random.Random(7)
    r = sc.Rng("two_sided", 3.9, 5.6)  # mmol/L fasting glucose
    for _ in range(300):
        s = sc.sample_value("glucose_fasting", "critical", rnd, printed=r, unit="mmol/L", direction="high")
        assert float(s.value) * 18.016 > 500 and len(s.text.split(".")[1]) == 1


def test_range_absent_clear_margins():
    rnd = random.Random(9)
    for _ in range(200):
        n = sc.sample_value("hemoglobin", "normal", rnd, printed=None, range_absent=True, sex_code="F")
        assert 12.0 + 0.2 * 3.0 - 0.05 <= float(n.value) <= 15.0 - 0.2 * 3.0 + 0.05  # middle 60% of F 12-15
        h = sc.sample_value("alt", "mild", rnd, printed=None, range_absent=True, direction="high")
        assert float(h.value) >= 1.15 * 56 - 0.5  # furthest-out upper bound across ALT variants is 56
        lo = sc.sample_value("hemoglobin", "mild", rnd, printed=None, range_absent=True, direction="low")
        assert float(lo.value) <= 0.85 * 11.5 + 0.05  # lowest bound across variants is 11.5


def test_hemoglobin_high_without_range_is_only_critical():
    # variants reach 18.0; 15% beyond is 20.7, past the 18.5-21.0 guard band -> no non-critical high exists
    assert sc.sample_value("hemoglobin", "mild", random.Random(1), printed=None, range_absent=True,
                           direction="high") is None


def test_troponin_has_no_mild_zone():
    rnd = random.Random(3)
    r = sc.Rng("upper", None, 14.0)
    assert sc.sample_value("hs_troponin_t", "mild", rnd, printed=r, unit="ng/L") is None


def test_derived_values_hand_checked():
    assert sc.indirect_bilirubin(1.8, 0.6) == 1.2
    assert sc.globulin(7.2, 4.2) == 3.0
    assert sc.ag_ratio(4.2, 3.0) == 1.4
    assert sc.vldl(150) == 30
    assert sc.ldl_friedewald(200, 50, 150) == 120
    assert sc.ldl_friedewald(300, 40, 400) is None  # TG >= 400: "Not calculated"
    assert sc.non_hdl(200, 50) == 150
    assert sc.mchc(12.0, 36.0) == pytest.approx(33.3, abs=0.05)
    assert sc.mcv(40.0, 4.5) == pytest.approx(88.9, abs=0.05)
    assert sc.mch(13.5, 4.5) == 30.0
    assert sc.abs_count(8.0, 60) == 4.8
    assert sc.egfr_ckd_epi_2021(1.0, 50, "F") == 69  # hand calc 68.6
    assert sc.egfr_ckd_epi_2021(1.0, 60, "M") == 86  # hand calc 86.2


def test_seeding_is_deterministic_and_purpose_separated():
    from tools.eval import config as cfg

    assert cfg.seed_for(1, "test", 3, "plan") == cfg.seed_for(1, "test", 3, "plan")
    assert cfg.seed_for(1, "test", 3, "plan") != cfg.seed_for(1, "test", 3, "values")
    assert cfg.DEVTEST_SEED != cfg.HELDOUT_SEED
    a = sc.build_patients(40, random.Random(1))
    assert a == sc.build_patients(40, random.Random(1))


# ---------------------------------------------------------------- T16 (zoro) deferred GT finding F1

# Range-absent rows printed in an SI or alternative unit, compared with the conventional-unit consensus interval.
# Each value is clearly normal: (key, printed value, printed unit, sex, value in the conventional unit).
SI_ABSENT_NORMAL = [
    ("creatinine", 80, "umol/L", "M"),  # 0.90 mg/dL vs M 0.7-1.3
    ("hemoglobin", 140, "g/L", "M"),  # 14.0 g/dL vs M 13.0-17.0
    ("glucose_fasting", 5.0, "mmol/L", "F"),  # 90 mg/dL vs 70-100
    ("bilirubin_total", 10, "umol/L", "F"),  # 0.58 mg/dL vs 0.3-1.2
    ("calcium", 2.35, "mmol/L", "M"),  # 9.4 mg/dL vs 8.5-10.5
]


@pytest.mark.parametrize("key,value,unit,sex", SI_ABSENT_NORMAL)
def test_range_absent_si_value_is_judged_in_canonical_units(key, value, unit, sex):
    from tools.eval import review_sim as rs

    t = cl.derive_row(key, value=value, unit=unit, unit_true=unit, printed_range=PR("none"), input_sex=sex,
                      input_age=40, true_sex=sex, true_age=40)
    assert (t.flag, t.flag_true) == ("NORMAL", "NORMAL")
    # the confirmed row the simulated clinician builds from GT (unit_true + effective_range_true) must agree
    g = {"name": key, "analyte_key": key, "value": value, "value_text": str(value), "qualifier": None,
         "text_value": None, "unit_true": unit, "specimen": "serum", "range": {"text": ""},
         "effective_range_true": t.effective_range_true}
    assert cl.ClinicalRulesScorer().score([rs.true_row(g)], sex, 40).headline == "normal"


def test_unitless_count_true_flag_uses_the_true_unit():
    t = cl.derive_row("wbc", value=7200, unit="", unit_true="/cumm", printed_range=PR("none"), input_sex="M",
                      input_age=40, true_sex="M", true_age=40)
    assert t.flag == "UNKNOWN" and t.ambiguity == ["AMB_UNITLESS_COUNT"]
    assert t.flag_true == "NORMAL"  # 7.2 x10^3/uL vs 4.0-11.0
