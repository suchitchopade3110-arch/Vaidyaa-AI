"""Template-based report families (P1 plan section 3.2): IN-A, IN-B, IN-C, US-A (T05); IN-D, IN-E, UK-A, GEN-CSV (T06).

Layout STRUCTURE only is modelled on common report styles; every name, address, phone and logo is fictional
(section 3.9). GT-side module: imports nothing from ``app``. Pure and deterministic.

Extension points: a family is a ``FamilySpec`` (columns, header style, panels) registered in ``SPECS``; the generation
engine (``build_report``) and ``base`` kit are shared. T06 additions are switched on by spec flags (``cumulative``,
``two_panel``, ``unit_place``, ``csv``) and by per-report ``Lay`` fields drawn from a SEPARATE rng stream, so the
T05 families stay byte-identical. GEN-CSV delegates to ``csv_export``.
"""

from __future__ import annotations

import datetime
import random
import re
from dataclasses import dataclass, replace
from decimal import Decimal

from tools.eval import clinical
from tools.eval import config as cfg
from tools.eval import scenarios as sc
from tools.eval import units
from tools.eval.catalogue import CATALOGUE, COUNT_KEYS, Analyte, RefVariant, Rng, paediatric_band
from tools.eval.clinical import OptionRange, PrintedRange
from tools.eval.layouts import base
from tools.eval.layouts.base import (
    A4, FONT_BOLD, FONT_ITALIC, FONT_REGULAR, FONT_SERIF, FONT_SERIF_BOLD, LETTER, Cell, Column, DocBuilder,
    Identity, PrintedReport, PrintedRow, simple_cell,
)
from tools.eval.scenarios import Patient

MAX_ATTEMPTS = 80
A4_LANDSCAPE = (A4[1], A4[0])

# keys computed from other printed values (never scenario targets)
DERIVED = frozenset({
    "hematocrit", "mcv", "mch", "mchc", "bilirubin_indirect", "globulin", "ag_ratio", "vldl", "ldl", "non_hdl",
    "chol_hdl_ratio", "ldl_hdl_ratio", "neutrophils_abs", "lymphocytes_abs", "egfr",
})

# ---------------------------------------------------------------- methods (own column / technology column)
_METHODS: dict[str, tuple[str, str]] = {}


def _m(keys: str, method: str, tech: str) -> None:
    for k in keys.split():
        _METHODS[k] = (method, tech)


_m("hemoglobin", "Photometry", "SLS-Hb")
_m("rbc wbc platelets neutrophils_pct lymphocytes_pct monocytes_pct eosinophils_pct basophils_pct neutrophils_abs "
   "lymphocytes_abs", "Impedance", "Flow Cytometry")
_m("hematocrit mcv mch mchc rdw mpv", "Calculated", "Calculated")
_m("esr", "Westergren", "Westergren")
_m("glucose_fasting glucose_pp glucose_random glucose_unspecified", "GOD-POD", "Hexokinase")
_m("hba1c", "HPLC", "HPLC")
_m("urea bun", "Urease-GLDH", "Urease UV")
_m("creatinine", "Jaffe", "Enzymatic")
_m("uric_acid", "Uricase", "Uricase POD")
_m("egfr", "Calculated", "CKD-EPI")
_m("sodium potassium chloride bicarbonate", "ISE", "ISE Indirect")
_m("calcium", "Arsenazo", "Arsenazo III")
_m("phosphorus", "Molybdate", "Molybdate UV")
_m("magnesium", "Xylidyl", "Xylidyl Blue")
_m("bilirubin_total bilirubin_direct bilirubin_indirect", "Diazo", "Diazo")
_m("alt ast alp ggt ldh ck ck_mb amylase lipase", "Enzymatic", "IFCC")
_m("total_protein globulin", "Biuret", "Biuret")
_m("albumin ag_ratio", "BCG", "BCG")
_m("cholesterol triglycerides hdl ldl vldl non_hdl chol_hdl_ratio ldl_hdl_ratio", "Enzymatic", "CHOD-POD")
_m("t3 t4 tsh free_t3 free_t4 ferritin vitamin_b12 folate vitamin_d psa procalcitonin troponin_i", "CLIA", "ECLIA")
_m("iron tibc transferrin_saturation", "Ferrozine", "Ferene")
_m("crp", "Turbidimetry", "Immunoturbidimetry")
_m("pt inr aptt", "Clot-based", "Clotting")


def method_for(key: str, which: int) -> str:
    return _METHODS.get(key, ("Photometry", "Photometry"))[which]


# ---------------------------------------------------------------- panels

CBC = ("hemoglobin", "rbc", "hematocrit", "mcv", "mch", "mchc", "rdw", "wbc", "neutrophils_pct", "lymphocytes_pct",
       "monocytes_pct", "eosinophils_pct", "basophils_pct", "platelets", "mpv", "esr")
CBC_US = ("wbc", "rbc", "hemoglobin", "hematocrit", "mcv", "mch", "mchc", "rdw", "platelets", "neutrophils_pct",
          "lymphocytes_pct", "monocytes_pct", "eosinophils_pct", "basophils_pct", "neutrophils_abs",
          "lymphocytes_abs")
KFT = ("urea", "creatinine", "uric_acid", "egfr", "sodium", "potassium", "chloride", "bicarbonate", "calcium",
       "phosphorus", "magnesium")
KFT_US = ("glucose_unspecified", "bun", "creatinine", "egfr", "sodium", "potassium", "chloride", "bicarbonate",
          "calcium")
LFT = ("bilirubin_total", "bilirubin_direct", "bilirubin_indirect", "ast", "alt", "alp", "ggt", "total_protein",
       "albumin", "globulin", "ag_ratio")
LIPID = ("cholesterol", "triglycerides", "hdl", "ldl", "vldl", "non_hdl", "chol_hdl_ratio", "ldl_hdl_ratio")
DIAB = ("glucose_fasting", "glucose_pp", "hba1c")
THY = ("t3", "t4", "tsh", "free_t4")
IRON = ("iron", "tibc", "transferrin_saturation", "ferritin")
VIT = ("vitamin_b12", "folate", "vitamin_d")
MISC = ("crp", "ldh", "amylase", "lipase", "psa", "procalcitonin")
COAG = ("pt", "inr", "aptt")
CARD = ("ck", "ck_mb", "troponin_i")

PANELS_IN = (
    ("COMPLETE BLOOD COUNT (CBC)", CBC), ("GLUCOSE AND HbA1c", DIAB), ("LIPID PROFILE", LIPID),
    ("LIVER FUNCTION TEST (LFT)", LFT), ("KIDNEY FUNCTION TEST (KFT) AND ELECTROLYTES", KFT),
    ("THYROID PROFILE", THY), ("IRON STUDIES", IRON), ("VITAMINS", VIT), ("COAGULATION PROFILE", COAG),
    ("CARDIAC MARKERS", CARD), ("SPECIAL CHEMISTRY", MISC),
)
PANELS_US = (
    ("CBC With Differential", CBC_US), ("Comprehensive Metabolic Panel", KFT_US), ("Hepatic Function Panel", LFT),
    ("Lipid Panel", LIPID), ("Hemoglobin A1c", ("hba1c",)), ("Thyroid Panel", THY), ("Iron and TIBC", IRON),
    ("Vitamins", VIT), ("Prothrombin Time", COAG), ("Cardiac Markers", CARD), ("Special Chemistry", MISC),
)

# T06 panel sets
KFT_E = ("urea", "creatinine", "uric_acid", "sodium", "potassium", "chloride", "bicarbonate", "calcium", "phosphorus",
         "magnesium")  # no eGFR: its long unit would not fit a half-width panel
PANELS_E = (  # consecutive pairs are printed side by side: (left, right) per block
    ("HAEMATOLOGY", CBC), ("BIOCHEMISTRY", DIAB + KFT_E),
    ("LIPID PROFILE", LIPID), ("LIVER FUNCTION TEST (LFT)", LFT),
    ("THYROID AND IRON STUDIES", THY + IRON), ("COAGULATION AND CARDIAC", COAG + CARD),
    ("SPECIAL CHEMISTRY", MISC), ("VITAMINS", VIT),
)
PANELS_UK = (
    ("FULL BLOOD COUNT", CBC), ("GLUCOSE AND HbA1c", DIAB), ("UREA AND ELECTROLYTES", KFT),
    ("LIVER FUNCTION TESTS", LFT), ("LIPID PROFILE", LIPID), ("THYROID FUNCTION TESTS", THY),
    ("IRON STUDIES", IRON), ("VITAMINS", VIT), ("COAGULATION", COAG), ("CARDIAC MARKERS", CARD),
    ("CLINICAL CHEMISTRY", MISC),
)

