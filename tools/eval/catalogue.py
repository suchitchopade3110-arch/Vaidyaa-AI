"""Analyte catalogue used to derive ground truth (P1 plan section 3.4, Table P).

GT-side module: imports nothing from ``app`` or ``clinical_reference``. All
values are conventional units, adult, unless a variant carries its own unit.
The first reference variant of each analyte is the *consensus interval*; it is
used only when a report prints no range.
"""

from __future__ import annotations

from dataclasses import dataclass, field

LOCALES = ("IN", "US", "UK", "generic")


@dataclass(frozen=True)
class Rng:
    """A reference range. ``kind``: two_sided (lo-hi inclusive), upper ('< b'), lower ('> a')."""

    kind: str
    low: float | None
    high: float | None


def R(lo: float, hi: float) -> Rng:
    return Rng("two_sided", lo, hi)


def U(b: float) -> Rng:
    return Rng("upper", None, b)


def L(a: float) -> Rng:  # noqa: E743
    return Rng("lower", a, None)


@dataclass(frozen=True)
class RefVariant:
    """One lab-specific reference. Either ``unisex`` or sex-specific ``male``/``female``."""

    unisex: Rng | None = None
    male: Rng | None = None
    female: Rng | None = None
    unit: str | None = None  # None = the analyte's conventional unit
    label: str = ""

    @property
    def sex_specific(self) -> bool:
        return self.unisex is None


def V(u: Rng | None = None, m: Rng | None = None, f: Rng | None = None, unit: str | None = None) -> RefVariant:
    return RefVariant(unisex=u, male=m, female=f, unit=unit)


@dataclass(frozen=True)
class Analyte:
    key: str
    display: str
    names: dict[str, tuple[str, ...]]
    specimen: str  # serum (any blood specimen) | urine | csf | fluid
    value_type: str  # numeric | qualitative | titre | range_result | info
    key_optional: bool
    unit: str
    unit_spellings: tuple[str, ...]
    si_unit: str | None
    si_factor: float | None  # conventional x factor = SI
    decimals: int
    si_decimals: int
    plaus: tuple[float, float] | None
    refs: tuple[RefVariant, ...]
    ped: dict[str, RefVariant]  # band ('2-5','6-11','12-17') -> variant (Table P)
    can_qualify: bool
    derived: str | None
    panels: tuple[str, ...]
    registry: bool
    alt_units: dict[str, float] = field(default_factory=dict)  # spelling -> multiplier to conventional
    alt_decimals: dict[str, int] = field(default_factory=dict)  # canonical token -> decimals
    normal_texts: tuple[str, ...] = ()
    sex_only: str | None = None  # 'M' when only reported for males (PSA)

    @property
    def consensus(self) -> RefVariant:
        return self.refs[0]

    @property
    def sex_dependent(self) -> bool:
        return any(v.sex_specific for v in self.refs)


# keys absent from the product registry today (contract for P3, plan section 3.4)
_NOT_IN_REGISTRY = frozenset(
    {
        "urine_sodium", "urine_potassium", "urine_creatinine", "urine_protein", "urine_glucose", "urine_ketones",
        "urine_blood", "urine_bilirubin", "urine_urobilinogen", "urine_nitrite", "urine_leukocyte_esterase",
        "urine_casts", "urine_bacteria", "urine_crystals", "urine_colour", "urine_appearance",
        "csf_glucose", "csf_protein", "csf_wbc", "csf_chloride", "csf_neutrophils_pct", "csf_lymphocytes_pct",
        "csf_appearance", "hiv_screen", "hbsag", "anti_hcv", "vdrl", "dengue_ns1", "dengue_igm",
        "widal_o", "widal_h", "widal_ah", "widal_bh", "ra_factor", "aso", "blood_group_abo", "blood_group_rh",
    }
)

CATALOGUE: dict[str, Analyte] = {}
PEDIATRIC_BANDS = ("2-5", "6-11", "12-17")


def _add(
    key, display, unit, dec, plaus, refs, *, si=None, panels=(), specimen="serum", value_type="numeric",
    aka=None, spell=(), alt=None, alt_dec=None, ped=None, qual=False, derived=None, normal_texts=(),
    sex_only=None,
):
    names = {loc: (display,) for loc in LOCALES}
    for loc, vals in (aka or {}).items():
        names[loc] = tuple(vals)
        if loc == "generic":
            for other in ("IN", "US", "UK"):
                if other not in (aka or {}):
                    names[other] = tuple(vals)
    si_unit, si_factor, si_dec = si if si else (None, None, dec)
    CATALOGUE[key] = Analyte(
        key=key, display=display, names=names, specimen=specimen, value_type=value_type,
        key_optional=value_type in ("qualitative", "titre", "info", "range_result"),
        unit=unit, unit_spellings=tuple(spell) or (unit,), si_unit=si_unit, si_factor=si_factor,
        decimals=dec, si_decimals=si_dec, plaus=plaus, refs=tuple(refs), ped=ped or {}, can_qualify=qual,
        derived=derived, panels=tuple(panels), registry=key not in _NOT_IN_REGISTRY,
        alt_units=alt or {}, alt_decimals=alt_dec or {}, normal_texts=tuple(normal_texts), sex_only=sex_only,
    )


