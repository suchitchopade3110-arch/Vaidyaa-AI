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