# IN-C interpretive categories (printed after the panel; every line is a GT decoy of kind 'interpretive')
INTERPRETIVE: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "hba1c": ("HbA1c interpretation guide", (("Normal", "< 5.7 %"), ("Prediabetes", "5.7 - 6.4 %"),
                                             ("Diabetes", ">= 6.5 %"))),
    "vitamin_d": ("Vitamin D status guide", (("Deficient", "< 20 ng/mL"), ("Insufficient", "20 - 29 ng/mL"),
                                             ("Sufficient", "30 - 100 ng/mL"))),
    "cholesterol": ("Total cholesterol", (("Desirable", "< 200 mg/dL"), ("Borderline high", "200 - 239 mg/dL"),
                                          ("High", ">= 240 mg/dL"))),
    "triglycerides": ("Triglycerides", (("Normal", "< 150 mg/dL"), ("Borderline high", "150 - 199 mg/dL"),
                                        ("High", "200 - 499 mg/dL"), ("Very high", ">= 500 mg/dL"))),
    "ldl": ("LDL cholesterol", (("Optimal", "< 100 mg/dL"), ("Near optimal", "100 - 129 mg/dL"),
                                ("Borderline high", "130 - 159 mg/dL"), ("High", "160 - 189 mg/dL"),
                                ("Very high", ">= 190 mg/dL"))),
    "hdl": ("HDL cholesterol", (("Low", "< 40 mg/dL"), ("High (protective)", ">= 60 mg/dL"))),
    "egfr": ("CKD stage by eGFR (mL/min/1.73m2)", (("G1", ">= 90"), ("G2", "60 - 89"), ("G3a", "45 - 59"),
                                                   ("G3b", "30 - 44"), ("G4", "15 - 29"), ("G5", "< 15"))),
}


# ---------------------------------------------------------------- family specs

@dataclass(frozen=True)
class FamilySpec:
    fid: str
    locale: str  # catalogue name locale: IN | US
    page: tuple[float, float]
    font: str
    bold: str
    size: float
    variant: int  # printed reference-variant index: a lab stays consistent
    header_style: str  # 'in_a' | 'in_b' | 'in_c' | 'us_a'
    date_style: str  # 'in' | 'us'
    grouping: str  # 'indian' | 'western'
    columns: tuple[Column, ...]
    panels: tuple[tuple[str, tuple[str, ...]], ...]
    two_sided: tuple[str, ...]  # range syntaxes drawn from per report
    one_sided: tuple[str, ...]  # 'sym' ('< b') or 'word' ('Up to b')
    sex_labelled: bool
    age_label_p: float
    si_p: float = 0.0
    cumm_p: float = 0.0
    method_mode: str | None = None  # 'column' | 'technology' | 'under'
    flag_mode: str | None = None  # 'glued' | 'column'
    interpretive: bool = False
    one_sided_unit: bool = False
    range_size_delta: float = 0.0
    sex_word: tuple[str, str] = ("M", "F")
    abs_counts: bool = False
    template_id: str | None = None
    cumulative: bool = False  # IN-D: dated result columns, oldest to newest
    two_panel: bool = False  # IN-E: two half-width panels per visual line, CBC unit in the panel header
    unit_place: str = "cell"  # GT unit_placement for printed units: 'cell' | 'inline'
    csv: bool = False  # GEN-CSV: exported by ``csv_export``

    def col(self, key: str) -> Column:
        return next(c for c in self.columns if c.key == key)


SPECS: dict[str, FamilySpec] = {
    "IN-A": FamilySpec(
        "IN-A", "IN", A4, FONT_REGULAR, FONT_BOLD, 8.5, 0, "in_a", "in", "indian",
        (Column("name", "Test", 40), Column("result", "Result", 305), Column("unit", "Unit", 372),
         Column("range", "Bio. Ref. Interval", 438), Column("method", "Method", 520)),
        PANELS_IN, ("dash", "dash", "endash", "to"), ("sym", "sym", "word"), True, 0.10, cumm_p=0.35,
        method_mode="column", range_size_delta=-1.0, template_id="IN-A",
    ),
    "IN-B": FamilySpec(
        "IN-B", "IN", A4, FONT_REGULAR, FONT_BOLD, 8.5, 1, "in_b", "in", "indian",
        (Column("name", "Test Name", 40), Column("tech", "Technology", 205), Column("result", "Result", 292),
         Column("unit", "Unit", 352), Column("range", "Bio. Ref. Interval", 418)),
        PANELS_IN, ("dash", "bracket", "paren"), ("sym",), False, 0.0, si_p=0.20, cumm_p=0.30,
        method_mode="technology", flag_mode="glued", one_sided_unit=True, template_id="IN-B",
    ),
    "IN-C": FamilySpec(
        "IN-C", "IN", A4, FONT_SERIF, FONT_SERIF_BOLD, 9.5, 0, "in_c", "in", "indian",
        (Column("name", "Investigation", 40), Column("result", "Result", 300), Column("unit", "Units", 372),
         Column("range", "Reference Value", 448)),
        PANELS_IN, ("dash", "paren", "to"), ("sym", "word"), True, 0.0, cumm_p=0.20,
        method_mode="under", interpretive=True, range_size_delta=-0.5, template_id="IN-C",
    ),
    "US-A": FamilySpec(
        "US-A", "US", LETTER, FONT_REGULAR, FONT_BOLD, 8.5, 2, "us_a", "us", "western",
        (Column("name", "Test", 36), Column("result", "Current Result", 250, "r"), Column("flag", "Flag", 268),
         Column("range", "Reference Interval", 322), Column("unit", "Units", 440), Column("lab", "Lab", 520)),
        PANELS_US, ("tight", "tight", "dash"), ("sym",), True, 0.0, flag_mode="column", abs_counts=True,
        sex_word=("Male", "Female"), template_id="US-A",
    ),
    "IN-D": FamilySpec(
        "IN-D", "IN", A4_LANDSCAPE, FONT_REGULAR, FONT_BOLD, 8.5, 1, "in_d", "in", "indian",
        (Column("name", "Test", 40), Column("d1", "", 255), Column("d2", "", 335), Column("d3", "", 415),
         Column("unit", "Unit", 500), Column("range", "Bio. Ref. Interval", 570)),
        PANELS_IN, ("dash", "dash", "endash", "to"), ("sym", "sym", "word"), True, 0.0, cumm_p=0.30,
        cumulative=True, template_id="IN-D",
    ),
    "IN-E": FamilySpec(
        "IN-E", "IN", A4, FONT_REGULAR, FONT_BOLD, 8.0, 0, "in_e", "in", "indian",
        (Column("name", "Test", 36), Column("result", "Result", 128), Column("unit", "Unit", 172),
         Column("range", "Ref. Range", 218)),
        PANELS_E, ("dash", "paren", "tight"), ("sym",), True, 0.0, si_p=0.30, two_panel=True, template_id="IN-E",
    ),
    "UK-A": FamilySpec(
        "UK-A", "UK", A4, FONT_REGULAR, FONT_BOLD, 8.5, 1, "uk_a", "in", "western",
        (Column("name", "Test", 40), Column("result", "Result", 270), Column("range", "Normal range", 390)),
        PANELS_UK, ("paren_sp", "bracket", "dash"), ("sym",), False, 0.0, si_p=1.0, unit_place="inline",
        template_id="UK-A",
    ),
    "GEN-CSV": FamilySpec(
        "GEN-CSV", "GEN", A4, "Courier", "Courier-Bold", 10.0, 0, "csv", "in", "none",
        (Column("name", "Test", 0),), PANELS_IN, ("dash",), ("sym",), False, 0.0, si_p=0.30, flag_mode="column",
        csv=True, template_id="GEN-CSV",
    ),
}


# ---------------------------------------------------------------- number / range / unit formatting

_UNIT_PRETTY = (("10^3/uL", "10³/µL"), ("/uL", "/µL"), ("uIU", "µIU"), ("umol", "µmol"),
                ("ug/", "µg/"), ("1.73m2", "1.73m²"))