_CNT = {"/uL": 0.001}

# ---------------------------------------------------------------- haematology (CBC)
_add("hemoglobin", "Hemoglobin", "g/dL", 1, (2.0, 25.0),
     [V(m=R(13.0, 17.0), f=R(12.0, 15.0)), V(m=R(13.5, 17.5), f=R(12.0, 15.5)), V(m=R(13.0, 18.0), f=R(11.5, 16.5))],
     si=("g/L", 10, 0), panels=("CBC",), spell=("g/dL", "gm/dL", "gm%", "g%"),
     aka={"generic": ["Hemoglobin", "Haemoglobin", "Hb", "Hemoglobin (Hb)"], "UK": ["Haemoglobin"], "US": ["Hemoglobin"]},
     ped={"2-5": V(u=R(11.0, 14.0)), "6-11": V(u=R(11.5, 15.5)), "12-17": V(m=R(13.0, 16.0), f=R(12.0, 16.0))})
_add("hematocrit", "Hematocrit", "%", 1, (8.0, 70.0),
     [V(m=R(40, 50), f=R(36, 46)), V(m=R(41, 53), f=R(36, 46))], si=("L/L", 0.01, 2), panels=("CBC",),
     aka={"generic": ["Hematocrit", "Haematocrit", "PCV", "Packed Cell Volume (PCV)", "Hct"]},
     ped={"2-5": V(u=R(33, 42)), "6-11": V(u=R(35, 45)), "12-17": V(m=R(37, 49), f=R(36, 46))})
_add("rbc", "RBC Count", "million/uL", 2, (1.0, 8.5),
     [V(m=R(4.5, 5.5), f=R(3.8, 4.8)), V(m=R(4.7, 6.1), f=R(4.2, 5.4))], si=("10^12/L", 1, 2), panels=("CBC",),
     aka={"generic": ["RBC Count", "Red Blood Cell Count", "Total RBC Count", "RBC"]},
     alt={"/uL": 1e-6})
_add("wbc", "WBC Count", "10^3/uL", 1, (0.1, 200.0), [V(u=R(4.0, 11.0)), V(u=R(4.0, 10.0)), V(u=R(4.5, 11.0))],
     si=("10^9/L", 1, 1), panels=("CBC",), alt=_CNT, alt_dec={"/uL": 0}, spell=("10^3/uL", "x10^3/uL", "10³/µL", "K/µL"),
     aka={"generic": ["WBC Count", "Total Leucocyte Count (TLC)", "Total WBC Count", "WBC", "White Blood Cell Count"],
          "US": ["WBC", "White Blood Cell Count"], "UK": ["White cell count", "WBC"]},
     ped={"2-5": V(u=R(5.5, 15.5)), "6-11": V(u=R(4.5, 13.5)), "12-17": V(u=R(4.5, 11.0))})
_add("platelets", "Platelet Count", "10^3/uL", 0, (1.0, 2000.0), [V(u=R(150, 450)), V(u=R(150, 410)), V(u=R(1.5 * 100, 4.5 * 100))],
     si=("10^9/L", 1, 0), panels=("CBC",), alt={"/uL": 0.001, "lakh/cumm": 100.0}, alt_dec={"/uL": 0, "lakh/cumm": 2},
     aka={"generic": ["Platelet Count", "Platelets", "PLT", "Platelet count"]})
_add("mcv", "MCV", "fL", 1, (40.0, 140.0), [V(u=R(80, 100)), V(u=R(83, 101))], panels=("CBC",),
     ped={"2-5": V(u=R(75, 87)), "6-11": V(u=R(77, 95)), "12-17": V(u=R(78, 98))})
_add("mch", "MCH", "pg", 1, (10.0, 50.0), [V(u=R(27, 32)), V(u=R(27, 33))], panels=("CBC",))
_add("mchc", "MCHC", "g/dL", 1, (20.0, 45.0), [V(u=R(32, 36)), V(u=R(31.5, 34.5))], panels=("CBC",),
     spell=("g/dL", "gm/dL", "g%"))
