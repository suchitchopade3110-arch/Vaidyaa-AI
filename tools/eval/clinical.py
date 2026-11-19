"""Ground-truth derivation rules and the clinical rules oracle scorer (P1 plan sections 3.8, 4.2).

Pure functions. GT-side/judge-side module: imports nothing from ``app``. Every flag is
derived from the page (printed range, input demographics) or from the truth (true unit,
true sex and age); nothing here reads engine output.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from tools.eval import critical_table as ct
from tools.eval import units
from tools.eval.catalogue import CATALOGUE, COUNT_KEYS, Rng

NEGATIVE_TEXTS = frozenset({"nil", "negative", "absent", "not detected", "non reactive", "nonreactive", "normal", "neg"})
POSITIVE_TEXTS = frozenset({"trace", "present", "positive", "reactive", "detected", "pos"})
_GRADE = re.compile(r"^(?:[1-4]\+|\++)$")


@dataclass(frozen=True)
class OptionRange:
    """One labelled alternative of a sex- or age-labelled printed range."""

    label: str
    low: float | None
    high: float | None
    kind: str = "two_sided"  # two_sided | upper | lower
    sex: str | None = None  # 'M' | 'F'
    age_min: int | None = None
    age_max: int | None = None


@dataclass(frozen=True)
class PrintedRange:
    kind: str = "none"  # two_sided upper lower sex age interpretive cutoff none
    low: float | None = None
    high: float | None = None
    options: tuple[OptionRange, ...] = ()
    text: str = ""


@dataclass
class RowTruth:
    flag: str
    flag_true: str
    is_critical: bool
    critical_rule: str | None
    ambiguity: list[str] = field(default_factory=list)
    effective_range_page: dict[str, Any] | None = None
    effective_range_true: dict[str, Any] | None = None
    canonical: dict[str, Any] | None = None

    @property
    def expected_needs_review(self) -> bool:
        return bool(self.ambiguity)


# ---------------------------------------------------------------- qualitative / titre

def qualitative_class(text: str) -> str:
    """NEGATIVE or POSITIVE for a qualitative result string (section 4.2.2)."""
    t = re.sub(r"[-_]+", " ", text.strip().lower())
    t = re.sub(r"\s+", " ", t)
    if t in NEGATIVE_TEXTS:
        return "NEGATIVE"
    if t in POSITIVE_TEXTS or _GRADE.match(t):
        return "POSITIVE"
    raise ValueError(f"unclassified qualitative result {text!r}")


def titre_ratio(text: str) -> int:
    m = re.match(r"^\s*1\s*:\s*(\d+)\s*$", text)
    if not m:
        raise ValueError(f"bad titre {text!r}")
    return int(m.group(1))


# ---------------------------------------------------------------- numeric comparison

def _rng_dict(r: Rng | None) -> dict[str, Any] | None:
    return None if r is None else {"kind": r.kind, "low": r.low, "high": r.high}


def _region_point(r: Rng, v: float) -> str:
    if r.kind == "two_sided":
        return "LOW" if v < r.low else "HIGH" if v > r.high else "NORMAL"
    if r.kind == "upper":
        return "NORMAL" if v < r.high else "HIGH"
    return "NORMAL" if v > r.low else "LOW"  # lower


def region(r: Rng, value: float, qualifier: str | None = None) -> str:
    """LOW / NORMAL / HIGH / UNKNOWN of a (possibly qualified) value against a range.

    A qualified value is an interval; it must lie wholly inside one region, else UNKNOWN.
    """
    if not qualifier:
        return _region_point(r, value)
    if qualifier in ("<", "<="):
        ends = [_region_point(r, 0.0), _region_point(r, value)]
    elif qualifier in (">", ">="):
        top = "HIGH" if r.kind != "lower" else "NORMAL"
        ends = [_region_point(r, value), top]
    else:
        raise ValueError(f"unknown qualifier {qualifier!r}")
    return ends[0] if ends[0] == ends[1] else "UNKNOWN"


def _option_rng(o: OptionRange) -> Rng:
    return Rng(o.kind, o.low, o.high)


def _consensus_for(key: str, sex: str | None) -> Rng | None:
    v = CATALOGUE[key].consensus
    if not v.sex_specific:
        return v.unisex
    return v.male if sex == "M" else v.female if sex == "F" else None


def _norm_sex(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip().upper()
    return "M" if s.startswith("M") else "F" if s.startswith("F") else None


def effective_range(
    key: str, printed: PrintedRange, sex: str | None, age: int | None, value: float, qualifier: str | None = None,
) -> tuple[Rng | None, str, str | None]:
    """Section 4.2.1. Returns (effective range or None, region, ambiguity code).

    ``region`` is LOW/NORMAL/HIGH/UNKNOWN. When the range is null but every option agrees on the
    region the value falls in, the region is determinable (not ambiguous) and the range stays null.
    """
    sex = _norm_sex(sex)
    k = printed.kind
    if k in ("two_sided", "upper", "lower"):
        r = Rng(k, printed.low, printed.high)
        return r, region(r, value, qualifier), None
    if k in ("sex", "age"):
        opts = printed.options
        if k == "sex" and sex is not None:
            hit = [o for o in opts if o.sex == sex]
        elif k == "age" and age is not None:
            hit = [o for o in opts if (o.age_min is None or age >= o.age_min) and (o.age_max is None or age <= o.age_max)]
        else:
            hit = []
        if hit:
            r = _option_rng(hit[0])
            return r, region(r, value, qualifier), None
        regs = {region(_option_rng(o), value, qualifier) for o in opts}
        if len(regs) == 1 and "UNKNOWN" not in regs:
            return None, regs.pop(), None
        return None, "UNKNOWN", "AMB_SEX_RANGES_NO_SEX" if k == "sex" else "AMB_AGE_RANGES_NO_AGE"
    # no usable printed range: consensus interval (interpretive tables and range-absent rows)
    a = CATALOGUE[key]
    if a.consensus.sex_specific and sex is None:
        regs = set()
        for r in (a.consensus.male, a.consensus.female):
            if r is not None:
                regs.add(region(r, value, qualifier))
        if len(regs) == 1 and "UNKNOWN" not in regs:
            return None, regs.pop(), None
        return None, "UNKNOWN", "AMB_NO_RANGE_NO_DEMOGRAPHICS"
    r = _consensus_for(key, sex)
    if r is None:
        return None, "UNKNOWN", "AMB_NO_RANGE_NO_DEMOGRAPHICS"
    return None, region(r, value, qualifier), None


def _true_region(key, printed, true_sex, true_age, value, qualifier) -> tuple[Rng | None, str]:
    r, reg, amb = effective_range(key, printed, true_sex, true_age, value, qualifier)
    if reg == "UNKNOWN":  # cannot happen with a known sex/age; fall back to the plain value
        r, reg, _ = effective_range(key, printed, true_sex, true_age, value, None)
    if r is None and printed.kind in ("interpretive", "none", "sex", "age") and reg != "UNKNOWN":
        # determinable regardless of which option applies; report the range the true patient has
        if printed.kind in ("sex", "age"):
            hit = [o for o in printed.options if (printed.kind == "sex" and o.sex == _norm_sex(true_sex))
                   or (printed.kind == "age" and true_age is not None
                       and (o.age_min is None or true_age >= o.age_min) and (o.age_max is None or true_age <= o.age_max))]
            r = _option_rng(hit[0]) if hit else None
        else:
            r = _consensus_for(key, _norm_sex(true_sex))
    return r, reg


def _dir_of(flag: str) -> str:
    return {"HIGH": "high", "CRITICAL_HIGH": "high", "LOW": "low", "CRITICAL_LOW": "low"}.get(flag, "normal")


def printed_flag_direction(pf: str) -> str | None:
    p = pf.strip().upper()
    if p in ("H", "HH"):
        return "high"
    if p in ("L", "LL"):
        return "low"
    if p in ("N", "NORMAL"):
        return "normal"
    return None


def _crit_unit(key: str, unit: str) -> str:
    return unit if unit else CATALOGUE[key].unit


def derive_row(
    key: str,
    *,
    value: float | None = None,
    qualifier: str | None = None,
    unit: str = "",
    unit_true: str | None = None,
    text_value: str | None = None,
    specimen: str | None = None,
    printed_range: PrintedRange | None = None,
    printed_flag: str = "",
    input_sex: str | None = None,
    input_age: int | None = None,
    true_sex: str | None = None,
    true_age: int | None = None,
    unlabelled_multivalue: bool = False,
    titre_cutoff: int | None = None,
) -> RowTruth:
    """Derive page flag, true flag, criticality and ambiguity codes for one row (sections 3.8, 4.2)."""
    a = CATALOGUE[key]
    specimen = specimen or a.specimen
    printed = printed_range or PrintedRange()
    unit_true = unit if unit_true is None else unit_true
    vt = a.value_type
    amb: list[str] = []

    if vt == "info":
        return RowTruth("INFO", "INFO", False, None)
    if vt == "qualitative":
        cls = qualitative_class(text_value or "")
        return RowTruth(cls, cls, False, None)
    if vt == "titre":
        ratio = titre_ratio(text_value or "")
        if titre_cutoff is not None:
            f = "POSITIVE" if ratio >= titre_cutoff else "NEGATIVE"
            return RowTruth(f, f, False, None)
        ft = "POSITIVE" if ratio >= 160 else "NEGATIVE"
        return RowTruth("UNKNOWN", ft, False, None, ["AMB_TITRE_NO_CUTOFF"])
    if vt == "range_result":
        nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text_value or "")]
        if not nums:  # "Nil" / "Absent"
            nums = [0.0]
        r = a.consensus.unisex if printed.kind in ("none", "interpretive") else Rng("two_sided", printed.low, printed.high)
        f = "HIGH" if min(nums) > r.high else "LOW" if max(nums) < r.low else "NORMAL"
        return RowTruth(f, f, False, None, effective_range_page=_rng_dict(r), effective_range_true=_rng_dict(r))

    assert value is not None
    # ---- truth side
    ts = _norm_sex(true_sex if true_sex is not None else input_sex)
    ta = true_age if true_age is not None else input_age
    cu_true, _ = units.to_canonical(key, value, _crit_unit(key, unit_true))
    crit_true = ct.critical_direction(key, cu_true, specimen)
    r_true, reg_true = _true_region(key, printed, ts, ta, value, qualifier)
    if crit_true:
        flag_true = "CRITICAL_" + crit_true.upper()
    else:
        flag_true = reg_true if reg_true != "UNKNOWN" else "NORMAL"
    rule = ct.critical_rule(key, cu_true) if crit_true else None

    # ---- page side
    r_page, reg_page, code = effective_range(key, printed, input_sex, input_age, value, qualifier)
    unitless = key in COUNT_KEYS and unit == "" and printed.kind == "none"
    crit_page = None
    if not unitless:
        cu_page, _ = units.to_canonical(key, value, _crit_unit(key, unit if unit else unit_true))
        crit_page = ct.critical_direction(key, cu_page, specimen)
    if unitless:
        flag = "UNKNOWN"
        amb.append("AMB_UNITLESS_COUNT")
    elif unlabelled_multivalue:
        flag = "UNKNOWN"
        amb.append("AMB_UNLABELLED_MULTIVALUE")
    elif crit_page:
        flag = "CRITICAL_" + crit_page.upper()
    elif reg_page == "UNKNOWN":
        flag = "UNKNOWN"
        if code:
            amb.append(code)
    else:
        flag = reg_page
    # printed flag contradicting value-versus-range (a lab error; the value is right)
    pd = printed_flag_direction(printed_flag) if printed_flag else None
    if pd is not None and not unitless and flag != "UNKNOWN" and pd != _dir_of(flag):
        amb.append("AMB_PRINTED_FLAG_CONFLICT")
    return RowTruth(
        flag, flag_true, crit_true is not None, rule, amb, _rng_dict(r_page), _rng_dict(r_true),
        {"value": cu_true, "unit": a.unit},
    )


# ---------------------------------------------------------------- headline

def headline(rows: list[tuple[bool, str]]) -> str:
    """Section 4.2.5 over (is_critical, flag_true) pairs: critical > abnormal > normal."""
    if any(c for c, _ in rows):
        return "critical"
    if any(f in ("HIGH", "LOW", "POSITIVE", "CRITICAL_HIGH", "CRITICAL_LOW") for _, f in rows):
        return "abnormal"
    return "normal"


# ---------------------------------------------------------------- oracle scorer (tests only)

@dataclass
class OracleScore:
    headline: str
    row_flags: list[str]
    label_raw: str = ""


class ClinicalRulesScorer:
    """Oracle: scores confirmed rows with the GT rules. Duck-typed rows (value_num, unit_raw, ref_low,
    ref_high, qualifier, text_value, analyte_key, specimen). Never used by the product."""

    name = "clinical_rules"
    version = "1"

    def score(self, rows: list[Any], sex: str | None = None, age: int | None = None) -> OracleScore:
        flags: list[str] = []
        pairs: list[tuple[bool, str]] = []
        unresolved = False
        for r in rows:
            key = getattr(r, "analyte_key", None)
            if key is None or key not in CATALOGUE:
                flags.append("UNKNOWN")
                unresolved = True
                continue
            lo, hi = getattr(r, "ref_low", None), getattr(r, "ref_high", None)
            kind = "two_sided" if lo is not None and hi is not None else "lower" if lo is not None else \
                "upper" if hi is not None else "none"
            specimen = getattr(r, "specimen", None)
            specimen = specimen if specimen in ("serum", "urine", "csf", "fluid", "other") else None
            try:
                t = derive_row(
                    key, value=getattr(r, "value_num", None), qualifier=getattr(r, "qualifier", None) or None,
                    unit=getattr(r, "unit_raw", "") or "", text_value=getattr(r, "text_value", None),
                    specimen=specimen, printed_range=PrintedRange(kind, lo, hi),
                    printed_flag=getattr(r, "printed_flag", "") or "", input_sex=sex, input_age=age,
                )
            except (ValueError, KeyError, AssertionError, TypeError):
                flags.append("UNKNOWN")
                unresolved = True
                continue
            flags.append(t.flag)
            if t.flag == "UNKNOWN":
                unresolved = True
            pairs.append((t.flag.startswith("CRITICAL"), t.flag))
        if any(c for c, _ in pairs):
            h = "critical"
        elif any(f in ("HIGH", "LOW", "POSITIVE") for _, f in pairs):
            h = "abnormal"
        elif unresolved:
            h = "unknown"
        else:
            h = "normal"
        return OracleScore(h, flags)


# ---------------------------------------------------------------- eGFR (CKD-EPI 2021)

def ckd_epi_2021(creatinine_mg_dl: float, age: int, sex: str) -> float:
    female = _norm_sex(sex) == "F"
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    ratio = creatinine_mg_dl / kappa
    egfr = 142 * min(ratio, 1) ** alpha * max(ratio, 1) ** -1.200 * 0.9938 ** age
    return egfr * 1.012 if female else egfr


def nearly(a: float, b: float, rel: float = 1e-9) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-12)