def pretty_unit(u: str) -> str:
    for a, b in _UNIT_PRETTY:
        u = u.replace(a, b)
    return u


def group_digits(s: str, grouping: str) -> str:
    """Indian (2,50,000) or western (250,000) grouping of the integer part of a plain decimal string."""
    if grouping == "none":
        return s
    neg = s.startswith("-")
    body = s[1:] if neg else s
    ip, dot, fp = body.partition(".")
    if len(ip) <= 3:
        return s
    if grouping == "indian":
        head, tail = ip[:-3], ip[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        out = ",".join(parts + [tail])
    else:
        parts = []
        rest = ip
        while len(rest) > 3:
            parts.insert(0, rest[-3:])
            rest = rest[:-3]
        out = ",".join([rest] + parts)
    return ("-" if neg else "") + out + dot + fp


def fmt_bound(x: float, dec: int, grouping: str | None) -> str:
    """Bound text with at least ``dec`` decimals and as many more (up to 3) as exactness needs."""
    d = dec
    s = f"{x:.{d}f}"
    while float(s) != x and d < dec + 3:
        d += 1
        s = f"{x:.{d}f}"
    return group_digits(s, grouping) if grouping else s


def _range_text(r: Rng, dec: int, syntax: str, one_sided: str, grouping: str | None, unit: str = "") -> str:
    tail = f" {unit}" if unit else ""
    if r.kind == "two_sided":
        lo, hi = fmt_bound(r.low, dec, grouping), fmt_bound(r.high, dec, grouping)
        return {
            "dash": f"{lo} - {hi}", "endash": f"{lo}–{hi}", "to": f"{lo} to {hi}", "paren": f"({lo}-{hi})",
            "bracket": f"[{lo} - {hi}]", "tight": f"{lo}-{hi}", "paren_sp": f"({lo} - {hi})",
        }[syntax] + tail
    if r.kind == "upper":
        b = fmt_bound(r.high, dec, grouping)
        return (f"< {b}" if one_sided == "sym" else f"Up to {b}") + tail
    return f"> {fmt_bound(r.low, dec, grouping)}" + tail


# ---------------------------------------------------------------- per-report choices and the value engine

@dataclass
class Lay:
    """Layout and unit choices fixed once per report (a lab stays consistent within a report)."""

    ident: Identity
    collected: str
    reported: str
    syntax: str
    one_sided: str
    si: bool
    cumm: bool
    size: float
    unit_spell: dict[str, int]
    name_pick: dict[str, int]
    footnotes: tuple[str, ...]
    lab_codes: dict[str, str]
    start_pad: float
    # T06 (drawn from a separate rng stream; defaults leave T05 families unchanged)
    dates: tuple[str, ...] = ()  # IN-D dated columns, oldest first; the last is the collection date
    current_label: bool = False  # IN-D: the newest column is headed "Current"
    hdr_unit_text: str = ""  # IN-E: unit printed in the haematology panel header ("" = AMB variant)
    amb_counts: bool = False  # IN-E: count rows carry no unit and no range (AMB_UNITLESS_COUNT)
    ward: str = ""  # IN-E header
    uk_style: int = 0  # UK-A: 0 unit after value, 1 unit in brackets after the test name, 2 unit in the range
    range_bracket_unit: bool = False  # bracketed unit appended to every printed range line


def _variant(a: Analyte, variant: int, age: int) -> tuple[RefVariant, bool]:
    if age < 18 and a.ped:
        return a.ped[paediatric_band(age)], True
    return a.refs[variant % len(a.refs)], False


def _convertible(key: str, unit: str) -> bool:
    try:
        units.to_canonical(key, 1.0, unit)
    except ValueError:
        return False
    return True


def _unit_raw(key: str, a: Analyte, spec: FamilySpec, lay: Lay) -> str:
    if lay.cumm and key in COUNT_KEYS and key != "rbc" and "/uL" in a.alt_units and not lay.si:
        return "/cumm"
    if lay.si and a.si_unit:
        return a.si_unit
    cands = [s for s in a.unit_spellings if _convertible(key, s)] or [a.unit]
    return cands[lay.unit_spell.get(key, 0) % len(cands)]


def _conv_rng(key: str, r: Rng, from_unit: str, to_unit: str) -> Rng:
    if units.canon_unit(from_unit) == units.canon_unit(to_unit):
        return r
    dec = units.decimals_for(key, to_unit)

    def f(b: float | None) -> float | None:
        if b is None:
            return None
        c = units.to_canonical(key, b, from_unit)[0]
        return float(sc.round_half_up(units.from_canonical(key, c, to_unit), dec))

    return Rng(r.kind, f(r.low), f(r.high))


@dataclass
class RangeBundle:
    own: Rng | None  # the patient's own range (sampling reference); None when interpretive
    printed: PrintedRange
    lines: list[str]
    bounds: list[float]  # every printed bound in the printed unit (for the 'not equal a bound' invariant)
    features: list[str]


def _make_range(key: str, a: Analyte, spec: FamilySpec, lay: Lay, pt: Patient, unit: str, rng: random.Random,
                interpretive: bool) -> RangeBundle | None:
    if a.sex_only and a.sex_only != pt.sex_code:
        return None
    var, ped = _variant(a, spec.variant, pt.age_true)
    base_unit = var.unit or a.unit
    grouping = spec.grouping
    dec = units.decimals_for(key, unit)
    bounds: list[float] = []

    def cv(r: Rng) -> Rng | None:
        out = _conv_rng(key, r, base_unit, unit)
        if out.kind == "two_sided" and not out.low < out.high:
            return None
        return out

    def add(r: Rng) -> None:
        bounds.extend(b for b in (r.low, r.high) if b is not None)

    syntax = lay.syntax
    unit_tail = pretty_unit(unit) if spec.one_sided_unit else ""
    bracket = f"[{pretty_unit(unit)}]" if lay.range_bracket_unit and unit else ""

    def text(r: Rng) -> str:
        t = _range_text(r, dec, syntax, lay.one_sided, grouping, unit_tail if r.kind != "two_sided" else "")
        return f"{t} {bracket}" if bracket else t

    if interpretive:
        cons = a.consensus
        rs = [cons.unisex] if not cons.sex_specific else [cons.male, cons.female]
        for r in rs:
            c = cv(r) if r is not None else None
            if c is not None:
                add(c)
        for v in a.refs:  # all variants' bounds are 'printed' for the clear-margin invariant
            for r in ([v.unisex] if not v.sex_specific else [v.male, v.female]):
                c = cv(r) if r is not None else None
                if c is not None:
                    add(c)
        return RangeBundle(None, PrintedRange("interpretive", text="See interpretation"),
                           ["See interpretation"], bounds, ["interpretive_range"])

    if var.sex_specific:
        own0 = var.male if pt.sex_code == "M" else var.female
        own = cv(own0) if own0 is not None else None
        if own is None:
            return None
        if spec.sex_labelled or pt.input_sex is None:
            opts = []
            lines = []
            for code, word, r0 in (("M", spec.sex_word[0], var.male), ("F", spec.sex_word[1], var.female)):
                if r0 is None:
                    continue
                c = cv(r0)
                if c is None:
                    return None
                add(c)
                opts.append(OptionRange(word, c.low, c.high, c.kind, sex=code))
                lines.append(f"{word}: {text(c)}")
            return RangeBundle(own, PrintedRange("sex", options=tuple(opts), text=" ".join(lines)), lines, bounds,
                               ["sex_labelled_range"])
        add(own)
        return RangeBundle(own, PrintedRange(own.kind, own.low, own.high, text=text(own)), [text(own)], bounds, [])

    own0 = var.unisex
    own = cv(own0)
    if own is None:
        return None
    # age-labelled form ("Adult: ...; Child: ...") only when every band and the adult variant are unisex
    adult = a.refs[spec.variant % len(a.refs)]
    if (spec.age_label_p and a.ped and not adult.sex_specific and all(not v.sex_specific for v in a.ped.values())
            and rng.random() < spec.age_label_p and (adult.unit or a.unit) == base_unit):
        band = paediatric_band(pt.age_true) if pt.age_true < 18 else "6-11"
        child = cv(a.ped[band].unisex)
        adult_c = cv(adult.unisex)
        if child is not None and adult_c is not None:
            add(child)
            add(adult_c)
            opts = (OptionRange("Adult", adult_c.low, adult_c.high, adult_c.kind, age_min=18),
                    OptionRange("Child", child.low, child.high, child.kind, age_min=2, age_max=17))
            lines = [f"Adult: {text(adult_c)}", f"Child: {text(child)}"]
            return RangeBundle(own, PrintedRange("age", options=opts, text=" ".join(lines)), lines, bounds,
                               ["age_labelled_range"])
    add(own)
    return RangeBundle(own, PrintedRange(own.kind, own.low, own.high, text=text(own)), [text(own)], bounds, [])


def _near_bound(v: float, bounds: list[float], dec: int) -> bool:
    step = 10.0 ** (-dec)
    return any(abs(v - b) < step - 1e-9 for b in bounds)


def _row_name(spec: FamilySpec, lay: Lay, a: Analyte, key: str, width: float) -> str:
    names = list(a.names.get(spec.locale) or a.names["generic"])
    fitting = [n for n in names if base.text_width(n, spec.font, spec.size) <= width] or [min(names, key=len)]
    return fitting[lay.name_pick.get(key, 0) % len(fitting)]


class _Gen:
    """Draws one report's rows (all panels), deterministically from the passed RNG."""

    def __init__(self, spec: FamilySpec, lay: Lay, pt: Patient, cls: str, rng: random.Random,
                 plan: sc.ScenarioSpec) -> None:
        self.spec, self.lay, self.pt, self.cls, self.rng = spec, lay, pt, cls, rng
        self.targets = {t.key: t for t in plan.targets}
        self.vals: dict[str, float] = {}  # printed-then-canonical values by key
        self.rows: dict[str, PrintedRow] = {}
        self.notes: dict[str, str] = {}

    # -- eligibility
    def eligible(self, key: str) -> bool:
        a = CATALOGUE[key]
        pt = self.pt
        if a.sex_only and a.sex_only != pt.sex_code:
            return False
        if pt.is_paediatric and not set(a.panels) & set(cfg.PAEDIATRIC_PANELS):
            return False
        if key == "egfr" and (pt.input_age is None or pt.input_sex is None):
            return False
        return True

    def candidates(self) -> list[str]:
        return [k for _, keys in self.spec.panels for k in keys if k not in DERIVED and self.eligible(k)]

    # -- row construction
    def _finish_row(self, key: str, title: str, unit: str, dp: Decimal, rb: RangeBundle, state: str,
                    qualifier: str | None = None) -> PrintedRow:
        spec, a = self.spec, CATALOGUE[key]
        dec = units.decimals_for(key, unit)
        raw = format(dp.quantize(Decimal(1).scaleb(-dec)), "f")
        vtxt = group_digits(raw, spec.grouping)
        feats = list(rb.features)
        if vtxt != raw:
            feats.append(f"{spec.grouping}_grouping")
        if self.lay.si and a.si_unit and units.canon_unit(unit) == units.canon_unit(a.si_unit):
            feats.append("si")
        if qualifier:
            feats.append("qualifier")
        if self.pt.is_paediatric:
            feats.append("paediatric")
        which = {"column": 0, "technology": 1, "under": 0}.get(spec.method_mode or "", None)
        method = method_for(key, which) if which is not None else ""
        if spec.method_mode == "column":
            feats.append("method_column")
        elif spec.method_mode == "technology":
            feats.append("technology_column")
        elif spec.method_mode == "under":
            feats.append("method_under_name")
        if spec.flag_mode == "glued":
            feats.append("glued_flag")
        if spec.flag_mode == "column":
            feats.append("flag_column")
        if spec.interpretive and key in INTERPRETIVE:
            feats.append("interpretive_table")
        uk_unit = pretty_unit(unit) if spec.fid == "UK-A" and self.lay.uk_style == 1 and unit else ""
        row = PrintedRow(
            key=key, name=_row_name(spec, self.lay, a, key, self._name_width(uk_unit)),
            section=title, value=float(vtxt.replace(",", "")), value_text=vtxt, qualifier=qualifier,
            unit=pretty_unit(unit) if unit else "", unit_placement="cell" if unit else "none",
            unit_true=pretty_unit(unit) if unit else "", printed_range=rb.printed, range_lines=list(rb.lines),
            method=method, state=state, features=feats,
        )
        self._family_hooks(row, key, unit, dp, dec)
        t = base.row_truth(row, self.pt)
        row.printed_flag = self._printed_flag(t.flag_true)
        return row

    def _family_hooks(self, row: PrintedRow, key: str, unit: str, dp: Decimal, dec: int) -> None:
        """T06 per-family adjustments of a freshly built row (no-ops for the T05 families)."""
        spec, lay = self.spec, self.lay
        if spec.unit_place == "inline" and row.unit:
            row.unit_placement = "inline"
            if lay.uk_style > 0:
                row.features.append("unit_in_brackets")
        if spec.cumulative:
            row.features.append("cumulative")
            row.previous = self._previous(dp, dec)
            if self.rng.random() < 0.03:  # AMB_UNLABELLED_MULTIVALUE (plan 3.8: IN-D 3%)
                row.unlabelled_multivalue = True
                row.features.append("unlabelled_multivalue")
        if spec.two_panel:
            row.features.append("two_per_line")
            if key in COUNT_KEYS and lay.amb_counts:  # no unit, no range, no header unit (plan 3.8)
                row.unit, row.unit_placement = "", "none"
                row.printed_range, row.range_lines = PrintedRange(), []
                row.features = [f for f in row.features if not f.endswith("_range")] + ["unitless_count"]
            elif key in ("wbc", "platelets") and lay.hdr_unit_text:  # unit only in the panel header
                row.unit = row.unit_true = lay.hdr_unit_text
                row.unit_placement = "header"
                row.features.append("header_unit")

    def _previous(self, dp: Decimal, dec: int) -> list[dict]:
        """Earlier values for the dated columns (oldest first): each differs from the current and from each other."""
        grouping = self.spec.grouping
        step = Decimal(1).scaleb(-dec)

        def txt(v: Decimal) -> str:
            return group_digits(format(v.quantize(step), "f"), grouping)

        seen = {txt(dp)}
        out: list[dict] = []
        for k in range(len(self.lay.dates) - 1):
            pv = None
            for _ in range(16):
                cand = sc.round_half_up(float(dp) * self.rng.uniform(0.78, 1.22), dec)
                if cand > 0 and txt(cand) not in seen:
                    pv = cand
                    break
            if pv is None:  # tiny values: step away deterministically
                pv = dp + step * (len(seen) + 1)
            t = txt(pv)
            seen.add(t)
            out.append({"date": self.lay.dates[k], "value": float(t.replace(",", "")), "value_text": t})
        return out

    def _name_width(self, unit_txt: str = "") -> float:
        if self.spec.csv:
            return 1e9
        if unit_txt:  # UK-A style 1 prints "Name (unit)" in the name cell
            w = base.text_width(f" ({unit_txt})", self.spec.font, self.spec.size) + 2
            return self._name_width() - w
        cols = sorted(c.x for c in self.spec.columns if c.align == "l")
        name_x = self.spec.col("name").x
        nxt = min((x for x in cols if x > name_x), default=name_x + 150)
        if self.spec.fid == "US-A":
            return 205 - 0.0
        return nxt - name_x - 6 if self.spec.method_mode != "under" else 250

    def _printed_flag(self, flag_true: str) -> str:
        if self.spec.flag_mode is None:
            return ""
        crit = self.spec.flag_mode == "column"
        base_flag = {"HIGH": "H", "LOW": "L", "CRITICAL_HIGH": "HH" if crit else "H",
                     "CRITICAL_LOW": "LL" if crit else "L"}.get(flag_true, "")
        if self.rng.random() < 0.01:  # a lab error: printed flag contradicts the value (AMB_PRINTED_FLAG_CONFLICT)
            return {"H": "L", "HH": "L", "L": "H", "LL": "H", "": self.rng.choice(("H", "L"))}[base_flag]
        return base_flag

    def _unit(self, key: str) -> str:
        return _unit_raw(key, CATALOGUE[key], self.spec, self.lay)

    def primary(self, key: str, title: str) -> PrintedRow | None:
        a = CATALOGUE[key]
        spec, lay, pt, rng = self.spec, self.lay, self.pt, self.rng
        unit = self._unit(key)
        interpretive = spec.interpretive and key in INTERPRETIVE and key not in ("egfr",)
        rb = _make_range(key, a, spec, lay, pt, unit, rng, interpretive)
        if rb is None:
            return None
        tgt = self.targets.get(key)
        state, direction = (tgt.state, tgt.direction) if tgt else ("normal", None)
        dec = units.decimals_for(key, unit)
        s = None
        for _ in range(8):
            s = sc.sample_value(key, state, rng, printed=rb.own, unit=unit, direction=direction, sex_code=pt.sex_code,
                                range_absent=interpretive)
            if s is None or not _near_bound(float(s.value), rb.bounds, dec):
                break
            s = None
        if s is None:
            return None
        qualifier = None
        dp = s.value
        if a.can_qualify and state in ("mild", "severe") and s.direction and rng.random() < 0.5:
            qualifier = ">" if s.direction == "high" else "<"
        if key == "troponin_i" and state == "normal" and rng.random() < 0.4:
            qualifier, dp = "<", Decimal("0.01")
        self.vals[key] = s.canonical
        return self._finish_row(key, title, unit, dp, rb, state, qualifier)

    # -- derived rows
    def derived(self, key: str, title: str) -> PrintedRow | None:
        v, pt, rng = self.vals, self.pt, self.rng
        c: float | None = None
        try:
            if key == "hematocrit":
                c = sc.hct_from(v["hemoglobin"], rng.choice((33.0, 33.3, 33.6)))
            elif key == "mcv":
                c = sc.mcv(v["hematocrit"], v["rbc"])
            elif key == "mch":
                c = sc.mch(v["hemoglobin"], v["rbc"])
            elif key == "mchc":
                c = sc.mchc(v["hemoglobin"], v["hematocrit"])
            elif key == "bilirubin_indirect":
                c = sc.indirect_bilirubin(v["bilirubin_total"], v["bilirubin_direct"])
            elif key == "globulin":
                c = sc.globulin(v["total_protein"], v["albumin"])
            elif key == "ag_ratio":
                c = sc.ag_ratio(v["albumin"], v["globulin"])
            elif key == "vldl":
                c = sc.vldl(v["triglycerides"])
            elif key == "ldl":
                c = sc.ldl_friedewald(v["cholesterol"], v["hdl"], v["triglycerides"])
                if c is None:
                    self.notes["LIPID"] = "tg"
            elif key == "non_hdl":
                c = sc.non_hdl(v["cholesterol"], v["hdl"])
            elif key == "chol_hdl_ratio":
                c = sc.chol_hdl_ratio(v["cholesterol"], v["hdl"])
            elif key == "ldl_hdl_ratio":
                c = sc.ldl_hdl_ratio(v["ldl"], v["hdl"])
            elif key == "neutrophils_abs":
                c = sc.abs_count(v["wbc"], v["neutrophils_pct"])
            elif key == "lymphocytes_abs":
                c = sc.abs_count(v["wbc"], v["lymphocytes_pct"])
            elif key == "egfr":
                c = sc.egfr_ckd_epi_2021(v["creatinine"], pt.input_age, pt.input_sex[0].upper())
        except (KeyError, ZeroDivisionError):
            return None
        if c is None:
            return None
        a = CATALOGUE[key]
        unit = self._unit(key)
        rb = _make_range(key, a, self.spec, self.lay, pt, unit, rng,
                         self.spec.interpretive and key == "egfr")
        if rb is None:
            return None
        dec = units.decimals_for(key, unit)
        dp = sc.round_half_up(units.from_canonical(key, c, unit), dec)
        upper = [b for b in (units.to_canonical(key, x, unit)[0] for x in rb.bounds)] or [None]
        if not sc.invariants_ok(key, dp, unit, rb.bounds, printed_upper_canon=max(upper) if rb.own is not None
                                and rb.own.high is not None else None):
            return None
        self.vals[key] = units.to_canonical(key, float(dp), unit)[0]
        row = self._finish_row(key, title, unit, dp, rb, "derived")
        if self.cls == "normal" and base.row_truth(row, pt).flag_true != "NORMAL":
            return None  # a normal report stays all-normal
        return row

    def run(self) -> list[PrintedRow]:
        rows: list[PrintedRow] = []
        for title, keys in self.spec.panels:
            for key in keys:
                if not self.eligible(key):
                    continue
                if key in ("neutrophils_abs", "lymphocytes_abs") and not self.spec.abs_counts:
                    continue
                r = self.derived(key, title) if key in DERIVED else self.primary(key, title)
                if r is None:
                    continue
                if key == "pt" and self.vals.get("inr", 0) > 2.4:
                    continue  # INR > 2.4 prints without PT seconds (Table C caps)
                rows.append(r)
        return rows


# ---------------------------------------------------------------- drawing

def _header_height(spec: FamilySpec, first: bool) -> float:
    if not first:
        return 60.0
    return {"in_a": 128.0, "in_b": 140.0, "in_c": 126.0, "us_a": 120.0, "in_d": 128.0, "in_e": 128.0,
            "uk_a": 120.0}[spec.header_style]


def _draw_header(doc: DocBuilder, spec: FamilySpec, lay: Lay, pt: Patient, page_no: int) -> None:
    ident = lay.ident
    W = doc.size[0]
    mx = doc.margin_x
    rx = W - mx
    y = doc.top
    pl = base.patient_lines(pt, ident, spec.date_style)
    f, fb = spec.font, spec.bold
    if page_no > 1:  # compact repeated header
        base.draw_logo(doc, mx, y, ident, 10)
        doc.put(mx + 28, y + 2, ident.lab_name, fb, 10)
        doc.put(rx, y + 2, pl["pid"][0], f, 8, align="r", decoy="header_id")
        doc.put(mx + 28, y + 16, pl["name"][0], f, 8)
        doc.put(rx, y + 16, pl["labno"][0], f, 8, align="r", decoy="header_id")
        doc.line(mx, y + 38, rx, y + 38, 0.6)
        doc.y = doc.top + _header_height(spec, False) - 14
        return
    style = spec.header_style
    if style in ("in_a", "in_d"):
        base.draw_logo(doc, mx, y, ident, 17)
        doc.put(mx + 44, y + 2, ident.lab_name, fb, 15)
        doc.put(mx + 44, y + 21, ident.address, f, 8, decoy="address")
        doc.put(rx, y + 2, f"Tel: {ident.phone}", f, 8, align="r", decoy="phone")
        doc.put(rx, y + 13, f"Toll Free: {ident.tollfree}", f, 8, align="r", decoy="tollfree")
        doc.put(rx, y + 24, f"{ident.email}  |  {ident.web}", f, 7.5, align="r")
        doc.line(mx, y + 42, rx, y + 42, 1.2)
        if style == "in_d":
            doc.put(W / 2, y + 2, "CUMULATIVE LABORATORY REPORT", fb, 10, align="c")
        _patient_two_col(doc, spec, lay, pl, y + 50, ("name", "agesex", "ref"), ("pid", "labno", "sample"))
    elif style == "in_e":
        base.draw_logo(doc, mx, y, ident, 17)
        doc.put(mx + 44, y + 2, ident.lab_name.upper(), fb, 13)
        doc.put(mx + 44, y + 19, "Hospital Laboratory Services", f, 8)
        doc.put(mx + 44, y + 30, ident.address, f, 7.5, decoy="address")
        doc.put(rx, y + 2, f"Tel: {ident.phone}", f, 8, align="r", decoy="phone")
        doc.put(rx, y + 13, f"Toll Free: {ident.tollfree}", f, 8, align="r", decoy="tollfree")
        doc.line(mx, y + 42, rx, y + 42, 1.0)
        _patient_two_col(doc, spec, lay, pl, y + 50, ("name", "agesex", "ref"), ("pid", "labno", "sample"))
        doc.put(mx + 4, y + 87.5, f"Ward/Bed: {lay.ward}", f, 8.5, decoy="header_id")
    elif style == "in_b":
        base.draw_logo(doc, (W - 34) / 2, y, ident, 17)
        doc.put(W / 2, y + 38, ident.lab_name.upper(), fb, 14, align="c")
        doc.put(W / 2, y + 55, ident.address, f, 8, align="c", decoy="address")
        doc.put(W / 2, y + 66, f"Phone: {ident.phone}  |  Toll Free: {ident.tollfree}  |  {ident.email}", f, 7.5,
                align="c", decoy="phone")
        doc.rect(mx, y + 80, rx - mx, 52, width=0.8)
        _patient_two_col(doc, spec, lay, pl, y + 85, ("name", "agesex", "ref"), ("pid", "labno", "sample"))
    elif style == "in_c":
        doc.rect(mx, y, rx - mx, 30, fill=0.85)
        base.draw_logo(doc, mx + 4, y + 2, ident, 13)
        doc.put(mx + 40, y + 7, ident.lab_name, fb, 14)
        doc.put(rx - 4, y + 5, f"Tel: {ident.phone}", f, 8, align="r", decoy="phone")
        doc.put(rx - 4, y + 16, f"{ident.address_street}, {ident.address_city}", f, 7, align="r", decoy="address")
        _patient_two_col(doc, spec, lay, pl, y + 40, ("name", "agesex", "ref"), ("pid", "labno", "sample"))
    elif style == "uk_a":
        doc.put(mx, y, ident.lab_name, fb, 15)
        doc.put(mx, y + 19, f"{ident.address}", f, 8, decoy="address")
        doc.put(mx, y + 30, f"Telephone: {ident.phone}", f, 8, decoy="phone")
        doc.rect(rx - 150, y, 150, 24, width=1.0)
        doc.put(rx - 75, y + 6, "PATHOLOGY REPORT", fb, 11, align="c")
        _patient_two_col(doc, spec, lay, pl, y + 52, ("name", "agesex", "pid"), ("labno", "sample", "ref"))
    else:  # us_a
        doc.put(mx, y, ident.lab_name, fb, 15)
        doc.put(mx, y + 19, f"{ident.address}", f, 8, decoy="address")
        doc.put(mx, y + 30, f"Phone: {ident.phone}", f, 8, decoy="phone")
        doc.rect(rx - 120, y, 120, 24, width=1.0)
        doc.put(rx - 60, y + 6, "FINAL REPORT", fb, 11, align="c")
        _patient_two_col(doc, spec, lay, pl, y + 52, ("name", "agesex", "pid"), ("labno", "sample", "ref"))
    doc.y = doc.top + _header_height(spec, True)


def _patient_two_col(doc: DocBuilder, spec: FamilySpec, lay: Lay, pl: dict, y: float, left: tuple[str, ...],
                     right: tuple[str, ...]) -> None:
    mx = doc.margin_x
    mid = doc.size[0] / 2 + 10
    f = spec.font
    yy = y
    for k in left:
        if k in pl:
            doc.put(mx + 4, yy, pl[k][0], f, 8.5, decoy=pl[k][1])
            yy += 12.5
    yy = y
    for k in right:
        if k in pl:
            doc.put(mid, yy, pl[k][0], f, 8.5, decoy=pl[k][1])
            yy += 12.5
    doc.put(mid, yy, f"Collected: {lay.collected}", f, 8.5, decoy="date")
    doc.put(mid + 140, yy, f"Reported: {lay.reported}", f, 8.5, decoy="date")


def _cols(spec: FamilySpec, lay: Lay) -> tuple[Column, ...]:
    """Columns for this report: IN-D keeps only the last ``len(lay.dates)`` dated columns, headed by their dates."""
    if not spec.cumulative:
        return spec.columns
    dated = [c for c in spec.columns if c.key.startswith("d")]
    keep = dated[len(dated) - len(lay.dates):]
    return tuple(Column(c.key, lay.dates[keep.index(c)], c.x) if c in keep else c
                 for c in spec.columns if c in keep or c not in dated)


def _col_header(doc: DocBuilder, spec: FamilySpec, title: str, cont: bool, lay: Lay | None = None) -> None:
    """Panel title band and column headings (title repeats with '(contd.)' after a page break)."""
    mx = doc.margin_x
    rx = doc.size[0] - mx
    t = f"{title} (contd.)" if cont else title
    doc.rect(mx, doc.y, rx - mx, 15, fill=0.9)
    doc.put(mx + 4, doc.y + 3, t, spec.bold, spec.size + 0.5)
    doc.y += 18
    if spec.cumulative and lay is not None:
        lab = 10.0 if lay.current_label else 0.0
        cols = _cols(spec, lay)
        newest = [c for c in cols if c.key.startswith("d")][-1]
        for c in cols:
            if c.key.startswith("d"):
                if lab and c is newest:
                    doc.put(c.x, doc.y, "Current", spec.bold, spec.size - 0.5)
                doc.put(c.x, doc.y + lab, c.header, spec.bold, spec.size - 0.5, decoy="date")
            else:
                doc.put(c.x, doc.y + lab, c.header, spec.bold, spec.size - 0.5, align=c.align)
        doc.y += 12 + lab
        doc.line(mx, doc.y - 1, rx, doc.y - 1, 0.6)
        doc.y += 3
        return
    for c in spec.columns:
        doc.put(c.x, doc.y, c.header, spec.bold, spec.size - 0.5, align=c.align)
    doc.y += 12
    doc.line(mx, doc.y - 1, rx, doc.y - 1, 0.6)
    doc.y += 3


def _cells(spec: FamilySpec, lay: Lay, r: PrintedRow) -> list[Cell]:
    f, fb, sz = spec.font, spec.bold, spec.size
    abnormal = r.printed_flag != ""
    bold = fb if abnormal and spec.fid != "IN-B" else f
    val = (f"{r.qualifier} {r.value_text}" if r.qualifier else r.value_text)
    sid = spec.fid
    if sid == "IN-A":
        return [simple_cell(40, r.name, f, sz), simple_cell(305, val, bold, sz), simple_cell(372, r.unit, f, sz),
                simple_cell(438, r.range_lines, f, sz + spec.range_size_delta), simple_cell(520, r.method, f, 7)]
    if sid == "IN-B":
        res = f"{val} {r.printed_flag}" if r.printed_flag else val
        return [simple_cell(40, r.name, f, sz), simple_cell(205, r.method, f, 7.5), simple_cell(292, res, fb, sz),
                simple_cell(352, r.unit, f, sz), simple_cell(418, r.range_lines, f, sz)]
    if sid == "IN-C":
        name = Cell(40, [(r.name, f, sz)] + ([(r.method, FONT_ITALIC, 6.5)] if r.method else []))
        return [name, simple_cell(300, val, bold, sz), simple_cell(372, r.unit, f, sz),
                simple_cell(448, r.range_lines, f, sz + spec.range_size_delta)]
    if sid == "IN-D":
        cols = _cols(spec, lay)
        xs = [c.x for c in cols if c.key.startswith("d")]
        vals = [p["value_text"] for p in r.previous] + [val]
        if r.unlabelled_multivalue:  # all values squeezed into one cell: no column or date says which is current
            vcells = [simple_cell(xs[0], "  ".join(vals), f, sz)]
        else:
            vcells = [simple_cell(x, v, f, sz) for x, v in zip(xs, vals, strict=True)]
        ux = next(c.x for c in cols if c.key == "unit")
        rx_ = next(c.x for c in cols if c.key == "range")
        return [simple_cell(40, r.name, f, sz), *vcells, simple_cell(ux, r.unit, f, sz),
                simple_cell(rx_, r.range_lines, f, sz)]
    if sid == "UK-A":
        name = f"{r.name} ({r.unit})" if lay.uk_style == 1 and r.unit else r.name
        res = f"{val} {r.unit}" if lay.uk_style == 0 and r.unit else val
        return [simple_cell(40, name, f, sz), simple_cell(270, res, f, sz), simple_cell(390, r.range_lines, f, sz)]
    return [simple_cell(36, r.name, f, sz), simple_cell(250, val, bold, sz, "r"),
            simple_cell(268, r.printed_flag, fb, sz), simple_cell(322, r.range_lines, f, sz),
            simple_cell(440, r.unit, f, sz), simple_cell(520, lay.lab_codes.get(r.section, "01"), f, sz)]


def _draw_interpretive(doc: DocBuilder, spec: FamilySpec, keys: list[str]) -> None:
    mx = doc.margin_x
    for k in keys:
        title, cats = INTERPRETIVE[k]
        h = 13 + 11 * len(cats) + 6
        if doc.y + h > doc.limit:
            doc.new_page()
        doc.y += 3
        doc.rect(mx + 20, doc.y, 300, h - 4, width=0.4, gray=0.4)
        doc.put(mx + 26, doc.y + 2, f"Interpretation: {title}", FONT_ITALIC, 7.5, decoy="interpretive")
        yy = doc.y + 14
        for cat, rngt in cats:
            doc.put(mx + 30, yy, cat, spec.font, 7.5)
            doc.put(mx + 160, yy, rngt, spec.font, 7.5, decoy="interpretive")
            yy += 11
        doc.y += h


def _cells_e(spec: FamilySpec, r: PrintedRow, off: float) -> list[Cell]:
    """IN-E half-panel row: name / result / unit (header-unit and unitless rows leave it empty) / range."""
    f, sz = spec.font, spec.size
    val = f"{r.qualifier} {r.value_text}" if r.qualifier else r.value_text
    return [simple_cell(36 + off, r.name, f, sz), simple_cell(128 + off, val, f, sz),
            simple_cell(172 + off, r.unit if r.unit_placement == "cell" else "", f, sz),
            simple_cell(218 + off, r.range_lines, f, sz - 0.5)]


_E_OFF = 270.0


def _col_header_e(doc: DocBuilder, spec: FamilySpec, lay: Lay, title: str, off: float, cont: bool) -> None:
    """One half-panel band and its column headings at the cursor (cursor is not moved)."""
    x0 = doc.margin_x if off == 0 else doc.margin_x + off
    x1 = 296.0 if off == 0 else doc.size[0] - doc.margin_x
    t = f"{title} (contd.)" if cont else title
    if title == "HAEMATOLOGY" and lay.hdr_unit_text:
        t += f" (x{lay.hdr_unit_text})"
    doc.rect(x0, doc.y, x1 - x0, 15, fill=0.9)
    doc.put(x0 + 4, doc.y + 3, t, spec.bold, spec.size + 0.5)
    for c in spec.columns:
        doc.put(c.x + off, doc.y + 18, c.header, spec.bold, spec.size - 0.5)
    doc.line(x0, doc.y + 29, x1, doc.y + 29, 0.6)


def _layout_e(spec: FamilySpec, lay: Lay, pt: Patient, rows: list[PrintedRow], notes: dict[str, str], pad: float,
              tg_text: str) -> DocBuilder:
    """IN-E: panels in consecutive pairs, drawn side by side; per page the LEFT half is drawn fully, then the RIGHT."""
    def on_page(doc: DocBuilder, n: int, is_break: bool) -> None:
        _draw_header(doc, spec, lay, pt, n)

    doc = DocBuilder(spec.page, margin_x=36.0, top=34.0, bottom=84.0, on_new_page=on_page)
    for k in range(0, len(spec.panels) - 1, 2):
        lt, rt = spec.panels[k][0], spec.panels[k + 1][0]
        sides: list[tuple[str, float, list]] = []
        for title, off in ((lt, 0.0), (rt, _E_OFF)):
            items: list = [r for r in rows if r.section == title]
            if notes.get("LIPID") == "tg" and any(r.key == "triglycerides" for r in items) \
                    and not any(r.key == "ldl" for r in items):
                items.append(f"LDL: Not calculated (TG > {tg_text})")
            sides.append((title, off, items))
        n = max(len(items) for _, _, items in sides)
        if n == 0:
            continue

        def lh(j: int) -> float:
            hs = []
            for _, off, items in sides:
                if j < len(items):
                    it = items[j]
                    hs.append(doc.row_height(_cells_e(spec, it, off), pad) if isinstance(it, PrintedRow)
                              else base.line_height(8) + pad)
            return max(hs)

        doc.ensure(33 + lh(0) * 2)
        i = 0
        while i < n:
            y0 = doc.y + 33
            cum = [0.0]
            j = i
            while j < n and y0 + cum[-1] + lh(j) <= doc.limit:
                cum.append(cum[-1] + lh(j))
                j += 1
            if j == i:
                doc.new_page()
                continue
            for title, off, items in sides:
                if i >= len(items):
                    continue
                hy = y0 - 33
                doc.y = hy
                _col_header_e(doc, spec, lay, title, off, i > 0)
                for m in range(i, min(j, len(items))):
                    doc.y = y0 + cum[m - i]
                    it = items[m]
                    if isinstance(it, PrintedRow):
                        doc.row(it, _cells_e(spec, it, off), pad)
                    else:
                        doc.put(doc.margin_x + off + 4, doc.y + pad / 2, it, FONT_ITALIC, 8, decoy="not_calculated")
            doc.y = y0 + cum[-1]
            i = j
            if i < n:
                doc.new_page()
        doc.y += 8
    doc.finish(lambda d, pg, total: base.draw_standard_footer(
        d, pg, total, lay.ident, size=7.0, right_edge=d.size[0] - d.margin_x))
    return doc


def _layout(spec: FamilySpec, lay: Lay, pt: Patient, rows: list[PrintedRow], notes: dict[str, str], pad: float,
            tg_text: str) -> DocBuilder:
    if spec.two_panel:
        return _layout_e(spec, lay, pt, rows, notes, pad, tg_text)

    def on_page(doc: DocBuilder, n: int, is_break: bool) -> None:
        _draw_header(doc, spec, lay, pt, n)

    doc = DocBuilder(spec.page, margin_x=40.0 if spec.fid != "US-A" else 36.0, top=34.0, bottom=84.0,
                     on_new_page=on_page)
    order: list[str] = []
    for r in rows:
        if r.section not in order:
            order.append(r.section)
    for title in order:
        srows = [r for r in rows if r.section == title]
        first_h = 18 + 15 + doc.row_height(_cells(spec, lay, srows[0]), pad) * 2
        doc.ensure(first_h)
        _col_header(doc, spec, title, False, lay)
        for r in srows:
            cells = _cells(spec, lay, r)
            if doc.y + doc.row_height(cells, pad) > doc.limit:
                doc.new_page()
                _col_header(doc, spec, title, True, lay)
            doc.row(r, cells, pad)
        if notes.get(title) == "tg" and any(r.key == "triglycerides" for r in srows) \
                and not any(r.key == "ldl" for r in srows):
            doc.ensure(16)
            doc.put(doc.margin_x + 4, doc.y + 2, f"LDL: Not calculated (TG > {tg_text})", FONT_ITALIC, 8,
                    decoy="not_calculated")
            doc.y += 15
        if spec.interpretive:
            ks = [r.key for r in srows if r.key in INTERPRETIVE]
            seen: list[str] = []
            for k in ks:
                if k not in seen:
                    seen.append(k)
            _draw_interpretive(doc, spec, seen)
        doc.y += 8
    if spec.fid == "US-A":
        need = 12 * (len(lay.footnotes) + 1) + 6
        doc.ensure(need)
        doc.put(doc.margin_x, doc.y, "Footnotes:", FONT_BOLD, 7.5)
        doc.y += 11
        for note in lay.footnotes:
            kind = "address" if re.search(r"\d", note) else "comment"
            doc.put(doc.margin_x, doc.y, note, FONT_REGULAR, 7.5, decoy=kind)
            doc.y += 11
    doc.finish(lambda d, pg, total: base.draw_standard_footer(
        d, pg, total, lay.ident, size=7.0, right_edge=d.size[0] - d.margin_x))
    return doc


def _select(rows: list[PrintedRow], n: int) -> list[PrintedRow]:
    """First ``n`` rows in order, always keeping the scenario-target rows (so the class survives trimming)."""
    must = {id(r) for r in rows if r.state in ("mild", "severe", "critical")}
    keep = set(must)
    extra = max(0, n - len(must))
    for r in rows:
        if extra == 0:
            break
        if id(r) not in keep:
            keep.add(id(r))
            extra -= 1
    return [r for r in rows if id(r) in keep]


def _fit(spec: FamilySpec, lay: Lay, pt: Patient, rows: list[PrintedRow], notes: dict[str, str], target: int,
         rng: random.Random, tg_text: str) -> tuple[DocBuilder, list[PrintedRow]] | None:
    """Re-plan content until pagination matches ``target`` pages (section 3.1)."""
    n_all = len(rows)
    n_min = len({id(r) for r in rows if r.state in ("mild", "severe", "critical")})
    lo_pad = {1: 3.0, 2: 5.0, 3: 9.0}[target]
    pads = [lay.start_pad + k * 2.0 for k in range(0, 14)]
    pads = [p for p in pads if p >= lo_pad - 1.5] or pads

    def pages(n: int, pad: float) -> int:
        sel = _select(rows, n)
        return _layout(spec, lay, pt, sel, notes, pad, tg_text).pages.__len__()

    for pad in pads:
        if n_all < 4 or pages(n_all, pad) < target:
            continue
        if pages(max(n_min, 4), pad) > target:
            continue
        a, b = max(n_min, 4), n_all  # smallest n with pages >= target
        while a < b:
            m = (a + b) // 2
            if pages(m, pad) >= target:
                b = m
            else:
                a = m + 1
        n_lo = a
        a, b = n_lo, n_all  # largest n with pages <= target
        while a < b:
            m = (a + b + 1) // 2
            if pages(m, pad) <= target:
                a = m
            else:
                b = m - 1
        n = rng.randint(n_lo, a)
        sel = _select(rows, n)
        doc = _layout(spec, lay, pt, sel, notes, pad, tg_text)
        if len(doc.pages) == target:
            return doc, sel
    return None


def _headline_ok(cls: str, rows: list[PrintedRow], pt: Patient) -> bool:
    h = clinical.headline([(t.is_critical, t.flag_true) for t in (base.row_truth(r, pt) for r in rows)])
    return h == {"normal": "normal", "mild": "abnormal", "severe": "abnormal", "critical": "critical"}[cls]


UK_STREETS = ("Hawthorn Close", "Millers Row", "Ashgrove Road", "Kingfisher Way", "Linden Terrace")
UK_CITIES = ("Leeds", "Bristol", "Norwich", "Exeter", "Derby", "Lincoln")


def _uk_identity(ident: Identity, rng: random.Random) -> Identity:
    """UK-A identity: Ofcom drama-range phones (01632 960NNN), fictional street, no regulator named (plan 3.9)."""
    street = f"{rng.randint(2, 98)} {rng.choice(UK_STREETS)}"
    city = f"{rng.choice(UK_CITIES)} {rng.choice('ABCDEFGH')}{rng.choice('JKLMNPRS')}{rng.randint(1, 9)} " \
           f"{rng.randint(1, 9)}{rng.choice('QXZ')}{rng.choice('QXZ')}"
    doc = ident.pathologist.split(",")[0]
    return replace(
        ident, address=f"{street}, {city}", address_street=street, address_city=city,
        phone=f"01632 960{rng.randint(0, 999):03d}", tollfree=f"01632 960{rng.randint(0, 999):03d}",
        pathologist=f"{doc}, FRCPath", locale="UK",
    )


def _extend_lay(spec: FamilySpec, lay: Lay, xrng: random.Random, collected: datetime.date) -> Lay:
    """T06 per-report choices, drawn from their own rng stream so the T05 draws (and hashes) are untouched."""
    kw: dict = {}
    if spec.cumulative:
        n = xrng.choice((2, 3))
        gaps = sorted(xrng.sample(range(28, 400), n - 1), reverse=True)  # oldest first
        prev = [base.fmt_date(collected - datetime.timedelta(days=g), spec.date_style) for g in gaps]
        kw.update(dates=tuple(prev + [lay.collected]), current_label=xrng.random() < 0.5)
    if spec.two_panel:
        amb = xrng.random() < 0.15  # AMB_UNITLESS_COUNT: 15% of count rows (report-level draw)
        kw.update(amb_counts=amb, hdr_unit_text="" if amb else ("10^9/L" if lay.si else "10^3/uL"),
                  ward=f"{xrng.choice('ABCD')}{xrng.randint(1, 6)} / Bed {xrng.randint(1, 40)}")
    if spec.fid == "UK-A":
        st = xrng.randrange(3)
        kw.update(uk_style=st, range_bracket_unit=st == 2)
    return replace(lay, **kw)


def setup(spec: FamilySpec, *, split: str, index: int, target_pages: int):
    """Per-report layout choices shared by the PDF families and GEN-CSV: ``(spec_with_panel_order, lay, seed_fn)``."""
    master = cfg.master_seed(split)
    seed = lambda purpose: cfg.seed_for(master, split, index, purpose)  # noqa: E731
    lrng = random.Random(seed("layout"))
    ident = base.make_identity(random.Random(seed("names")), spec.locale)
    if spec.fid == "UK-A":
        ident = _uk_identity(ident, random.Random(seed("names") + 1))
    collected, reported = base.fixed_dates(lrng)
    n_foot = lrng.randint(1, 2)
    foots = ("01 = Main Laboratory, " + ident.address, "** Fasting specimen recommended for glucose and lipid tests.",
             "Results relate only to the specimen received.")[: n_foot + 1]
    lay = Lay(
        ident=ident, collected=base.fmt_date(collected, spec.date_style), reported=base.fmt_date(reported, spec.date_style),
        syntax=lrng.choice(spec.two_sided), one_sided=lrng.choice(spec.one_sided),
        si=lrng.random() < spec.si_p, cumm=lrng.random() < spec.cumm_p, size=spec.size,
        unit_spell={k: lrng.randint(0, 3) for k in CATALOGUE}, name_pick={k: lrng.randint(0, 4) for k in CATALOGUE},
        footnotes=foots, lab_codes={}, start_pad=lrng.uniform(3.0, 6.0) if target_pages == 1
        else lrng.uniform(5.0, 8.0) if target_pages == 2 else lrng.uniform(9.0, 12.0),
    )
    lay = _extend_lay(spec, lay, random.Random(seed("layout") + 7919), collected)
    panels = list(spec.panels)
    head = panels[0]
    rest = panels[1:]
    lrng.shuffle(rest)
    if spec.two_panel:
        spec2 = spec  # panel pairs are fixed: haematology | biochemistry first
    else:
        spec2 = FamilySpec(**{**spec.__dict__, "panels": tuple([head] + rest)})  # panel order varies per report
    return spec2, lay, seed


def build_report(family: str, *, split: str, index: int, patient: Patient, scenario_class: str, target_pages: int,
                 report_id: str | None = None) -> PrintedReport:
    """Build the printed report (rows, decoys, pages) for one template family.

    Fully determined by (family, split, index, patient, scenario_class, target_pages): per-purpose seeds come from
    ``config.seed_for``. The value draw is repeated (attempt counter added to the seed) until pagination matches
    ``target_pages`` and the GT headline matches the scenario class. GEN-CSV is delegated to ``csv_export``.
    """
    spec = SPECS[family]
    if spec.csv:
        from tools.eval.layouts import csv_export  # local import: csv_export imports this module

        return csv_export.build_report(family, split=split, index=index, patient=patient,
                                       scenario_class=scenario_class, target_pages=target_pages, report_id=report_id)
    rid = report_id or f"{split[0].upper()}{index + 1:03d}"
    spec2, lay, seed = setup(spec, split=split, index=index, target_pages=target_pages)
    ident = lay.ident
    tg_text = "4.5 mmol/L" if lay.si else "400 mg/dL"
    for attempt in range(MAX_ATTEMPTS):
        srng = random.Random(seed("scenario") + attempt)
        vrng = random.Random(seed("values") + attempt)
        gen0 = _Gen(spec2, lay, patient, scenario_class, vrng, sc.ScenarioSpec(scenario_class))
        cand = gen0.candidates()
        plan = sc.plan_scenario(scenario_class, srng, patient, cand)
        gen = _Gen(spec2, lay, patient, scenario_class, vrng, plan)
        rows = gen.run()
        fit = _fit(spec2, lay, patient, rows, gen.notes, target_pages, vrng, tg_text)
        if fit is None:
            continue
        doc, sel = fit
        if not _headline_ok(scenario_class, sel, patient):
            continue
        feats = sorted({f for r in sel for f in r.features})
        return PrintedReport(
            report_id=rid, split=split, family=family, seed=seed("plan"), page_size=spec.page,
            lab={"name": ident.lab_name, "address": ident.address, "phone": ident.phone, "fictional": True},
            patient=patient, scenario={"class": scenario_class, "clusters": list(plan.clusters)},
            unit_system="SI" if any("si" in r.features for r in sel) else "conventional", rows=sel,
            decoys=doc.decoys, pages=doc.pages, features=feats, template_id=spec.template_id,
        )
    raise RuntimeError(f"{family}: could not build {rid} (class {scenario_class}, {target_pages} pages)")


def native_pdf(report: PrintedReport, path: str) -> None:
    if SPECS[report.family].csv:
        raise ValueError(f"{report.family} has no native PDF rendering (csv only); use csv_export.write_csv")
    base.write_pdf(report.pages, report.page_size, path, title=f"{report.lab['name']} - Laboratory Report")


__all__ = ["DERIVED", "SPECS", "FamilySpec", "build_report", "native_pdf", "setup"]