_add("rdw", "RDW", "%", 1, (8.0, 30.0), [V(u=R(11.5, 14.5))], panels=("CBC",), aka={"generic": ["RDW", "RDW-CV"]})
_add("mpv", "MPV", "fL", 1, (4.0, 16.0), [V(u=R(7.5, 11.5))], panels=("CBC",))
for _k, _d, _r, _p in (
    ("neutrophils_pct", "Neutrophils", R(40, 80), {"2-5": V(u=R(30, 60)), "6-11": V(u=R(40, 65)), "12-17": V(u=R(40, 75))}),
    ("lymphocytes_pct", "Lymphocytes", R(20, 40), {"2-5": V(u=R(35, 60)), "6-11": V(u=R(30, 50)), "12-17": V(u=R(20, 45))}),
    ("monocytes_pct", "Monocytes", R(2, 10), {}),
    ("eosinophils_pct", "Eosinophils", R(1, 6), {}),
    ("basophils_pct", "Basophils", R(0, 2), {}),
):
    _add(_k, _d, "%", 0, (0.0, 100.0), [V(u=_r)], panels=("CBC",), ped=_p, spell=("%",))
for _k, _d, _r in (
    ("neutrophils_abs", "Absolute Neutrophil Count", R(2.0, 7.0)),
    ("lymphocytes_abs", "Absolute Lymphocyte Count", R(1.0, 3.0)),
    ("monocytes_abs", "Absolute Monocyte Count", R(0.2, 1.0)),
    ("eosinophils_abs", "Absolute Eosinophil Count", R(0.02, 0.5)),
    ("basophils_abs", "Absolute Basophil Count", R(0.02, 0.10)),
):
    _add(_k, _d, "10^3/uL", 2, (0.0, 100.0), [V(u=_r)], si=("10^9/L", 1, 2), panels=("CBC",), alt=_CNT,
         alt_dec={"/uL": 0}, derived="wbc*pct/100")
_add("esr", "ESR", "mm/hr", 0, (0.0, 140.0), [V(m=R(0, 15), f=R(0, 20))], panels=("CBC",),
     spell=("mm/hr", "mm/1st hr", "mm/h"))

# ---------------------------------------------------------------- glucose
_GLU_SI = ("mmol/L", 1 / 18.016, 1)
_add("glucose_fasting", "Glucose, Fasting", "mg/dL", 0, (15.0, 800.0), [V(u=R(70, 100)), V(u=R(70, 110)), V(u=R(74, 106))],
     si=_GLU_SI, panels=("DIABETES", "KFT"),
     aka={"generic": ["Glucose, Fasting", "Fasting Blood Sugar (FBS)", "Fasting Glucose", "Glucose Fasting"]},
     ped={"2-5": V(u=R(60, 100)), "6-11": V(u=R(70, 100)), "12-17": V(u=R(70, 100))})
_add("glucose_pp", "Glucose, Post-prandial", "mg/dL", 0, (15.0, 800.0), [V(u=R(70, 140)), V(u=U(140))],
     si=_GLU_SI, panels=("DIABETES",), aka={"generic": ["Glucose, Post Prandial", "Post Prandial Blood Sugar (PPBS)", "Glucose PP"]})
_add("glucose_random", "Glucose, Random", "mg/dL", 0, (15.0, 800.0), [V(u=R(70, 140)), V(u=R(80, 140))],
     si=_GLU_SI, panels=("DIABETES", "KFT"), aka={"generic": ["Glucose, Random", "Random Blood Sugar (RBS)", "Random Glucose"]})
_add("glucose_unspecified", "Glucose", "mg/dL", 0, (15.0, 800.0), [V(u=R(70, 140)), V(u=R(70, 110))],
     si=_GLU_SI, panels=("DIABETES", "KFT", "ELEC"), aka={"generic": ["Glucose", "Blood Glucose", "Blood Sugar"]})
_add("hba1c", "HbA1c", "%", 1, (3.0, 18.0), [V(u=U(5.7)), V(u=R(4.0, 5.6))], si=("mmol/mol", None, 0), panels=("DIABETES",),
     aka={"generic": ["HbA1c", "Glycosylated Hemoglobin (HbA1c)", "Glycated Haemoglobin", "HbA1C"]}, qual=False)

# ---------------------------------------------------------------- renal
_add("urea", "Urea", "mg/dL", 0, (3.0, 400.0), [V(u=R(15, 40)), V(u=R(17, 43)), V(u=R(13, 43))], si=("mmol/L", 0.1665, 1),
     panels=("KFT",), aka={"generic": ["Urea", "Blood Urea", "Serum Urea"]},
     ped={"2-5": V(u=R(10, 36)), "6-11": V(u=R(10, 40)), "12-17": V(u=R(10, 40))})
