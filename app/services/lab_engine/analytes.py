"""The ONE canonical analyte registry and the name matcher.

Everything the engine knows about a test lives here: canonical unit, unit
equivalents, reference ranges (sex / age specific), critical thresholds and
the synonyms a report may print. Names are matched by EXACT equality of the
canonical token set (see canon_tokens) -- never by substring -- so Direct vs
Indirect Bilirubin, LDL vs VLDL vs Non-HDL, Albumin vs Microalbumin,
Urea vs BUN and Fasting vs Post-prandial glucose can never be confused.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, FrozenSet, Iterable, List, Optional, Tuple, Union

Range = Tuple[Optional[float], Optional[float]]
Equivalent = Union[float, Callable[[float], float]]


@dataclass(frozen=True)
class Critical:
    direction: str          # "below" | "above"
    limit: float
    message: str
    priority: int = 1       # 1 = critical, 2 = warning only
    inclusive: bool = False
    # True when the limit is an assay decision cut-off sitting at the upper reference limit (troponin), not a
    # panic value far outside it: the laboratory's own printed range governs a value that lies inside it.
    cutoff: bool = False


@dataclass
class Analyte:
    key: str
    display: str
    synonyms: List[str]
    unit: str
    ranges: Dict[str, Range]
    equivalents: Dict[str, Equivalent] = field(default_factory=dict)
    criticals: List[Critical] = field(default_factory=list)
    age_rules: List[Tuple[int, int, Optional[str], Optional[float], Optional[float]]] = field(default_factory=list)
    kind: str = "numeric"
    sibling: Optional[str] = None      # bare differential name: "neutrophils" is % first, absolute second
    sanity_ratio: Optional[Tuple[float, float]] = None  # printed range must be this close to the registry range
    generic: bool = False              # bare ambiguous name ("pH", "PCT"): resolved from unit / report context
    allow_negative: bool = False       # negative results are physiological (base excess ...)

    @property
    def sex_specific(self) -> bool:
        return "M" in self.ranges or "F" in self.ranges or bool(self.age_rules)

    def all_ranges(self) -> List[Range]:
        out = list(self.ranges.values())
        out.extend((lo, hi) for _a, _b, _s, lo, hi in self.age_rules)
        return out

    def registry_range(self, sex: Optional[str], age: Optional[int]) -> Tuple[Optional[Range], str]:
        """(range, reason-if-none). Never assumes a sex or an age, and never applies an adult
        interval to a child unless the analyte carries explicit age rules."""
        if not self.age_rules and age is not None and age < 18:
            return None, f"adult reference range only; none for age {age}"
        if self.age_rules:
            if age is None:
                return None, "age not provided"
            matched = [r for r in self.age_rules if r[0] <= age <= r[1]]
            if not matched:
                return None, f"no reference range for age {age}"
            sexual = [r for r in matched if r[2] is not None]
            neutral = [r for r in matched if r[2] is None]
            if neutral:
                r = neutral[0]
                return (r[3], r[4]), ""
            if sex is None:
                return None, "sex not provided"
            for r in sexual:
                if r[2] == sex:
                    return (r[3], r[4]), ""
            return None, f"no reference range for sex {sex} at age {age}"
        if "all" in self.ranges and not ("M" in self.ranges or "F" in self.ranges):
            return self.ranges["all"], ""
        if "M" in self.ranges or "F" in self.ranges:
            if sex is None:
                return None, "sex not provided"
            if sex in self.ranges:
                return self.ranges[sex], ""
            if "all" in self.ranges:
                return self.ranges["all"], ""
            return None, f"no reference range for sex {sex}"
        return None, "no registry range"


# ── name canonicalisation ────────────────────────────────────────────────────

NOISE_TOKENS = {
    "serum", "plasma", "blood", "whole", "level", "levels", "test", "result", "value", "count", "conc",
    "concentration", "s", "sr", "patient", "sample", "specimen", "status", "approx", "approximately", "about",
    # measurement-method words that never change identity
    "calculated", "calc", "estimated", "jaffe", "enzymatic", "kinetic", "photometric", "colorimetric",
    "method", "ise",
}

# Multi-word synonyms, applied (longest first) on the lower-cased,
# punctuation-stripped name before it is split into tokens.
_PHRASES: List[Tuple[str, str]] = [
    ("total leukocyte count", "wbc"), ("total wbc count", "wbc"), ("total wbc", "wbc"),
    ("white blood cell count", "wbc"), ("white blood cells", "wbc"), ("white blood cell", "wbc"),
    ("white cell count", "wbc"), ("white cells", "wbc"), ("leukocytes", "wbc"), ("leukocyte", "wbc"), ("tlc", "wbc"),
    ("red blood cell count", "rbc"), ("red blood cells", "rbc"), ("red blood cell", "rbc"),
    ("red cell count", "rbc"), ("erythrocytes", "rbc"), ("erythrocyte", "rbc"),
    ("platelet count", "platelet"), ("platelets", "platelet"), ("thrombocytes", "platelet"),
    ("mean corpuscular hemoglobin concentration", "mchc"), ("mean corpuscular hemoglobin", "mch"),
    ("mean corpuscular volume", "mcv"), ("mean cell volume", "mcv"), ("mean platelet volume", "mpv"),
    ("red cell distribution width cv", "rdw"), ("red cell distribution width", "rdw"),
    ("packed cell volume", "hematocrit"), ("erythrocyte sedimentation rate", "esr"),
    ("hemoglobin s", "hbs"), ("hb s", "hbs"), ("hemoglobin f", "hbf"), ("hb f", "hbf"), ("fetal hb", "hbf"),
    ("fetal hemoglobin", "hbf"), ("hemoglobin a2", "hba2"), ("hb a2", "hba2"), ("hemoglobin a", "hba"), ("hb a", "hba"),
    ("glycated hemoglobin", "hba1c"), ("glycosylated hemoglobin", "hba1c"), ("glycohemoglobin", "hba1c"),
    ("glyco hemoglobin", "hba1c"), ("hemoglobin a1c", "hba1c"), ("hb a1c", "hba1c"), ("a1c", "hba1c"),
    ("alanine aminotransferase", "alt"), ("alanine transaminase", "alt"), ("sgpt", "alt"),
    ("aspartate aminotransferase", "ast"), ("aspartate transaminase", "ast"), ("sgot", "ast"),
    ("gamma glutamyl transferase", "ggt"), ("gamma glutamyl transpeptidase", "ggt"),
    ("gamma gt", "ggt"), ("ggtp", "ggt"),
    ("alkaline phosphatase", "alp"),
    ("total iron binding capacity", "tibc"), ("iron binding capacity", "tibc"),
    ("transferrin saturation", "tsat"), ("transferrin sat", "tsat"),
    ("thyroid stimulating hormone", "tsh"),
    ("free thyroxine", "free t4"), ("free triiodothyronine", "free t3"), ("ft4", "free t4"), ("ft3", "free t3"),
    ("thyroxine", "t4"), ("triiodothyronine", "t3"),
    ("very low density lipoprotein cholesterol", "vldl"), ("very low density lipoprotein", "vldl"),
    ("vldl cholesterol", "vldl"), ("vldl c", "vldl"),
    ("low density lipoprotein cholesterol", "ldl"), ("low density lipoprotein", "ldl"),
    ("ldl cholesterol", "ldl"), ("ldl c", "ldl"),
    ("high density lipoprotein cholesterol", "hdl"), ("high density lipoprotein", "hdl"),
    ("hdl cholesterol", "hdl"), ("hdl c", "hdl"),
    ("total cholesterol", "cholesterol"),
    ("blood urea nitrogen", "bun"), ("urea nitrogen", "bun"),
    ("high sensitivity", "hs"), ("highly sensitive", "hs"), ("hscrp", "hs crp"),
    ("c reactive protein", "crp"),
    ("cardiac troponin", "troponin"), ("ctni", "troponin i"), ("tni", "troponin i"),
    ("ctnt", "troponin t"), ("tnt", "troponin t"),
    ("25 hydroxy vitamin d", "vitamin d"), ("25 oh vitamin d", "vitamin d"), ("vitamin d 25 oh", "vitamin d"),
    ("vitamin d3 25 hydroxy", "vitamin d"), ("25 hydroxy cholecalciferol", "vitamin d"),
    ("25 hydroxy", "vitamin d"), ("vitamin d total", "vitamin d"), ("vit d", "vitamin d"),
    ("vitamin b12", "b12"), ("vit b12", "b12"), ("cobalamin", "b12"),
    ("post prandial", "pp"), ("postprandial", "pp"), ("post meal", "pp"), ("post lunch", "pp"),
    ("2 hour pp", "pp"), ("2 hr pp", "pp"), ("2hr pp", "pp"),
    ("after meal", "pp"), ("after food", "pp"), ("after lunch", "pp"), ("after breakfast", "pp"),
    ("post breakfast", "pp"), ("post food", "pp"), ("after eating", "pp"),
    ("ca corrected", "calcium"), ("corrected ca", "calcium"), ("adjusted ca", "calcium"), ("ca adjusted", "calcium"),
    ("ca corr", "calcium"), ("s g p t", "alt"), ("s g o t", "ast"),
    ("2 hr plasma glucose", "pp glucose"), ("2hr plasma glucose", "pp glucose"), ("2 hour plasma glucose", "pp glucose"),
    ("mean blood glucose", "mean glucose"), ("estimated average glucose", "mean glucose"),
    ("creatine kinase", "ck"), ("cpk", "ck"), ("lactate dehydrogenase", "ldh"),
    ("prostate specific antigen", "psa"), ("c peptide", "cpeptide"),
    ("potassium", "potassium"),
    # abbreviations / name variants that hid criticals
    ("corrected calcium", "calcium"), ("calcium corrected", "calcium"), ("adjusted calcium", "calcium"),
    ("calcium adjusted", "calcium"), ("albumin corrected calcium", "calcium"), ("albumin adjusted calcium", "calcium"),
    ("calcium albumin corrected", "calcium"), ("corr calcium", "calcium"), ("corr ca", "calcium"),
    ("t bilirubin", "total bilirubin"), ("t bil", "total bilirubin"), ("tot bilirubin", "total bilirubin"),
    ("d bilirubin", "direct bilirubin"), ("d bil", "direct bilirubin"), ("i bilirubin", "indirect bilirubin"),
    ("i bil", "indirect bilirubin"), ("t protein", "total protein"), ("t cholesterol", "total cholesterol"),
    ("t chol", "total cholesterol"),
    ("cholesterol hdl", "hdl"), ("cholesterol ldl", "ldl"), ("cholesterol vldl", "vldl"),
    ("cholesterol non hdl", "non hdl"),
    ("vitamin b 12", "b12"), ("vit b 12", "b12"), ("b 12", "b12"),
    ("arterial blood ph", "bloodph"), ("venous blood ph", "bloodph"), ("capillary blood ph", "bloodph"),
    ("blood ph", "bloodph"), ("arterial ph", "bloodph"), ("ph arterial", "bloodph"), ("venous ph", "bloodph"),
    ("ph venous", "bloodph"), ("capillary ph", "bloodph"), ("abg ph", "bloodph"), ("ph abg", "bloodph"),
    ("ph blood", "bloodph"), ("ph of blood", "bloodph"),
    ("urine ph", "urineph"), ("urinary ph", "urineph"), ("ph urine", "urineph"), ("reaction ph", "urineph"),
    ("ph reaction", "urineph"), ("reaction", "urineph"),
    ("hs troponin t", "hstnt"), ("troponin t hs", "hstnt"), ("high sensitivity troponin t", "hstnt"),
    ("hs ctnt", "hstnt"), ("hs tnt", "hstnt"), ("hs cardiac troponin t", "hstnt"), ("troponin t high sensitivity", "hstnt"),
    ("plateletcrit", "plateletcrit"), ("platelet crit", "plateletcrit"), ("pro calcitonin", "procalcitonin"),
    # German report words seen in decimal-comma exports
    ("thrombozyten", "platelet"), ("leukozyten", "wbc"), ("erythrozyten", "rbc"), ("hamoglobin", "hemoglobin"),
    ("kalium", "potassium"), ("natrium", "sodium"), ("chlorid", "chloride"), ("kreatinin", "creatinine"),
    ("harnstoff", "urea"), ("glukose", "glucose"), ("harnsaure", "uric acid"),
]
_PHRASES.sort(key=lambda p: -len(p[0]))
_PHRASE_RES = [(re.compile(rf"(?<![a-z0-9]){re.escape(p)}(?![a-z0-9])"), r) for p, r in _PHRASES]

_TOKEN_MAP = {
    "hb": "hemoglobin", "hgb": "hemoglobin", "plt": "platelet", "scr": "creatinine", "fe": "iron",
    "fbs": "fasting glucose", "fpg": "fasting glucose", "ppbs": "pp glucose", "ppg": "pp glucose",
    "rbs": "random glucose", "sugar": "glucose", "pcv": "hematocrit", "hct": "hematocrit",
    "bicarb": "bicarbonate", "percent": "pct", "percentage": "pct", "absolute": "abs", "ionised": "ionized",
    "chol": "cholesterol", "trig": "triglyceride", "tg": "triglyceride", "tgl": "triglyceride",
    "phosphate": "phosphorus", "inorganic": "", "polymorphs": "neutrophil", "polys": "neutrophil",
    "lymphs": "lymphocyte", "monos": "monocyte", "eos": "eosinophil", "basos": "basophil",
    "neut": "neutrophil", "lymph": "lymphocyte", "mono": "monocyte", "eosin": "eosinophil", "baso": "basophil",
    "microalbuminuria": "micro albumin", "microalbumin": "micro albumin", "hemoglobin1c": "hba1c",
    "anc": "abs neutrophil", "alc": "abs lymphocyte", "aec": "abs eosinophil", "creat": "creatinine",
    "creatinin": "creatinine",
}


def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))


def canon_tokens(name: str) -> FrozenSet[str]:
    """Name -> frozenset of canonical tokens. Qualifier words (direct, indirect,
    total, free, micro, absolute, fasting, pp, random, ldl/vldl/hdl, non, i, t,
    d, b12 ...) are KEPT, so different analytes stay different; only case,
    punctuation, noise words, plurals and known synonyms are normalised away."""
    text = _strip_accents(str(name or "")).lower()
    for a, b in (("haemo", "hemo"), ("haemat", "hemat"), ("leucocyte", "leukocyte"), ("leucocytes", "leukocytes"),
                 ("foetal", "fetal"), ("ionised", "ionized"), ("oesin", "eosin")):
        text = text.replace(a, b)
    text = text.replace("%", " pct ").replace("#", " abs ")
    # dotted abbreviations: "W.B.C." -> "wbc", "S.G.P.T" -> "sgpt" (a lone "S." / "T." stays a qualifier word)
    text = re.sub(r"\b(?:[a-z]\.){2,}[a-z]?(?![a-z])|\b[a-z](?:\.[a-z])+(?![a-z])",
                  lambda m: re.sub(r"[^a-z]", "", m.group(0)), text)
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    text = f" {text} "
    for rx, repl in _PHRASE_RES:
        text = rx.sub(repl, text)
    raw: List[str] = []
    for tok in text.split():
        raw.extend(_TOKEN_MAP.get(tok, tok).split())
    # "2 hours" / "2hr" -> one duration token "2h" (a method/timing qualifier, not part of the test's identity
    # for post-prandial glucose; for any other test it stays and keeps the name from matching a plain analyte)
    tokens: List[str] = []
    i = 0
    while i < len(raw):
        tok = raw[i]
        m = re.fullmatch(r"(\d+)(?:hours?|hrs?|h)", tok)
        if m:
            tokens.append(f"{m.group(1)}h")
        elif tok.isdigit() and i + 1 < len(raw) and raw[i + 1] in ("hour", "hours", "hr", "hrs", "h"):
            tokens.append(f"{tok}h")
            i += 1
        else:
            tokens.append(tok)
        i += 1
    out = set()
    for tok in tokens:
        if tok in NOISE_TOKENS:
            continue
        if len(tok) > 4 and tok.endswith("s") and not tok.endswith(("ss", "us", "is")):
            tok = tok[:-1]
        out.add(tok)
    if "fasting" in out:
        # "Glucose, Fasting, 12 hr": the fasting duration is not part of the test's identity
        out = {t for t in out if not re.fullmatch(r"\d+h", t)}
    if "2h" in out:
        if "pp" in out:
            out.discard("2h")
        elif "glucose" in out and not ({"fasting", "random"} & out):
            out.discard("2h")
            out.add("pp")
    return frozenset(out)


# ── registry construction ────────────────────────────────────────────────────

def _r(all_=None, M=None, F=None) -> Dict[str, Range]:
    out: Dict[str, Range] = {}
    if all_ is not None:
        out["all"] = all_
    if M is not None:
        out["M"] = M
    if F is not None:
        out["F"] = F
    return out


def _crit(direction: str, limit: float, message: str, priority: int = 1, inclusive: bool = False,
          cutoff: bool = False) -> Critical:
    return Critical(direction, float(limit), message, priority, inclusive, cutoff)


REGISTRY: Dict[str, Analyte] = {}


def _add(key: str, display: str, synonyms: Iterable[str], unit: str, ranges: Dict[str, Range], *,
         eq: Optional[Dict[str, Equivalent]] = None, crit: Iterable[Critical] = (),
         age_rules=(), sibling: Optional[str] = None, sanity_ratio=None, generic: bool = False,
         allow_negative: bool = False) -> Analyte:
    a = Analyte(key=key, display=display, synonyms=[display, key.replace("_", " "), *synonyms], unit=unit,
                ranges=ranges, equivalents=dict(eq or {}), criticals=list(crit), age_rules=list(age_rules),
                sibling=sibling, sanity_ratio=sanity_ratio, generic=generic, allow_negative=allow_negative)
    REGISTRY[key] = a
    return a


# SI <-> conventional factors (standard)
_GLUCOSE = {"mmol/l": 18.016}
_MONO = {"meq/l": 1.0}

# --- haematology -------------------------------------------------------------
_add("hemoglobin", "Hemoglobin", ["hb", "hgb", "haemoglobin", "hemoglobin hb", "hb pct", "hemoglobin pct"], "g/dL",
     _r(M=(13.5, 17.5), F=(12.0, 15.5)), eq={"g/l": 0.1, "mmol/l": 1.611},
     crit=[_crit("below", 7.0, "Severe anemia - transfusion likely required")],
     age_rules=[(2, 5, None, 11.0, 14.0), (6, 11, None, 11.5, 15.5), (12, 17, "M", 13.0, 16.0),
                (12, 17, "F", 12.0, 15.0), (18, 120, "M", 13.5, 17.5), (18, 120, "F", 12.0, 15.5)])
_add("hematocrit", "Hematocrit", ["hct", "pcv", "packed cell volume"], "%",
     _r(M=(41, 53), F=(36, 46)), eq={"l/l": 100.0},
     age_rules=[(2, 5, None, 34, 40), (6, 11, None, 35, 45), (12, 17, "M", 37, 49), (12, 17, "F", 36, 46),
                (18, 120, "M", 41, 53), (18, 120, "F", 36, 46)])
_add("rbc", "RBC", ["rbc count", "red blood cell count", "red blood cells", "erythrocytes"], "million/µL",
     _r(M=(4.7, 6.1), F=(4.2, 5.4)))
_add("wbc", "WBC", ["wbc count", "total wbc", "total leukocyte count", "tlc", "total leucocyte count",
                    "leukocytes", "white blood cell count"], "/µL", _r(all_=(4000, 11000)),
     crit=[_crit("above", 50000, "Extreme leukocytosis - possible leukemia"),
           _crit("below", 2000, "Severe leukopenia - high infection risk")])
_add("platelets", "Platelets", ["platelet count", "plt", "platelet"], "/µL", _r(all_=(150000, 450000)),
     crit=[_crit("below", 20000, "Critical bleeding risk - possible ITP or dengue"),
           _crit("above", 1000000, "Extreme thrombocytosis - clotting disorder risk")])
_add("mcv", "MCV", ["mean corpuscular volume"], "fL", _r(all_=(80, 100)))
_add("mch", "MCH", ["mean corpuscular hemoglobin"], "pg", _r(all_=(27, 33)))
_add("mchc", "MCHC", ["mean corpuscular hemoglobin concentration"], "g/dL", _r(all_=(32, 36)),
     eq={"g/l": 0.1, "%": 1.0})
_add("rdw", "RDW", ["rdw cv", "rdw-cv", "red cell distribution width"], "%", _r(all_=(11, 15)))
_add("mpv", "MPV", ["mean platelet volume"], "fL", _r(all_=(7.5, 12.5)))
_add("esr", "ESR", ["erythrocyte sedimentation rate"], "mm/hr", _r(M=(0, 15), F=(0, 20)))
for _k, _d, _lo, _hi, _alo, _ahi in (("neutrophils", "Neutrophils", 40, 70, 1800, 7700),
                                      ("lymphocytes", "Lymphocytes", 20, 40, 1000, 4800),
                                      ("monocytes", "Monocytes", 2, 8, 200, 800),
                                      ("eosinophils", "Eosinophils", 1, 4, 0, 450),
                                      ("basophils", "Basophils", 0, 1, 0, 200)):
    _stem = _d.lower()[:-1]
    _add(f"{_k}_pct", f"{_d} %", [_d, _stem, f"{_d} pct", f"{_stem} percentage"], "%", _r(all_=(_lo, _hi)),
         sibling=f"{_k}_abs")
    _crit_abs = [_crit("below", 500, "Severe neutropenia - high infection risk", inclusive=True)] if _k == "neutrophils" else []
    _add(f"{_k}_abs", f"{_d} Absolute", [f"{_d} abs", f"absolute {_stem}", f"absolute {_stem} count",
                                         f"{_stem} absolute count", f"{_d} #"], "/µL", _r(all_=(_alo, _ahi)),
         crit=_crit_abs)
REGISTRY["neutrophils_abs"].synonyms.append("anc")
REGISTRY["lymphocytes_abs"].synonyms.append("alc")

# --- glucose -----------------------------------------------------------------
_GLU_CRIT = [_crit("above", 300, "Diabetic crisis - hyperglycemic emergency"),
             _crit("below", 50, "Severe hypoglycemia - immediate intervention")]
_add("glucose_fasting", "Glucose Fasting", ["fasting glucose", "fasting blood sugar", "fbs", "fpg",
                                            "fasting plasma glucose", "fasting blood glucose"],
     "mg/dL", _r(all_=(70, 99)), eq=dict(_GLUCOSE), crit=_GLU_CRIT)
_add("glucose_pp", "Glucose Post Prandial", ["pp glucose", "ppbs", "post prandial blood sugar", "post prandial glucose",
                                              "2hr plasma glucose", "glucose pp"],
     "mg/dL", _r(all_=(0, 140)), eq=dict(_GLUCOSE), crit=_GLU_CRIT)
_add("glucose_random", "Glucose Random", ["random glucose", "random blood sugar", "rbs", "random blood glucose"],
     "mg/dL", _r(all_=(70, 140)), eq=dict(_GLUCOSE), crit=_GLU_CRIT)
_add("glucose_unspecified", "Glucose", ["blood glucose", "blood sugar", "glucose serum", "plasma glucose"],
     "mg/dL", _r(all_=(70, 140)), eq=dict(_GLUCOSE), crit=_GLU_CRIT)
_add("mean_glucose", "Mean Blood Glucose", ["estimated average glucose", "eag"], "mg/dL", _r(all_=(70, 140)),
     eq=dict(_GLUCOSE))
_add("hba1c", "HbA1c", ["glycated hemoglobin", "glycosylated hemoglobin", "hemoglobin a1c", "a1c", "hb a1c"],
     "%", _r(all_=(0, 5.7)), eq={"mmol/mol": lambda v: v / 10.929 + 2.15})

# --- lipids ------------------------------------------------------------------
_CHOL = {"mmol/l": 38.67}
_add("cholesterol", "Total Cholesterol", ["cholesterol", "total cholesterol", "chol"], "mg/dL", _r(all_=(0, 200)), eq=dict(_CHOL))
_add("ldl", "LDL Cholesterol", ["ldl", "ldl cholesterol", "direct ldl", "ldl direct"], "mg/dL", _r(all_=(0, 100)), eq=dict(_CHOL))
_add("hdl", "HDL Cholesterol", ["hdl", "hdl cholesterol"], "mg/dL", _r(M=(40, None), F=(50, None)), eq=dict(_CHOL))
_add("vldl", "VLDL Cholesterol", ["vldl", "vldl cholesterol"], "mg/dL", _r(all_=(5, 40)), eq=dict(_CHOL))
_add("non_hdl", "Non-HDL Cholesterol", ["non hdl", "non hdl cholesterol", "nonhdl cholesterol"], "mg/dL",
     _r(all_=(0, 130)), eq=dict(_CHOL))
_add("triglycerides", "Triglycerides", ["triglyceride", "tg"], "mg/dL", _r(all_=(0, 150)), eq={"mmol/l": 88.57},
     crit=[_crit("above", 500, "Pancreatitis risk - immediate intervention")])
_add("chol_hdl_ratio", "Chol/HDL Ratio", ["chol hdl ratio", "cholesterol hdl ratio", "tc hdl ratio"], "", _r(all_=(0, 5.0)))
_add("ldl_hdl_ratio", "LDL/HDL Ratio", ["ldl hdl ratio"], "", _r(all_=(0, 3.5)))

# --- liver / protein ---------------------------------------------------------
_BIL = {"umol/l": lambda v: v / 17.1}
_add("bilirubin_total", "Total Bilirubin", ["bilirubin", "total bilirubin", "bilirubin total", "billrubin"], "mg/dL",
     _r(all_=(0.1, 1.2)), eq=dict(_BIL), crit=[_crit("above", 10.0, "Severe jaundice - liver failure or hemolysis")])
_add("bilirubin_direct", "Direct Bilirubin", ["bilirubin direct", "conjugated bilirubin", "bilirubin conjugated"],
     "mg/dL", _r(all_=(0, 0.3)), eq=dict(_BIL))
_add("bilirubin_indirect", "Indirect Bilirubin", ["bilirubin indirect", "unconjugated bilirubin"], "mg/dL",
     _r(all_=(0, 1.1)), eq=dict(_BIL))
_add("alt", "ALT", ["sgpt", "alanine aminotransferase", "alt sgpt"], "U/L", _r(M=(7, 30), F=(7, 19)))
_add("ast", "AST", ["sgot", "aspartate aminotransferase", "ast sgot"], "U/L", _r(all_=(10, 40)))
_add("alp", "Alkaline Phosphatase", ["alp", "alkaline phosphatase"], "U/L", _r(all_=(44, 147)),
     age_rules=[(1, 12, None, 150, 420), (13, 17, None, 50, 390), (18, 120, None, 44, 147)])
_add("ggt", "GGT", ["gamma gt", "gamma glutamyl transferase", "ggtp"], "U/L", _r(M=(10, 71), F=(6, 42)))
_add("albumin", "Albumin", ["albumin serum"], "g/dL", _r(all_=(3.5, 5.0)), eq={"g/l": 0.1})
_add("total_protein", "Total Protein", ["protein total", "total proteins", "serum protein"], "g/dL",
     _r(all_=(6.0, 8.3)), eq={"g/l": 0.1})
_add("globulin", "Globulin", [], "g/dL", _r(all_=(2.3, 3.5)), eq={"g/l": 0.1})
_add("ag_ratio", "A/G Ratio", ["albumin globulin ratio", "a g ratio"], "", _r(all_=(1.3, 1.7)))
_add("microalbumin", "Microalbumin", ["micro albumin", "urine microalbumin", "urinary microalbumin"], "mg/L",
     _r(all_=(0, 16.7)))
_add("microalbumin_creatinine", "Microalbumin/Creatinine Ratio", ["acr", "albumin creatinine ratio",
                                                                  "microalbumin creatinine ratio"],
     "mg/g creatinine", _r(all_=(0, 30)))

# --- renal / electrolytes ----------------------------------------------------
_add("creatinine", "Creatinine", ["serum creatinine", "creatinine serum", "scr", "s creatinine"], "mg/dL",
     _r(M=(0.7, 1.3), F=(0.5, 1.1)), eq={"umol/l": lambda v: v / 88.42, "mmol/l": lambda v: v * 1000 / 88.42},
     crit=[_crit("above", 10.0, "Renal failure - dialysis likely required")])
_add("urea", "Urea", ["blood urea", "serum urea"], "mg/dL", _r(all_=(15, 45)), eq={"mmol/l": 6.006})
_add("bun", "BUN", ["blood urea nitrogen", "urea nitrogen"], "mg/dL", _r(all_=(7, 20)), eq={"mmol/l": 2.801})
_add("bun_creatinine_ratio", "BUN/Creatinine Ratio", ["bun creatinine ratio", "urea creatinine ratio"], "", _r(all_=(10, 20)))
_add("uric_acid", "Uric Acid", ["serum uric acid", "urate"], "mg/dL", _r(M=(3.4, 7.0), F=(2.4, 6.0)),
     eq={"umol/l": lambda v: v / 59.48})
_add("egfr", "eGFR", ["estimated gfr", "gfr"], "mL/min/1.73m²", _r(all_=(60, None)))
_add("sodium", "Sodium", ["na", "serum sodium", "natrium"], "mmol/L", _r(all_=(135, 145)), eq=dict(_MONO),
     crit=[_crit("below", 125, "Severe hyponatremia - seizure/coma risk"),
           _crit("above", 155, "Severe hypernatremia - dehydration/neurological")])
_add("potassium", "Potassium", ["k", "serum potassium", "kalium"], "mmol/L", _r(all_=(3.5, 5.0)), eq=dict(_MONO),
     crit=[_crit("above", 6.0, "Severe hyperkalemia - cardiac arrhythmia risk"),
           _crit("below", 3.0, "Severe hypokalemia - cardiac and muscle risk")])
_add("chloride", "Chloride", ["cl", "serum chloride"], "mmol/L", _r(all_=(96, 106)), eq=dict(_MONO))
_add("bicarbonate", "Bicarbonate", ["hco3", "total co2", "co2", "tco2", "bicarb", "co2 total"], "mmol/L",
     _r(all_=(22, 29)), eq=dict(_MONO))
_add("calcium", "Calcium", ["ca", "total calcium", "serum calcium", "calcium total"], "mg/dL", _r(all_=(8.5, 10.5)),
     eq={"mmol/l": 4.008, "meq/l": 2.004},
     crit=[_crit("above", 13.0, "Hypercalcemic crisis - cardiac arrest risk"),
           _crit("below", 6.5, "Severe hypocalcemia - tetany/seizure risk")])
_add("ionized_calcium", "Ionized Calcium", ["calcium ionized", "ionized ca", "ca ionized", "ica"], "mmol/L",
     _r(all_=(1.12, 1.32)), eq={"mg/dl": 0.2495},
     crit=[_crit("below", 0.8, "Severe ionized hypocalcemia - tetany/seizure risk"),
           _crit("above", 1.6, "Severe ionized hypercalcemia")])
_add("magnesium", "Magnesium", ["mg", "serum magnesium"], "mg/dL", _r(all_=(1.7, 2.2)), eq={"mmol/l": 2.431, "meq/l": 1.2155},
     crit=[_crit("below", 1.0, "Severe hypomagnesemia - arrhythmia/seizure risk"),
           _crit("above", 4.0, "Severe hypermagnesemia - respiratory/cardiac depression")])
_add("phosphorus", "Phosphorus", ["phosphate", "inorganic phosphorus", "serum phosphorus", "po4"], "mg/dL",
     _r(all_=(2.5, 4.5)), eq={"mmol/l": 3.097},
     crit=[_crit("below", 1.0, "Severe hypophosphatemia - muscle/respiratory failure risk")])

# --- thyroid / hormones ------------------------------------------------------
_add("tsh", "TSH", ["thyroid stimulating hormone", "thyrotropin"], "mIU/L", _r(all_=(0.4, 4.0)))
_add("t3", "T3", ["triiodothyronine", "t3 total", "total t3", "triiodothyronine total"], "ng/dL", _r(all_=(80, 200)),
     eq={"nmol/l": 65.1, "ng/ml": 100.0})
_add("t4", "T4", ["thyroxine", "t4 total", "total t4", "thyroxine total"], "µg/dL", _r(all_=(5, 12)),
     eq={"nmol/l": lambda v: v / 12.87})
_add("free_t3", "Free T3", ["ft3", "t3 free", "free triiodothyronine"], "pg/mL", _r(all_=(2.3, 4.2)),
     eq={"pmol/l": 0.651})
_add("free_t4", "Free T4", ["ft4", "t4 free", "free thyroxine"], "ng/dL", _r(all_=(0.8, 1.8)),
     eq={"pmol/l": lambda v: v / 12.87})
_add("testosterone", "Testosterone", ["total testosterone"], "ng/dL", _r(M=(300, 1000), F=(15, 70)),
     eq={"nmol/l": 28.84})
_add("cortisol", "Cortisol", [], "µg/dL", _r(all_=(6, 23)), eq={"nmol/l": lambda v: v / 27.59})
_add("prolactin", "Prolactin", [], "ng/mL", _r(M=(2, 18), F=(2, 29)))
_add("lh", "LH", ["luteinizing hormone"], "mIU/mL", _r(all_=(1.7, 8.6)))
_add("fsh", "FSH", ["follicle stimulating hormone"], "mIU/mL", _r(all_=(1.5, 12.4)))
_add("psa", "PSA", ["prostate specific antigen"], "ng/mL", _r(all_=(0, 4.0)))

# --- iron / vitamins ---------------------------------------------------------
_add("iron", "Iron", ["serum iron", "fe"], "µg/dL", _r(all_=(60, 170)), eq={"umol/l": 5.587})
_add("tibc", "TIBC", ["total iron binding capacity", "iron binding capacity"], "µg/dL", _r(all_=(250, 370)),
     eq={"umol/l": 5.587})
_add("transferrin_saturation", "Transferrin Saturation", ["tsat", "transferrin sat"], "%", _r(all_=(20, 50)))
_add("ferritin", "Ferritin", ["serum ferritin"], "ng/mL", _r(M=(24, 200), F=(11, 150)))
_add("vitamin_b12", "Vitamin B12", ["b12", "cobalamin", "cyanocobalamin", "vit b12"], "pg/mL", _r(all_=(200, 900)),
     eq={"pmol/l": 1.355, "ng/l": 1.0})
_add("folate", "Folate", ["folic acid", "serum folate"], "ng/mL", _r(all_=(2.7, 17.0)),
     eq={"nmol/l": lambda v: v / 2.266})
_add("vitamin_d", "Vitamin D", ["25 oh vitamin d", "25 hydroxy vitamin d", "vitamin d 25 oh", "25(oh) vitamin d"],
     "ng/mL", _r(all_=(30, 100)), eq={"nmol/l": lambda v: v / 2.496})

# --- inflammation / coagulation / cardiac ------------------------------------
_add("crp", "CRP", ["c reactive protein", "c-reactive protein"], "mg/L", _r(all_=(0, 10)), eq={"mg/dl": 10.0})
_add("hs_crp", "hs-CRP", ["hs crp", "high sensitivity crp", "hscrp"], "mg/L", _r(all_=(0, 3)), eq={"mg/dl": 10.0})
_add("procalcitonin", "Procalcitonin", ["pro calcitonin"], "ng/mL", _r(all_=(0, 0.1)), eq={"ug/l": 1.0})
_add("plateletcrit", "Plateletcrit", ["platelet crit"], "%", _r(all_=(0.19, 0.39)))
_add("pct_ambiguous", "PCT", [], "", {}, generic=True)
_add("pt", "PT", ["prothrombin time", "pt sec"], "sec", _r(all_=(11, 13.5)))
_add("inr", "INR", ["pt inr", "international normalized ratio", "prothrombin time inr",
                    "pt international normalized ratio", "inr value", "prothrombin inr"], "", _r(all_=(0.8, 1.1)),
     crit=[_crit("above", 3.0, "Severe bleeding risk - medication overdose"),
           _crit("below", 0.5, "Critically low INR - hypercoagulable state", priority=2)])
_add("aptt", "aPTT", ["activated partial thromboplastin time", "ptt", "apt time"], "sec", _r(all_=(25, 35)))
_add("d_dimer", "D-Dimer", ["d dimer", "ddimer"], "µg/mL", _r(all_=(0, 0.5)),
     eq={"ng/ml": 0.001, "mg/l": 1.0})
_TROP_EQ = {"ng/l": 0.001, "pg/ml": 0.001}
_add("troponin_i", "Troponin I", ["troponin", "hs troponin i", "troponin i hs", "cardiac troponin i", "trop i"],
     "ng/mL", _r(all_=(0, 0.04)), eq=dict(_TROP_EQ),
     crit=[_crit("above", 0.04, "Myocardial infarction - EMERGENCY", cutoff=True)])
_add("troponin_t", "Troponin T", ["cardiac troponin t", "trop t"], "ng/mL",
     _r(all_=(0, 0.01)), eq=dict(_TROP_EQ),
     crit=[_crit("above", 0.01, "Myocardial injury - cardiac emergency", cutoff=True)])
# high-sensitivity troponin T is reported in ng/L with a 99th-percentile cut-off of 14 ng/L: the conventional
# 0.01 ng/mL (= 10 ng/L) critical limit above would call a normal hs-TnT of 12 ng/L critical.
_add("hs_troponin_t", "hs Troponin T", ["hstnt"], "ng/L", _r(all_=(0, 14)),
     eq={"ng/ml": 1000.0, "pg/ml": 1.0, "ug/l": 1000.0},
     crit=[_crit("above", 100, "Markedly raised high-sensitivity troponin T - possible myocardial infarction")])
_add("hs_troponin", "hs Troponin", ["hs troponin", "high sensitivity troponin"], "ng/L", _r(all_=(0, 14)),
     eq={"ng/ml": 1000.0},
     crit=[_crit("above", 100, "Markedly raised high-sensitivity troponin - possible myocardial infarction")])
_add("ck", "CK", ["creatine kinase", "cpk", "ck total"], "U/L", _r(all_=(30, 200)))
_add("ck_mb", "CK-MB", ["ck mb", "creatine kinase mb"], "U/L", _r(all_=(0, 25)))
_add("ldh", "LDH", ["lactate dehydrogenase"], "U/L", _r(all_=(140, 280)))
_add("amylase", "Amylase", [], "U/L", _r(all_=(28, 100)))
_add("lipase", "Lipase", [], "U/L", _r(all_=(13, 60)))
_add("bnp", "BNP", ["b type natriuretic peptide"], "pg/mL", _r(all_=(0, 100)))
_add("nt_probnp", "NT-proBNP", ["nt probnp"], "pg/mL", _r(all_=(0, 125)))

# --- misc --------------------------------------------------------------------
# A bare "pH" is NOT a blood pH: urine pH (4.6-8.0) is printed on every urine routine. Only a name that says
# blood/arterial/venous/ABG (canonical token "bloodph"), a blood-gas context or a blood-range printed interval
# makes it the blood pH that carries the 7.2 / 7.6 critical limits (see evaluate._resolve_generic).
_add("blood_ph", "Blood pH", ["bloodph"], "", _r(all_=(7.35, 7.45)),
     crit=[_crit("below", 7.2, "Severe acidosis - life-threatening"),
           _crit("above", 7.6, "Severe alkalosis - life-threatening")], sanity_ratio=(0.9, 1.1))
_add("ph_unspecified", "pH", ["ph"], "", {}, generic=True)
_add("pco2", "pCO2", ["pco2 arterial"], "mmHg", _r(all_=(35, 45)))
_add("po2", "pO2", ["po2 arterial"], "mmHg", _r(all_=(80, 100)))
_add("base_excess", "Base Excess", ["be"], "mmol/L", _r(all_=(-2, 2)), allow_negative=True)
_add("hb_a", "Hb A", [], "%", _r(all_=(96.8, 97.8)))
_add("hb_a2", "Hb A2", [], "%", _r(all_=(2.2, 3.2)))
_add("hb_f", "Hb F", ["fetal hb", "fetal hemoglobin", "foetal hb"], "%", _r(all_=(0, 1.0)))
_add("hb_s", "Hb S", [], "%", _r(all_=(0, 0)))
_add("ige", "IgE", [], "IU/mL", _r(all_=(0, 87)))
_add("igg", "IgG", [], "mg/dL", _r(all_=(700, 1600)))
_add("igm", "IgM", [], "mg/dL", _r(all_=(40, 230)))
_add("cd4", "CD4", ["cd4 count"], "/µL", _r(all_=(500, 1500)),
     crit=[_crit("below", 200, "Advanced HIV Disease - opportunistic infection risk")])
_add("specific_gravity", "Specific Gravity", [], "", _r(all_=(1.005, 1.030)))
# urine microscopy counts: keys start with "urine_" so the pipeline never mistakes a urine-only report for a blood one
_add("urine_pus_cells", "Urine Pus Cells", ["pus cells", "pus cell", "urine wbc", "wbc urine", "urine white cells",
                                             "urine leukocytes", "pus cells urine"], "/hpf", _r(all_=(0, 5)))
_add("urine_rbc", "Urine RBC", ["urine rbc", "rbc urine", "urine red cells", "urine red blood cells", "urine erythrocytes"],
     "/hpf", _r(all_=(0, 2)))
_add("urine_epithelial", "Urine Epithelial Cells", ["epithelial cells", "epithelial cell", "squamous epithelial cells",
                                                    "squamous cells", "urine epithelial cells"], "/hpf", _r(all_=(0, 5)))
_add("urine_ph", "Urine pH", ["urineph"], "", _r(all_=(4.6, 8.0)))
_add("apri", "APRI", [], "", _r(all_=(0, 1.0)),
     crit=[_crit("above", 2.0, "Cirrhosis likely (APRI score) - hepatology referral", priority=2)])


# ── lookup ───────────────────────────────────────────────────────────────────

_INDEX: Dict[FrozenSet[str], Analyte] = {}
_COLLISIONS: List[Tuple[FrozenSet[str], str, str]] = []
for _a_ in REGISTRY.values():
    for _syn in _a_.synonyms:
        _t = canon_tokens(_syn)
        if not _t:
            continue
        _prev = _INDEX.get(_t)
        if _prev is not None and _prev.key != _a_.key:
            _COLLISIONS.append((_t, _prev.key, _a_.key))
            continue
        _INDEX[_t] = _a_

# `total X` -> X's analyte, ONLY for these X.
_TOTAL_OK = {
    frozenset({"cholesterol"}): "cholesterol", frozenset({"bilirubin"}): "bilirubin_total",
    frozenset({"wbc"}): "wbc", frozenset({"protein"}): "total_protein", frozenset({"calcium"}): "calcium",
}


def _match_exact(name: str) -> Optional[Analyte]:
    tokens = canon_tokens(name)
    if not tokens:
        return None
    hit = _INDEX.get(tokens)
    if hit is not None:
        return hit
    if "total" in tokens:
        key = _TOTAL_OK.get(tokens - {"total"})
        if key:
            return REGISTRY[key]
    return None


_METHOD_WORDS = re.compile(
    r"method|meth\b|:|ifcc|jaffe|\bgod\b|\bpod\b|\bise\b|enzymatic|colorimetric|calculated|\bcalc\b|photometric|"
    r"spectro|hplc|elisa|clia|cmia|eclia|immunoassay|turbidimetr|nephelometr|electrode|trinder|biuret|\bbcg\b|"
    r"\bbcp\b|oxidase|kinetic|\buv\b|\bdpd\b|\bpnp\b|impedance|flow cytometry|automated|analy[sz]er|hexokinase|"
    r"cyanmeth|\bsls\b|\becl\b|chemilumin|reflectance|immunoturbidi|\blatex|agglutination|ion selective|"
    r"\bckd-?epi\b|\bmdrd\b|friedewald|\bgopod\b|\bgod-?pod\b|generation|\bgen\b|assay|ultra|sensitive|"
    r"\bcontrol\b|\brapid\b|\bmanual\b|quantitative|qualitative|\blatex\b|\bcard\b|\bstrip\b",
    re.I)


_QUALIFIER_ABBREV = {"f": "fasting", "r": "random", "t": "total", "d": "direct", "i": "indirect", "pp": "pp",
                     "tot": "total", "dir": "direct", "ind": "indirect"}


def _inner_explained(inner: str, outer_tokens: FrozenSet[str], found: set) -> bool:
    """Is a parenthetical a method note / a repeat of the outer name / an abbreviation of the analyte?
    (An unexplained qualifier -- "Bilirubin (Direct)", "Cholesterol (HDL)" -- must never be dropped.)"""
    if _METHOD_WORDS.search(inner):
        return True
    a = _match_exact(inner)
    if a is not None:
        found.add(a.key)
        return True
    tokens = canon_tokens(inner)
    if tokens and tokens <= outer_tokens:
        return True
    plain = re.sub(r"[^a-z]", "", inner.lower())
    return plain in _QUALIFIER_ABBREV and _QUALIFIER_ABBREV[plain] in outer_tokens


def _match_single(name: str) -> Optional[Analyte]:
    hit = _match_exact(name)
    if hit is not None:
        return hit
    text = str(name or "")
    inners = re.findall(r"\(([^)]*)\)", text)
    if not inners:
        return None
    outer_text = re.sub(r"\([^)]*\)?", " ", text)
    if len(inners) == 1:
        # "Glucose (F)", "Bilirubin (T)", "Glucose (PP)": a one-letter qualifier abbreviation is spelled out and
        # the whole must then be an exact analyte name ("Hemoglobin (F)" stays unmatched)
        plain = re.sub(r"[^a-z]", "", inners[0].lower())
        word = _QUALIFIER_ABBREV.get(plain)
        if word and word not in canon_tokens(outer_text):
            spelled = _match_exact(f"{outer_text} {word}")
            if spelled is not None:
                return spelled
    outer_tokens = canon_tokens(outer_text)
    found: set = set()
    for inner in inners:
        if not _inner_explained(inner, outer_tokens, found):
            return None  # an unexplained qualifier: do not guess
    outer_hit = _match_exact(outer_text) or _match_exact(re.split(r"\s[-\u2013]\s", outer_text)[0])
    if outer_hit is not None:
        found.add(outer_hit.key)
    if len(found) > 1:
        # a generic abbreviation ("(pH)") next to a specific name ("Reaction") is not a conflict
        specific = {k for k in found if not REGISTRY[k].generic}
        if len(specific) == 1:
            found = specific
    if len(found) == 1:
        return REGISTRY[next(iter(found))]
    return None


def match_analyte(name: str) -> Optional[Analyte]:
    """EXACT canonical-token-set match against the registry. No substrings.

    A printed name that does not match as written is retried once with its
    parentheticals handled: a method note "(Method: GOD-POD)", "(Jaffe)" is
    ignored, and an abbreviation "(ALT)" / "(Na+)" / "(F)" may identify the
    analyte; but a qualifier in brackets ("Bilirubin (Direct)", "Cholesterol
    (HDL)") is never dropped: if the bracket is neither a method note, a repeat
    of the name nor an exact analyte name, the row stays unmatched. Alternative
    names separated by ";" ("VITAMIN B12; CYANOCOBALAMIN, SERUM") must all agree."""
    hit = _match_single(name)
    if hit is not None:
        return hit
    parts = [p.strip() for p in str(name or "").split(";") if p.strip()]
    if len(parts) > 1:
        hits = [_match_single(p) for p in parts]
        if all(h is not None for h in hits) and len({h.key for h in hits}) == 1:
            return hits[0]
    return None


def get_analyte(key: str) -> Optional[Analyte]:
    return REGISTRY.get(key)


# analyte key -> the names clinical_reference / the XGBoost feature set use.
ANALYTE_TO_LEGACY_KEY: Dict[str, Tuple[str, ...]] = {
    "hemoglobin": ("hemoglobin",), "wbc": ("wbc",), "platelets": ("platelets",), "mcv": ("mcv",), "rdw": ("rdw",),
    "rbc": ("rbc",), "hematocrit": ("hematocrit",), "esr": ("esr",),
    "neutrophils_pct": ("neutrophils",), "lymphocytes_pct": ("lymphocytes",), "eosinophils_pct": ("eosinophils",),
    "monocytes_pct": ("monocytes",),
    "glucose_fasting": ("glucose", "fbs"), "glucose_random": ("glucose",), "glucose_unspecified": ("glucose",),
    "glucose_pp": ("glucose",),
    "hba1c": ("hba1c",), "cholesterol": ("cholesterol",), "ldl": ("ldl",), "hdl": ("hdl",),
    "triglycerides": ("triglycerides",), "bilirubin_total": ("bilirubin",),
    "alt": ("sgpt", "alt"), "ast": ("sgot", "ast"), "alp": ("alp",), "albumin": ("albumin",),
    "creatinine": ("creatinine",), "urea": ("urea",), "bun": ("bun",), "uric_acid": ("uric_acid",),
    "sodium": ("sodium",), "potassium": ("potassium",), "chloride": ("chloride",), "bicarbonate": ("bicarbonate",),
    "calcium": ("calcium",), "magnesium": ("magnesium",), "phosphorus": ("phosphorus",),
    "tsh": ("tsh",), "t3": ("t3",), "t4": ("t4",), "vitamin_d": ("vitamin_d",), "vitamin_b12": ("vitamin_b12",),
    "iron": ("iron",), "ferritin": ("ferritin",), "crp": ("crp",), "hs_crp": ("hs_crp",),
    "procalcitonin": ("procalcitonin",), "inr": ("inr",), "troponin_i": ("troponin_i",),
    "ck_mb": ("ck_mb",), "cd4": ("cd4",), "apri": ("apri",), "ige": ("ige",), "egfr": ("egfr",),
}