_add("bun", "Blood Urea Nitrogen", "mg/dL", 1, (2.0, 200.0), [V(u=R(7, 20)), V(u=R(8, 23))], si=("mmol/L", 0.357, 1),
     panels=("KFT",), aka={"generic": ["Blood Urea Nitrogen (BUN)", "BUN"]})
_add("creatinine", "Creatinine", "mg/dL", 2, (0.1, 20.0),
     [V(m=R(0.7, 1.3), f=R(0.5, 1.1)), V(m=R(0.9, 1.3), f=R(0.6, 1.1)), V(u=R(0.6, 1.2))],
     si=("umol/L", 88.42, 0), panels=("KFT",), spell=("mg/dL",), aka={"generic": ["Creatinine", "Serum Creatinine"]},
     ped={"2-5": V(u=R(0.2, 0.5)), "6-11": V(u=R(0.3, 0.7)), "12-17": V(u=R(0.5, 1.0))})
_add("uric_acid", "Uric Acid", "mg/dL", 1, (0.5, 20.0), [V(m=R(3.5, 7.2), f=R(2.6, 6.0))], si=("umol/L", 59.48, 0), panels=("KFT",))
_add("egfr", "eGFR", "mL/min/1.73m2", 0, (2.0, 150.0), [V(u=L(90)), V(u=L(60))], panels=("KFT",), derived="ckd-epi-2021",
     spell=("mL/min/1.73m2", "mL/min/1.73m²"))

# ---------------------------------------------------------------- electrolytes, minerals
_add("sodium", "Sodium", "mmol/L", 0, (90.0, 190.0), [V(u=R(135, 145)), V(u=R(133, 146))], panels=("ELEC", "KFT"),
     spell=("mmol/L", "mEq/L"), aka={"generic": ["Sodium", "Sodium (Na+)", "Serum Sodium", "Na"]})
_add("potassium", "Potassium", "mmol/L", 1, (1.0, 10.0), [V(u=R(3.5, 5.1)), V(u=R(3.5, 5.3))], panels=("ELEC", "KFT"),
     spell=("mmol/L", "mEq/L"), aka={"generic": ["Potassium", "Potassium (K+)", "Serum Potassium", "K"]})
_add("chloride", "Chloride", "mmol/L", 0, (60.0, 140.0), [V(u=R(98, 107)), V(u=R(95, 108))], panels=("ELEC", "KFT"),
     spell=("mmol/L", "mEq/L"), aka={"generic": ["Chloride", "Chloride (Cl-)", "Serum Chloride"]})
_add("bicarbonate", "Bicarbonate", "mmol/L", 0, (2.0, 60.0), [V(u=R(22, 29))], panels=("ELEC", "KFT"),
     spell=("mmol/L", "mEq/L"), aka={"generic": ["Bicarbonate", "Total CO2", "HCO3"]},
     ped={"2-5": V(u=R(20, 28)), "6-11": V(u=R(20, 28)), "12-17": V(u=R(20, 28))})
_add("calcium", "Calcium", "mg/dL", 1, (3.0, 20.0), [V(u=R(8.5, 10.5)), V(u=R(8.6, 10.2)), V(u=R(2.20, 2.60), unit="mmol/L")],
     si=("mmol/L", 0.2495, 2), panels=("KFT", "ELEC", "VIT"), aka={"generic": ["Calcium", "Calcium, Total", "Serum Calcium"]},
     ped={"2-5": V(u=R(8.8, 10.8)), "6-11": V(u=R(8.8, 10.8)), "12-17": V(u=R(8.8, 10.8))})
_add("ionized_calcium", "Ionized Calcium", "mmol/L", 2, (0.3, 2.5), [V(u=R(1.12, 1.32))], panels=("ABG",))
_add("phosphorus", "Phosphorus", "mg/dL", 1, (0.3, 12.0), [V(u=R(2.5, 4.5)), V(u=R(0.8, 1.5), unit="mmol/L")], si=("mmol/L", 0.3229, 2),
     panels=("KFT", "ELEC"), aka={"generic": ["Phosphorus", "Inorganic Phosphorus", "Phosphate"]},
     ped={"2-5": V(u=R(4.0, 7.0)), "6-11": V(u=R(3.7, 5.6)), "12-17": V(u=R(2.9, 5.4))})
_add("magnesium", "Magnesium", "mg/dL", 1, (0.3, 9.0), [V(u=R(1.7, 2.4)), V(u=R(0.7, 1.0), unit="mmol/L")], si=("mmol/L", 0.4114, 2),
     panels=("ELEC", "KFT"))

# ---------------------------------------------------------------- liver
_BIL = ("umol/L", 17.1, 0)
_add("bilirubin_total", "Bilirubin, Total", "mg/dL", 1, (0.1, 40.0), [V(u=R(0.3, 1.2))], si=_BIL, panels=("LFT",),
     aka={"generic": ["Bilirubin, Total", "Total Bilirubin", "Serum Bilirubin (Total)"]})
_add("bilirubin_direct", "Bilirubin, Direct", "mg/dL", 1, (0.0, 30.0), [V(u=R(0.0, 0.3))], si=_BIL, panels=("LFT",),
     aka={"generic": ["Bilirubin, Direct", "Direct Bilirubin", "Conjugated Bilirubin"]})
_add("bilirubin_indirect", "Bilirubin, Indirect", "mg/dL", 1, (0.0, 30.0), [V(u=R(0.2, 0.8))], si=_BIL, panels=("LFT",),
     derived="total-direct", aka={"generic": ["Bilirubin, Indirect", "Indirect Bilirubin"]})
_add("alt", "ALT (SGPT)", "U/L", 0, (2.0, 3000.0), [V(u=R(7, 56)), V(u=U(40)), V(m=U(50), f=U(35))], panels=("LFT",),
     aka={"generic": ["ALT (SGPT)", "Alanine Aminotransferase (ALT)", "SGPT", "ALT"]},
     ped={"2-5": V(u=R(5, 30)), "6-11": V(u=R(5, 30)), "12-17": V(u=R(5, 30))})
_add("ast", "AST (SGOT)", "U/L", 0, (2.0, 3000.0), [V(u=R(10, 40))], panels=("LFT",),
     aka={"generic": ["AST (SGOT)", "Aspartate Aminotransferase (AST)", "SGOT", "AST"]},
     ped={"2-5": V(u=R(15, 50)), "6-11": V(u=R(10, 40)), "12-17": V(u=R(10, 40))})
_add("alp", "Alkaline Phosphatase", "U/L", 0, (10.0, 2000.0), [V(u=R(44, 147))], panels=("LFT",),
     aka={"generic": ["Alkaline Phosphatase (ALP)", "Alkaline Phosphatase", "ALP"]},
     ped={"2-5": V(u=R(150, 420)), "6-11": V(u=R(150, 440)), "12-17": V(u=R(60, 400))})
_add("ggt", "GGT", "U/L", 0, (2.0, 2000.0), [V(m=R(8, 61), f=R(5, 36))], panels=("LFT",),
     aka={"generic": ["Gamma GT (GGT)", "GGT", "Gamma-Glutamyl Transferase"]})
_add("total_protein", "Total Protein", "g/dL", 1, (2.0, 12.0), [V(u=R(6.0, 8.3))], si=("g/L", 10, 0), panels=("LFT",))
_add("albumin", "Albumin", "g/dL", 1, (1.0, 7.0), [V(u=R(3.5, 5.0))], si=("g/L", 10, 0), panels=("LFT",))
_add("globulin", "Globulin", "g/dL", 1, (0.5, 8.0), [V(u=R(2.0, 3.5))], si=("g/L", 10, 0), panels=("LFT",),
     derived="tp-alb")
_add("ag_ratio", "A/G Ratio", "", 2, (0.2, 4.0), [V(u=R(1.0, 2.1))], panels=("LFT",), derived="alb/glob",
     aka={"generic": ["A/G Ratio", "Albumin/Globulin Ratio"]}, spell=("",))

# ---------------------------------------------------------------- lipids
_CHOL = ("mmol/L", 0.02586, 2)
_add("cholesterol", "Cholesterol, Total", "mg/dL", 0, (40.0, 600.0), [V(u=U(200))], si=_CHOL, panels=("LIPID",),
     aka={"generic": ["Cholesterol, Total", "Total Cholesterol", "Cholesterol"]})
_add("triglycerides", "Triglycerides", "mg/dL", 0, (20.0, 1000.0), [V(u=U(150))], si=("mmol/L", 0.01129, 2), panels=("LIPID",))
_add("hdl", "HDL Cholesterol", "mg/dL", 0, (10.0, 120.0), [V(m=L(40), f=L(50))], si=_CHOL, panels=("LIPID",),
     aka={"generic": ["HDL Cholesterol", "HDL", "HDL-C"]})
_add("ldl", "LDL Cholesterol", "mg/dL", 0, (10.0, 400.0), [V(u=U(100))], si=_CHOL, panels=("LIPID",), derived="friedewald",
     aka={"generic": ["LDL Cholesterol", "LDL", "LDL-C", "LDL Cholesterol (Calculated)"]})
_add("vldl", "VLDL Cholesterol", "mg/dL", 0, (1.0, 200.0), [V(u=R(5, 40))], si=_CHOL, panels=("LIPID",), derived="tg/5")
_add("non_hdl", "Non-HDL Cholesterol", "mg/dL", 0, (20.0, 500.0), [V(u=U(130))], si=_CHOL, panels=("LIPID",),
     derived="tc-hdl")
_add("chol_hdl_ratio", "Chol/HDL Ratio", "", 1, (1.0, 15.0), [V(u=R(3.5, 5.0))], panels=("LIPID",), derived="tc/hdl",
     spell=("",))
_add("ldl_hdl_ratio", "LDL/HDL Ratio", "", 1, (0.5, 10.0), [V(u=R(1.5, 3.5))], panels=("LIPID",), derived="ldl/hdl",
     spell=("",))

# ---------------------------------------------------------------- thyroid, iron, vitamins, inflammation
_add("tsh", "TSH", "uIU/mL", 2, (0.005, 150.0), [V(u=R(0.4, 4.0)), V(u=R(0.35, 4.94)), V(u=R(0.27, 4.2))],
     panels=("THYROID",), qual=True, spell=("uIU/mL", "µIU/mL", "mIU/L"), aka={"generic": ["TSH", "Thyroid Stimulating Hormone (TSH)", "TSH, Ultrasensitive"]},
     ped={"2-5": V(u=R(0.7, 5.9)), "6-11": V(u=R(0.6, 4.8)), "12-17": V(u=R(0.5, 4.3))})
_add("t3", "Total T3", "ng/dL", 0, (10.0, 800.0), [V(u=R(80, 200))], si=("nmol/L", 0.01536, 2), panels=("THYROID",))
_add("t4", "Total T4", "ug/dL", 1, (0.5, 30.0), [V(u=R(5.0, 12.0))], si=("nmol/L", 12.87, 0), panels=("THYROID",))
_add("free_t3", "Free T3", "pg/mL", 1, (0.3, 20.0), [V(u=R(2.3, 4.2))], si=("pmol/L", 1.536, 1), panels=("THYROID",))
_add("free_t4", "Free T4", "ng/dL", 2, (0.1, 8.0), [V(u=R(0.8, 1.8))], si=("pmol/L", 12.87, 1), panels=("THYROID",),
     ped={"2-5": V(u=R(0.9, 1.7)), "6-11": V(u=R(0.9, 1.7)), "12-17": V(u=R(0.9, 1.7))})
_add("iron", "Iron", "ug/dL", 0, (5.0, 500.0), [V(u=R(60, 170))], si=("umol/L", 0.1791, 1), panels=("IRON",),
     spell=("ug/dL", "µg/dL"))
_add("tibc", "TIBC", "ug/dL", 0, (100.0, 700.0), [V(u=R(250, 450))], panels=("IRON",), spell=("ug/dL", "µg/dL"))
_add("transferrin_saturation", "Transferrin Saturation", "%", 0, (1.0, 100.0), [V(u=R(20, 50))], panels=("IRON",),
     derived="iron/tibc")
_add("ferritin", "Ferritin", "ng/mL", 0, (1.0, 5000.0), [V(m=R(24, 336), f=R(11, 307))], panels=("IRON",), qual=True,
     spell=("ng/mL", "µg/L"))
_add("vitamin_b12", "Vitamin B12", "pg/mL", 0, (50.0, 3000.0), [V(u=R(200, 900))], si=("pmol/L", 0.7378, 0),
     panels=("VIT",), qual=True)
_add("folate", "Folate", "ng/mL", 1, (0.5, 40.0), [V(u=R(2.7, 17.0))], si=("nmol/L", 2.266, 1), panels=("VIT",))
_add("vitamin_d", "Vitamin D, 25-Hydroxy", "ng/mL", 1, (2.0, 150.0), [V(u=R(30, 100))], si=("nmol/L", 2.496, 0), panels=("VIT",),
     aka={"generic": ["Vitamin D, 25-Hydroxy", "25-OH Vitamin D", "Vitamin D (25-OH)"]})
_add("crp", "C-Reactive Protein (CRP)", "mg/L", 1, (0.1, 400.0), [V(u=R(0, 6)), V(u=U(5))], panels=("MISC",), qual=True,
     aka={"generic": ["C-Reactive Protein (CRP)", "CRP", "C Reactive Protein"]})
_add("hs_crp", "hs-CRP", "mg/L", 2, (0.05, 100.0), [V(u=U(3.0))], panels=("CARDIAC",), qual=True)

# ---------------------------------------------------------------- coagulation, cardiac, misc
_add("pt", "Prothrombin Time (PT)", "s", 1, (5.0, 120.0), [V(u=R(11.0, 13.5))], panels=("COAG",), spell=("s", "sec", "seconds"))
_add("inr", "INR", "", 2, (0.5, 12.0), [V(u=R(0.8, 1.2))], panels=("COAG",), spell=("",))
_add("aptt", "aPTT", "s", 1, (10.0, 150.0), [V(u=R(25, 35))], panels=("COAG",), spell=("s", "sec", "seconds"),
     aka={"generic": ["aPTT", "APTT", "Activated Partial Thromboplastin Time"]})
_add("hs_troponin_t", "hs-Troponin T", "ng/L", 0, (1.0, 5000.0), [V(u=U(14))], panels=("CARDIAC",), spell=("ng/L", "pg/mL"))
_add("troponin_i", "Troponin I", "ng/mL", 2, (0.001, 50.0), [V(u=U(0.04))], panels=("CARDIAC",), qual=True,
     spell=("ng/mL", "µg/L"))
_add("ck", "Creatine Kinase (CK)", "U/L", 0, (5.0, 10000.0), [V(u=R(30, 200))], panels=("CARDIAC",))
_add("ck_mb", "CK-MB", "U/L", 0, (1.0, 1000.0), [V(u=U(25))], panels=("CARDIAC",))
_add("ldh", "LDH", "U/L", 0, (20.0, 3000.0), [V(u=R(140, 280))], panels=("MISC",))
_add("amylase", "Amylase", "U/L", 0, (5.0, 3000.0), [V(u=R(28, 100))], panels=("MISC",))
_add("lipase", "Lipase", "U/L", 0, (2.0, 3000.0), [V(u=R(13, 60))], panels=("MISC",))
_add("psa", "PSA, Total", "ng/mL", 2, (0.01, 100.0), [V(m=U(4.0))], panels=("MISC",), qual=True, sex_only="M")
_add("d_dimer", "D-Dimer", "ug/mL", 2, (0.05, 20.0), [V(u=U(0.5))], panels=("COAG",), qual=True,
     spell=("ug/mL FEU", "µg/mL FEU"))
_add("procalcitonin", "Procalcitonin", "ng/mL", 2, (0.01, 100.0), [V(u=U(0.1))], panels=("MISC",), qual=True)

# ---------------------------------------------------------------- ABG
_add("blood_ph", "pH", "", 2, (6.8, 7.9), [V(u=R(7.35, 7.45))], panels=("ABG",), spell=("",), aka={"generic": ["pH", "Blood pH", "Arterial pH"]})
_add("pco2", "pCO2", "mmHg", 0, (5.0, 120.0), [V(u=R(35, 45))], panels=("ABG",))
_add("po2", "pO2", "mmHg", 0, (15.0, 600.0), [V(u=R(80, 100))], panels=("ABG",))
_add("base_excess", "Base Excess", "mmol/L", 1, (-30.0, 30.0), [V(u=R(-2, 2))], panels=("ABG",))

# ---------------------------------------------------------------- urine
_add("specific_gravity", "Specific Gravity", "", 3, (1.000, 1.060), [V(u=R(1.005, 1.030))], panels=("URINE",), specimen="urine", spell=("",))
_add("urine_ph", "pH", "", 1, (4.0, 9.0), [V(u=R(4.6, 8.0))], panels=("URINE",), specimen="urine", spell=("",))
_add("urine_pus_cells", "Pus Cells", "/hpf", 0, None, [V(u=R(0, 5))], panels=("URINE",), specimen="urine", value_type="range_result",
     aka={"generic": ["Pus Cells", "WBC", "Leukocytes"]})
_add("urine_rbc", "RBC", "/hpf", 0, None, [V(u=R(0, 2))], panels=("URINE",), specimen="urine", value_type="range_result",
     aka={"generic": ["RBC", "Red Blood Cells"]})
_add("urine_epithelial", "Epithelial Cells", "/hpf", 0, None, [V(u=R(0, 5))], panels=("URINE",), specimen="urine", value_type="range_result")
for _k, _d in (
    ("urine_protein", "Protein"), ("urine_glucose", "Glucose"), ("urine_ketones", "Ketones"), ("urine_blood", "Blood"),
    ("urine_bilirubin", "Bilirubin"), ("urine_nitrite", "Nitrite"), ("urine_leukocyte_esterase", "Leukocyte Esterase"),
    ("urine_casts", "Casts"), ("urine_bacteria", "Bacteria"),
):
    _add(_k, _d, "", 0, None, [], panels=("URINE",), specimen="urine", value_type="qualitative",
         normal_texts=("Nil", "Negative", "Absent"), spell=("",))
_add("urine_urobilinogen", "Urobilinogen", "mg/dL", 1, None, [V(u=R(0.2, 1.0))], panels=("URINE",), specimen="urine",
     value_type="qualitative", normal_texts=("Normal",))
for _k, _d in (("urine_colour", "Colour"), ("urine_appearance", "Appearance"), ("urine_crystals", "Crystals")):
    _add(_k, _d, "", 0, None, [], panels=("URINE",), specimen="urine", value_type="info", spell=("",))
_add("urine_sodium", "Sodium, Urine", "mmol/L", 0, (1.0, 400.0), [V(u=R(40, 220))], panels=("URINE_SPOT",), specimen="urine")
_add("urine_potassium", "Potassium, Urine", "mmol/L", 0, (1.0, 250.0), [V(u=R(25, 125))], panels=("URINE_SPOT",), specimen="urine")
_add("urine_creatinine", "Creatinine, Urine", "mg/dL", 0, (5.0, 500.0), [V(m=R(39, 259), f=R(28, 217))],
     panels=("URINE_SPOT",), specimen="urine")
_add("microalbumin", "Microalbumin, Urine", "mg/L", 1, (0.5, 3000.0), [V(u=U(20))], panels=("URINE_SPOT",), specimen="urine")
_add("microalbumin_creatinine", "Microalbumin/Creatinine Ratio", "mg/g", 1, (0.5, 5000.0), [V(u=U(30))],
     panels=("URINE_SPOT",), specimen="urine")

# ---------------------------------------------------------------- CSF
_add("csf_glucose", "Glucose, CSF", "mg/dL", 0, (5.0, 400.0), [V(u=R(40, 70))], panels=("CSF",), specimen="csf")
_add("csf_protein", "Protein, CSF", "mg/dL", 0, (5.0, 1500.0), [V(u=R(15, 45))], panels=("CSF",), specimen="csf")
_add("csf_wbc", "Total Cell Count, CSF", "cells/uL", 0, (0.0, 20000.0), [V(u=R(0, 5))], panels=("CSF",), specimen="csf")
_add("csf_chloride", "Chloride, CSF", "mmol/L", 0, (80.0, 160.0), [V(u=R(120, 130))], panels=("CSF",), specimen="csf")
for _k, _d, _t in (("csf_neutrophils_pct", "Neutrophils, CSF", "info"), ("csf_lymphocytes_pct", "Lymphocytes, CSF", "info"),
                   ("csf_appearance", "Appearance, CSF", "info")):
    _add(_k, _d, "%" if _k.endswith("pct") else "", 0, None, [], panels=("CSF",), specimen="csf", value_type=_t)

# ---------------------------------------------------------------- serology and other qualitative
for _k, _d in (("hiv_screen", "HIV I & II Antibody"), ("hbsag", "HBsAg"), ("anti_hcv", "Anti-HCV"), ("vdrl", "VDRL"),
               ("dengue_ns1", "Dengue NS1 Antigen"), ("dengue_igm", "Dengue IgM")):
    _add(_k, _d, "", 0, None, [], panels=("SEROLOGY",), value_type="qualitative",
         normal_texts=("Non-Reactive", "Negative"), spell=("",))
for _k, _d in (("widal_o", "Widal S. Typhi O"), ("widal_h", "Widal S. Typhi H"), ("widal_ah", "Widal S. Paratyphi AH"),
               ("widal_bh", "Widal S. Paratyphi BH")):
    _add(_k, _d, "", 0, None, [], panels=("SEROLOGY",), value_type="titre", spell=("",))
_add("ra_factor", "Rheumatoid Factor", "IU/mL", 0, (1.0, 2000.0), [V(u=U(14))], panels=("SEROLOGY",))
_add("aso", "ASO Titre", "IU/mL", 0, (10.0, 2000.0), [V(u=U(200))], panels=("SEROLOGY",))
_add("blood_group_abo", "Blood Group (ABO)", "", 0, None, [], panels=("SEROLOGY",), value_type="info", spell=("",))
_add("blood_group_rh", "Rh Type", "", 0, None, [], panels=("SEROLOGY",), value_type="info", spell=("",))

# panel membership view
PANELS: dict[str, tuple[str, ...]] = {}
for _a in CATALOGUE.values():
    for _p in _a.panels:
        PANELS[_p] = PANELS.get(_p, ()) + (_a.key,)

ABG_KEYS = ("blood_ph", "pco2", "po2", "bicarbonate", "base_excess")

# analytes that print as counts (unit may be missing on the page -> AMB_UNITLESS_COUNT)
COUNT_KEYS = frozenset({"wbc", "platelets", "rbc", "neutrophils_abs", "lymphocytes_abs", "monocytes_abs",
                        "eosinophils_abs", "basophils_abs"})


def paediatric_band(age: int) -> str:
    """Table P band for an age in years (2-17)."""
    if age < 2 or age > 17:
        raise ValueError(f"no paediatric band for age {age}")
    return "2-5" if age <= 5 else "6-11" if age <= 11 else "12-17"
