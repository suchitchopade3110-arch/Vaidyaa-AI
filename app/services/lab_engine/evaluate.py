"""Range precedence, flags, critical thresholds, anomalies and the risk floor.

The rules (see lab_engine_plan.md section 4):
  * a range printed on the report always wins; the registry range is only
    used when the report printed none AND the analyte's sex/age needs are met;
    the engine never assumes a sex or an age;
  * a value that cannot be placed against a range is UNKNOWN with a warning,
    never NORMAL;
  * critical thresholds are evaluated on the canonical analyte key and the
    canonical unit for EVERY row, independent of how the report names it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from .analytes import REGISTRY, Analyte, canon_tokens, match_analyte
from .models import NON_ANOMALY_FLAGS, LabAnalysis, ParsedRow, ScoredRow
from .tokenizer import qualitative_class
from .units import canonical_critical, fmt_num, resolve_unit, unit_key, units_equivalent

_PRINTED_DIR = {"H": "HIGH", "HH": "HIGH", "L": "LOW", "LL": "LOW"}


def normalize_sex(sex: Any) -> Optional[str]:
    if sex is None:
        return None
    text = str(sex).strip().lower()
    if text in ("m", "male", "man", "boy", "1"):
        return "M"
    if text in ("f", "female", "woman", "girl", "0"):
        return "F"
    return None


def normalize_age(age: Any) -> Optional[int]:
    try:
        n = int(float(age))
    except (TypeError, ValueError):
        return None
    return n if 0 <= n <= 120 else None


def _r6(x: Optional[float]) -> Optional[float]:
    """Round converted numbers to 6 significant digits (conversion noise)."""
    return None if x is None else float(f"{x:.6g}")


def _fmt(x: Optional[float]) -> str:
    return "" if x is None else f"{x:.6g}"


def _fmt_display(x: Optional[float]) -> str:
    """Compact display of a converted bound (70.2624 -> 70.26, 0.67858 -> 0.679); the stored bound is exact."""
    if x is None:
        return ""
    return _fmt(round(x, 3) if abs(x) < 1 else round(x, 2))


def format_reference(lo: Optional[float], hi: Optional[float], strict_lo: bool = False, strict_hi: bool = False,
                     compact: bool = False) -> str:
    f = _fmt_display if compact else _fmt
    if lo is not None and hi is not None:
        return f"{f(lo)} - {f(hi)}"
    if hi is not None:
        return f"{'<' if strict_hi else '<='} {f(hi)}"
    if lo is not None:
        return f"{'>' if strict_lo else '>='} {f(lo)}"
    return "--"


# ── flag from value vs range ─────────────────────────────────────────────────

def _compare(value: float, qualifier: Optional[str], lo: Optional[float], hi: Optional[float],
             strict_lo: bool = False, strict_hi: bool = False) -> str:
    """HIGH | LOW | NORMAL | UNKNOWN. Qualified results ("<x", ">x") only decide
    when the answer is the same for every value the qualifier allows."""
    if lo is None and hi is None:
        return "UNKNOWN"
    if not qualifier:
        if lo is not None and (value < lo or (strict_lo and value <= lo)):
            return "LOW"
        if hi is not None and (value > hi or (strict_hi and value >= hi)):
            return "HIGH"
        return "NORMAL"
    if qualifier in ("<", "<="):
        strict = qualifier == "<"
        # true value is below `value` (or equal for <=)
        if lo is not None and (value < lo if not strict else value <= lo):
            return "LOW"
        # not below the range for certain; NORMAL only if everything from 0 up to `value` lies in range
        if (lo is None or lo <= 0) and (hi is None or value <= hi):
            if hi is not None and strict_hi and value > hi:
                return "UNKNOWN"
            return "NORMAL"
        return "UNKNOWN"
    if qualifier in (">", ">="):
        strict = qualifier == ">"
        if hi is not None and (value >= hi if strict else value > hi):
            return "HIGH"
        return "UNKNOWN"
    return "UNKNOWN"


def _critical_hit(analyte: Analyte, value: float, qualifier: Optional[str]):
    for c in analyte.criticals:
        if c.direction == "below":
            if qualifier in (">", ">="):
                continue
            if qualifier in ("<", "<="):
                # true value < value (or <=): critical for sure only if value is not above the limit
                hit = value <= c.limit if qualifier == "<" else value < c.limit
            else:
                hit = value <= c.limit if c.inclusive else value < c.limit
        else:
            if qualifier in ("<", "<="):
                continue
            if qualifier in (">", ">="):
                hit = value >= c.limit if qualifier == ">" else value > c.limit
            else:
                hit = value > c.limit
        if hit:
            return c
    return None


def _severity(flag: str, value: Optional[float], lo: Optional[float], hi: Optional[float], from_report_only: bool) -> Tuple[str, float]:
    if flag in ("CRITICAL_HIGH", "CRITICAL_LOW"):
        return "CRITICAL", 0.0
    if flag == "POSITIVE":
        return "HIGH", 0.0
    if flag not in ("HIGH", "LOW"):
        return "NONE", 0.0
    if value is None or from_report_only:
        return "MODERATE", 0.0
    if flag == "HIGH" and hi is not None:
        pct = round(((value - hi) / hi) * 100, 1) if hi > 0 else 100.0
    elif flag == "LOW" and lo is not None:
        pct = round(((lo - value) / lo) * 100, 1) if lo > 0 else 100.0
    else:
        return "MODERATE", 0.0
    if pct > 50:
        return "HIGH", pct
    if pct > 25:
        return "MODERATE", pct
    return "MILD", pct


# ── report context (specimen / panel evidence for ambiguous bare names) ─────────

_URINE_NAME_RE = re.compile(r"urine|urinary|pus cell|epithelial|\bcasts?\b|nitrite|leukocyte esterase|leucocyte esterase|"
                            r"urobilinogen|ketone|crystals|specific gravity|bacteria")
_BLOOD_GAS_RE = re.compile(r"blood gas|\babg\b|arterial|venous|capillary")
_CBC_KEYS = {"hemoglobin", "wbc", "platelets", "mcv", "mch", "mchc", "rdw", "mpv", "hematocrit", "rbc"}


@dataclass
class _Report:
    keys: Set[str] = field(default_factory=set)
    urine: bool = False
    blood_gas: bool = False
    cbc: bool = False


def _report_context(rows: List[ParsedRow]) -> _Report:
    ctx = _Report()
    sections = " | ".join((p.section or "").lower() for p in rows)
    names = " | ".join(p.name.lower() for p in rows)
    for p in rows:
        a = match_analyte(p.name)
        if a is not None:
            ctx.keys.add(a.key)
    ctx.urine = bool(_URINE_NAME_RE.search(sections) or _URINE_NAME_RE.search(names) or ctx.keys & {"specific_gravity", "urine_ph"})
    ctx.blood_gas = bool(_BLOOD_GAS_RE.search(sections) or ctx.keys & {"pco2", "po2", "base_excess"})
    ctx.cbc = bool(ctx.keys & _CBC_KEYS or re.search(r"blood count|\bcbc\b|haemogram|hemogram", sections))
    return ctx


def _resolve_generic(p: ParsedRow, analyte: Analyte, value: float, lo: Optional[float], hi: Optional[float],
                     ctx: _Report) -> Tuple[Optional[Analyte], str, str]:
    """A bare ambiguous name ("pH", "PCT") -> (analyte | None, note, warning). None + warning = cannot tell: UNKNOWN."""
    name = p.name
    if analyte.key == "ph_unspecified":
        sec = (p.section or "").lower()
        if "urine" in sec:
            return REGISTRY["urine_ph"], "specimen: urine (section heading)", ""
        if _BLOOD_GAS_RE.search(sec):
            return REGISTRY["blood_ph"], "specimen: blood (section heading)", ""
        if ctx.blood_gas and not ctx.urine:
            return REGISTRY["blood_ph"], "specimen: blood (blood-gas results on the report)", ""
        if ctx.urine and not ctx.blood_gas:
            return REGISTRY["urine_ph"], "specimen: urine (urine results on the report)", ""
        if lo is not None and hi is not None:
            if 6.8 <= lo and hi <= 7.8 and hi - lo <= 1.0:
                return REGISTRY["blood_ph"], "specimen: blood (printed range is a blood pH range)", ""
            return REGISTRY["urine_ph"], "specimen: urine (printed range is not a blood pH range)", ""
        if value < 6.8 or value > 7.8:
            return REGISTRY["urine_ph"], "assumed urine pH (outside the range possible for blood)", ""
        return None, "", (f"{name}: pH {fmt_num(value)} without a specimen (urine or blood-gas context) or range; "
                          "not scored (it would be a critical blood pH if the specimen is blood)"
                          if value < 7.2 or value > 7.6 else
                          f"{name}: pH {fmt_num(value)} without a specimen (urine or blood-gas context) or range; not scored")
    if analyte.key == "pct_ambiguous":
        ukey = unit_key(p.unit)
        if ukey == "%":
            return REGISTRY["plateletcrit"], "PCT read as plateletcrit (unit %)", ""
        if ukey in ("ng/ml", "ug/l"):
            return REGISTRY["procalcitonin"], "PCT read as procalcitonin (unit ng/mL)", ""
        if ctx.cbc:
            return REGISTRY["plateletcrit"], "PCT read as plateletcrit (complete blood count report)", ""
        return None, "", f"{name}: ambiguous abbreviation (procalcitonin or plateletcrit) with no unit or blood-count context; not scored"
    return analyte, "", ""


# ── qualitative results: which positives are abnormal ────────────────────────

_POSITIVE_ABNORMAL = {
    "hiv", "hbsag", "hbeag", "hcv", "hbv", "hav", "hev", "hepatitis", "vdrl", "rpr", "tpha", "syphilis", "treponema",
    "widal", "typhi", "typhidot", "brucella", "dengue", "ns1", "malaria", "plasmodium", "parasite", "parasites",
    "filaria", "microfilaria", "covid", "sars", "cov", "influenza", "h1n1", "rsv", "tb", "afb", "mantoux",
    "quantiferon", "igm", "igg", "iga", "ige", "antigen", "antibody", "antibodies", "anti", "ab", "ag", "ana", "anca",
    "dsdna", "rf", "rheumatoid", "aso", "pcr", "culture", "growth", "organism", "bacteria", "fungus", "candida",
    "pylori", "occult", "fobt", "ova", "cyst", "bence", "jones", "sickling", "sickle", "coombs", "cmv", "ebv", "hsv",
    "rubella", "toxoplasma", "chikungunya", "leptospira", "typhus", "helicobacter", "clostridium", "salmonella",
    "shigella", "vibrio", "cholera", "leishmania", "screen", "troponin", "rapid", "protein", "albumin", "glucose",
    "sugar", "ketone", "ketones", "blood", "bilirubin", "urobilinogen", "nitrite", "leukocyte", "leukocytes",
    "leucocyte", "leucocytes", "esterase", "cast", "casts", "crystal", "crystals", "pus", "rbc", "wbc", "yeast",
    "hemoglobin", "haemoglobin", "myoglobin", "chyle", "blast", "blasts", "mp", "ict", "tuberculosis", "gonorrhea",
    "chlamydia", "trichomonas", "pregnancy", "hcg", "antiglobulin", "dat", "hla", "b27", "hlab27", "gonococcus",
    "meningococcus", "pneumococcus", "legionella", "mycoplasma", "treponema", "brucella",
}
_INFO_ONLY_PHRASES = re.compile(r"blood group|\babo\b|\brh\b|rhesus|\bgroup\b|\bgrouping\b|genotype|phenotype")


def _positive_is_abnormal(name: str, text: str) -> bool:
    """Is a 'Positive'/'Present' result on this test an abnormal finding? Reactive / Detected always are. For
    'Positive' / 'Present' / '+' the test must be one where a positive is a finding (serology, antigens,
    antibodies, cultures, urine dipstick analytes ...): 'Blood Group O Positive' and 'Rh Factor Positive' are
    identity information."""
    t = (text or "").strip().lower()
    if t in ("reactive", "detected"):
        return True
    low = name.lower()
    words = set(re.findall(r"[a-z0-9]+", low))
    if _INFO_ONLY_PHRASES.search(low) and not (words & {"antibody", "antibodies", "coombs", "screen", "antigen"}):
        return False
    return bool(words & _POSITIVE_ABNORMAL)


_URINE_MICROSCOPY_REMAP = {"rbc": "urine_rbc", "wbc": "urine_pus_cells"}
_NEGATIVE_OK_NAME = re.compile(r"excess|deficit|delta|change|differ|gap|balance|slope|score|index|variation|shift|z-?score", re.I)


# ── scoring one row ──────────────────────────────────────────────────────────

def _unknown_row(p: ParsedRow, analyte: Optional[Analyte], value, unit, lo, hi, source, note, value_type,
                 reference_text="--") -> ScoredRow:
    return ScoredRow(parsed=p, analyte_key=analyte.key if analyte else None, display_name=p.name, value=value,
                     unit=unit, note=note, ref_low=lo, ref_high=hi, range_source=source, flag="UNKNOWN",
                     value_type=value_type, reference_text=reference_text, qualifier=p.qualifier,
                     text_value=p.text_value)


def _ambiguous_row(p: ParsedRow, warnings: List[str]) -> ScoredRow:
    """Several bare numbers before the unit and nothing that says which is the current result."""
    analyte = match_analyte(p.name)
    msg = f"{p.name}: {p.ambiguity}; not scored"
    hits = []
    for cand in p.candidates or []:
        if analyte is None or analyte.generic:
            break
        res = resolve_unit(analyte, p.unit, cand, p.ref_low, p.ref_high)
        if not res.ok:
            continue
        v = res.fn(cand) if res.fn else cand
        c = canonical_critical(analyte, v)
        if c is not None:
            hits.append(f"{fmt_num(cand)} would be CRITICAL ({c.message})")
    if hits:
        msg += "; WARNING: " + "; ".join(hits) + " - verify urgently"
    warnings.append(msg)
    ref_txt = p.ref_text or format_reference(p.ref_low, p.ref_high, p.ref_strict_low, p.ref_strict_high)
    row = _unknown_row(p, analyte, None, (p.unit or "").strip(), p.ref_low, p.ref_high,
                       "report" if (p.ref_low is not None or p.ref_high is not None) else "none",
                       p.ambiguity or "", "ambiguous", ref_txt)
    return row


def _score_row(p: ParsedRow, sex: Optional[str], age: Optional[int], warnings: List[str],
               ctx: Optional[_Report] = None) -> Optional[ScoredRow]:
    name = p.name
    ctx = ctx or _Report()
    warnings.extend(p.warnings)

    # ---- several candidate results, none chosen ------------------------------------------------------
    if p.value is None and p.candidates:
        return _ambiguous_row(p, warnings)

    # ---- qualitative result -------------------------------------------------
    if p.value is None:
        if p.text_value is None:
            return None
        cls = qualitative_class(p.text_value)
        flag = cls or "UNKNOWN"
        value_type = "qualitative"
        if cls == "POSITIVE" and not _positive_is_abnormal(name, p.text_value):
            flag, value_type = "INFO", "informational"
        elif cls == "NEGATIVE" and _INFO_ONLY_PHRASES.search(name.lower()) and not (
                set(re.findall(r"[a-z0-9]+", name.lower())) & _POSITIVE_ABNORMAL - {"blood"}):
            flag, value_type = "INFO", "informational"
        row = ScoredRow(parsed=p, analyte_key=None, display_name=name, value=None, unit="", note="",
                        ref_low=None, ref_high=None, range_source="none", flag=flag, value_type=value_type,
                        reference_text=p.ref_text or "--", text_value=p.text_value)
        if flag == "UNKNOWN":
            warnings.append(f"{name}: qualitative result '{p.text_value}' not understood")
        row.severity, _ = _severity(flag, None, None, None, True)
        if flag == "POSITIVE" and p.text_value.strip().lower() in ("trace", "+"):
            row.severity = "MODERATE"
        return row

    analyte = match_analyte(name)
    microscopy_unit = unit_key(p.unit) in ("/hpf", "/lpf") or p.result_range is not None
    if analyte is not None and microscopy_unit and analyte.key in _URINE_MICROSCOPY_REMAP:
        # "RBC 2-3 /hpf", "WBC 5 /hpf" in a urine routine are urine microscopy counts, not the blood count
        analyte = REGISTRY[_URINE_MICROSCOPY_REMAP[analyte.key]]
    value = p.value
    qualifier = p.qualifier
    lo, hi = p.ref_low, p.ref_high
    strict_lo, strict_hi = p.ref_strict_low, p.ref_strict_high
    notes: List[str] = [p.note] if p.note else []

    # ---- a negative concentration is not a result ------------------------------------------------------
    negative_ok = (analyte is not None and analyte.allow_negative) or bool(_NEGATIVE_OK_NAME.search(name)) \
        or (lo is not None and lo < 0) or bool(p.sex_ranges and any(v[0] is not None and v[0] < 0 for v in p.sex_ranges.values()))
    if value < 0 and not qualifier and not negative_ok:
        warnings.append(f"{name}: negative value {fmt_num(value)} is not physiologically possible; not scored")
        ref_txt = p.ref_text or format_reference(lo, hi, strict_lo, strict_hi)
        return _unknown_row(p, analyte, value, (p.unit or "").strip(), lo, hi,
                            "report" if (lo is not None or hi is not None) else "none",
                            "negative value is not physiologically possible", "invalid", ref_txt)

    # ---- printed range: sex-labelled, age-labelled, interpretation-only ---------------------------------
    printed_range_unusable = False
    if lo is None and hi is None and p.sex_ranges:
        if sex is not None and sex in p.sex_ranges:
            lo, hi = p.sex_ranges[sex]
        elif sex is None:
            warnings.append(f"{name}: the report prints sex-specific ranges and the patient's sex was not provided")
            printed_range_unusable = True
    if lo is None and hi is None and p.age_ranges and not printed_range_unusable:
        if age is None:
            warnings.append(f"{name}: the report prints age-specific ranges and the patient's age was not provided")
            printed_range_unusable = True
        else:
            band = next((b for b in p.age_ranges if b[0] <= age <= b[1]), None)
            if band is not None:
                lo, hi, strict_lo, strict_hi = band[2], band[3], band[4], band[5]
            else:
                warnings.append(f"{name}: none of the printed age-specific ranges covers age {age}")
                printed_range_unusable = True
    elif lo is None and hi is None and p.age_ranges and p.sex_ranges:
        printed_range_unusable = True
    if lo is None and hi is None and p.interpretive_only:
        warnings.append(f"{name}: the printed ranges are interpretation categories (e.g. deficient / borderline), "
                        "not a reference range")
        printed_range_unusable = True
    has_printed = lo is not None or hi is not None

    # ---- ambiguous bare names ("pH", "PCT") ---------------------------------------------------------------
    if analyte is not None and analyte.generic:
        resolved, why, warn = _resolve_generic(p, analyte, value, lo, hi, ctx)
        if resolved is None:
            warnings.append(warn)
            ref_txt = p.ref_text or format_reference(lo, hi, strict_lo, strict_hi)
            return _unknown_row(p, None, value, (p.unit or "").strip(), lo, hi, "report" if has_printed else "none",
                                "ambiguous name", "ambiguous", ref_txt)
        analyte = resolved
        if why:
            notes.append(why)

    if analyte is not None and analyte.sanity_ratio and has_printed:
        from .units import _range_matches  # noqa: PLC0415 - tiny private helper

        tol = analyte.sanity_ratio
        can_lo, can_hi = analyte.ranges["all"]
        ok = all(b is None or c is None or c == 0 or tol[0] <= b / c <= tol[1]
                 for b, c in ((lo, can_lo), (hi, can_hi)))
        if not ok:
            analyte = None  # same name, different specimen/scale (e.g. urine pH): trust only the report
            notes.append("printed range does not match the registry analyte; not matched")

    # sibling analyte (bare "Neutrophils 4500 /uL" is an absolute count, not a %)
    if analyte is not None and analyte.sibling and p.unit:
        first = resolve_unit(analyte, p.unit, value, lo, hi)
        if not first.ok:
            sib = REGISTRY.get(analyte.sibling)
            if sib is not None and resolve_unit(sib, p.unit, value, lo, hi).ok:
                analyte = sib

    # ---- units --------------------------------------------------------------
    unit_out = (p.unit or "").strip()
    fn = None
    value_c = value
    criticals_ok = True
    if analyte is not None:
        res = resolve_unit(analyte, p.unit, value, lo, hi)
        if not res.ok:
            same_unit_range = has_printed and (not p.ref_unit or unit_key(p.ref_unit) == unit_key(p.unit))
            if res.kind == "unknown" and same_unit_range:
                # the unit is not one the registry can convert, but the report printed the range in that very unit:
                # compare against the printed range as printed. Criticals need a known unit, so they are not evaluated.
                criticals_ok = False
                notes.append(f"unit {p.unit} is not convertible for {analyte.display}; scored against the printed range only")
                warnings.append(f"{name}: unit {p.unit} is not a known unit for {analyte.display}; scored against the "
                                "printed range only, critical thresholds not evaluated")
            else:
                warnings.append(f"{name}: {res.note}" if res.kind == "missing_bad" else
                                f"{name}: unit {p.unit} not recognised for {analyte.display}; not scored")
                ref_txt = p.ref_text or format_reference(lo, hi, strict_lo, strict_hi)
                return _unknown_row(p, analyte, value, unit_out, lo, hi, "report" if has_printed else "none",
                                    res.note, "unit_mismatch", ref_txt)
        else:
            fn = res.fn
            value_c = _r6(fn(value)) if fn else value
            unit_out = unit_out if res.keep_printed_unit else (res.unit or analyte.unit)
            if res.note:
                notes.append(res.note.replace("{value}", fmt_num(value)))
            if res.warn:
                warnings.append(f"{name}: {res.note}")
            # printed range: its own unit (if different) else the result's unit
            if has_printed:
                if p.ref_unit and not units_equivalent(p.ref_unit, p.unit or "", analyte.key):
                    rres = resolve_unit(analyte, p.ref_unit)
                    if rres.ok:
                        rfn = rres.fn
                        lo = _r6(rfn(lo)) if (rfn and lo is not None) else lo
                        hi = _r6(rfn(hi)) if (rfn and hi is not None) else hi
                    else:
                        warnings.append(f"{name}: reference range unit {p.ref_unit} not recognised; printed range ignored")
                        lo = hi = None
                        has_printed = False
                elif fn:
                    lo = _r6(fn(lo)) if lo is not None else None
                    hi = _r6(fn(hi)) if hi is not None else None
    else:
        if has_printed and p.ref_unit and p.unit and unit_key(p.ref_unit) != unit_key(p.unit) \
                and not units_equivalent(p.ref_unit, p.unit, name):
            warnings.append(f"{name}: reference range is in {p.ref_unit} but the result is in {p.unit}; printed range ignored")
            lo = hi = None
            has_printed = False

    # ---- range precedence ---------------------------------------------------
    range_source = "none"
    if has_printed:
        range_source = "report"
    elif analyte is not None:
        rng, reason = analyte.registry_range(sex, age)
        if rng is not None:
            lo, hi = rng
            strict_lo = strict_hi = False
            range_source = "registry"
            if printed_range_unusable:
                notes.append("printed range not applicable; registry range used")
        else:
            warnings.append(f"{name}: no reference range ({reason})")
    else:
        warnings.append(f"{name}: no reference range (not in reference table)")

    # ---- flag ---------------------------------------------------------------
    flag = _compare(value_c, qualifier, lo, hi, strict_lo, strict_hi)
    critical: Optional[Dict[str, Any]] = None
    if analyte is not None and criticals_ok:
        hit = _critical_hit(analyte, value_c, qualifier)
        if hit is not None and range_source == "report" and not qualifier \
                and (lo is None or value_c >= lo) and (hi is None or value_c <= hi):
            if hit.cutoff:
                # an assay cut-off (troponin) is the laboratory's own decision limit: the value is inside the range
                # the laboratory prints for ITS assay, so the registry limit does not apply
                warnings.append(f"{name}: the value lies inside the printed reference range; the registry critical "
                                f"limit ({fmt_num(hit.limit)}) was not applied")
                hit = None
            else:
                # a panic value (K 6.9, Hb 6.1 ...) is critical whatever the printed range says; show the clash
                warnings.append(f"{name}: the value lies inside the printed reference range but beyond the critical "
                                f"limit ({fmt_num(hit.limit)}); treated as critical - verify the printed range")
        if hit is not None:
            if hit.priority <= 1:
                flag = "CRITICAL_LOW" if hit.direction == "below" else "CRITICAL_HIGH"
                critical = {"direction": hit.direction, "limit": hit.limit, "message": hit.message, "priority": 1}
            else:
                warnings.append(f"{name}: {hit.message}")
                if flag in ("UNKNOWN", "NORMAL"):
                    flag = "LOW" if hit.direction == "below" else "HIGH"

    # ---- printed flag vs computed flag -------------------------------------
    discrepancy: Optional[str] = None
    pf = p.printed_flag
    from_report_only = False
    if pf:
        pdir = _PRINTED_DIR.get(pf)
        pcrit = pf in ("HH", "LL")
        cdir = "HIGH" if flag in ("HIGH", "CRITICAL_HIGH") else "LOW" if flag in ("LOW", "CRITICAL_LOW") else None
        if flag == "UNKNOWN":
            if pdir:
                flag = f"CRITICAL_{pdir}" if pcrit else pdir
                from_report_only = True
                notes.append(f"flag taken from the report ({pf}); no usable reference range")
                if pcrit:
                    critical = {"direction": "above" if pdir == "HIGH" else "below", "limit": None,
                                "message": "Report flagged this result critical", "priority": 1}
            else:
                warnings.append(f"{name}: flagged abnormal by the report but direction/range unknown")
        elif flag == "NORMAL":
            if pdir:
                discrepancy = f"report flag {pf} but the value is within the reference range"
                warnings.append(f"{name}: {discrepancy}")
                flag = f"CRITICAL_{pdir}" if pcrit else pdir
                if pcrit:
                    critical = {"direction": "above" if pdir == "HIGH" else "below", "limit": None,
                                "message": "Report flagged this result critical", "priority": 1}
            else:
                discrepancy = "report flagged abnormal but the value is within the reference range"
                warnings.append(f"{name}: {discrepancy}")
                flag = "UNKNOWN"
        else:
            if pdir and pdir != cdir:
                discrepancy = f"report flag {pf} contradicts the computed direction ({cdir})"
                warnings.append(f"{name}: {discrepancy}")
                if critical is None:
                    flag = "UNKNOWN"
            elif pcrit and critical is None:
                flag = f"CRITICAL_{cdir}"
                critical = {"direction": "above" if cdir == "HIGH" else "below", "limit": None,
                            "message": "Report flagged this result critical", "priority": 1}

    severity, pct = _severity(flag, value_c, lo, hi, from_report_only)
    value_type = "qualified" if qualifier else "numeric"
    if not criticals_ok:
        # scored on the printed range in the printed (unconvertible) unit: kept out of the canonical feature dict
        value_type = "unit_mismatch"
    reference_text = "--"
    if range_source != "none":
        if range_source == "report" and p.ref_text and fn is None and not p.ref_unit and lo == p.ref_low and hi == p.ref_high:
            reference_text = p.ref_text
        else:
            reference_text = format_reference(lo, hi, strict_lo, strict_hi, compact=fn is not None)
    analyte_key = analyte.key if analyte else None
    if analyte_key is None and microscopy_unit:
        analyte_key = "urine_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return ScoredRow(
        parsed=p, analyte_key=analyte_key, display_name=name, value=value_c, unit=unit_out,
        note="; ".join(n for n in notes if n), ref_low=lo, ref_high=hi, range_source=range_source, flag=flag,
        value_type=value_type, critical=critical, discrepancy=discrepancy, severity=severity,
        pct_deviation=pct, reference_text=reference_text, qualifier=qualifier, text_value=None,
    )


def _quality(r: ScoredRow) -> Tuple[int, int, int]:
    return (int(r.range_source == "report"), int(bool(r.unit)), -len(r.display_name))


def _dedupe(rows: List[ScoredRow], warnings: List[str]) -> List[ScoredRow]:
    kept: List[ScoredRow] = []
    for r in rows:
        ident = r.analyte_key or tuple(sorted(canon_tokens(r.display_name)))
        value_key = (round(r.value, 6) if r.value is not None else None, r.qualifier, (r.text_value or "").lower())
        dup = None
        for i, e in enumerate(kept):
            e_ident = e.analyte_key or tuple(sorted(canon_tokens(e.display_name)))
            if e_ident != ident or not ident:
                continue
            e_key = (round(e.value, 6) if e.value is not None else None, e.qualifier, (e.text_value or "").lower())
            if e_key == value_key and e.value_type == r.value_type:
                dup = i
                break
            if e.value_type != "unit_mismatch" and r.value_type != "unit_mismatch":
                msg = f"{r.display_name}: reported more than once with different values"
                if msg not in warnings:
                    warnings.append(msg)
        if dup is None:
            kept.append(r)
        elif _quality(r) > _quality(kept[dup]):
            kept[dup] = r
    return kept


def analyze_lab_rows(parsed_rows: List[ParsedRow], sex: Any = None, age: Any = None,
                     unparsed_numeric_lines: int = 0, extra_warnings: Tuple[str, ...] = ()) -> LabAnalysis:
    sex_n = normalize_sex(sex)
    age_n = normalize_age(age)
    warnings: List[str] = list(extra_warnings)
    scored: List[ScoredRow] = []
    ctx = _report_context(parsed_rows)
    for p in parsed_rows:
        row = _score_row(p, sex_n, age_n, warnings, ctx)
        if row is not None:
            scored.append(row)
    rows = _dedupe(scored, warnings)
    anomalies = [r for r in rows if r.flag not in NON_ANOMALY_FLAGS]
    alerts: List[Dict[str, Any]] = []
    for r in rows:
        if r.critical:
            alerts.append({
                "test": r.display_name, "analyte": r.analyte_key, "value": r.value, "unit": r.unit,
                "limit": r.critical.get("limit"), "direction": r.critical.get("direction"),
                "message": r.critical.get("message"), "priority": 1, "alert_type": "CRITICAL",
            })
    if unparsed_numeric_lines > 0:
        warnings.append(f"{unparsed_numeric_lines} numeric line(s) could not be read")
    seen, uniq = set(), []
    for w in warnings:
        if w not in seen:
            seen.add(w)
            uniq.append(w)
    return LabAnalysis(rows=rows, anomalies=anomalies, critical_alerts=alerts, warnings=uniq,
                       unparsed_numeric_lines=unparsed_numeric_lines, sex_used=sex_n, age_used=age_n)


# ── risk floor + explanation decoration ──────────────────────────────────────

_RANK = {"low": 0, "normal": 0, "unknown": 0, "moderate": 1, "high": 2, "critical": 3}


def _norm_label(label: Any) -> str:
    text = str(label or "").strip().lower()
    return {"medium": "moderate"}.get(text, text)


def apply_risk_floor(analysis: LabAnalysis, risk_label: Any, risk_score: Optional[float]) -> Tuple[str, Optional[float], bool]:
    """(label, score, uncertainty_flag). The engine's findings are a FLOOR: no
    model output can lower the result below what the rows themselves show."""
    label = _norm_label(risk_label)
    score = risk_score
    uncertain = False
    crit = any(r.flag in ("CRITICAL_HIGH", "CRITICAL_LOW") for r in analysis.rows)
    high = any(r.severity == "HIGH" for r in analysis.anomalies)
    if crit:
        floor_label, floor_score = "critical", 90.0
    elif high:
        floor_label, floor_score = "high", 70.0
    elif analysis.anomalies:
        floor_label, floor_score = "moderate", 40.0
    else:
        floor_label, floor_score = None, None

    if floor_label:
        if _RANK.get(label, 0) < _RANK[floor_label] or label in ("", "unknown"):
            label = floor_label
        score = max(float(score or 0.0), floor_score)
        if analysis.warnings:
            uncertain = True
        return label, round(score, 1), uncertain

    # no anomalies: say so honestly when something could not be assessed
    rows = analysis.rows
    all_unknown = bool(rows) and all(r.flag == "UNKNOWN" for r in rows)
    if all_unknown or analysis.warnings:
        return "unknown", None, True
    return label or "low", score, False


def critical_summary(analysis: LabAnalysis) -> str:
    lines = []
    for a in analysis.critical_alerts:
        limit = a.get("limit")
        rel = ""
        if limit is not None:
            rel = f" ({'below' if a.get('direction') == 'below' else 'above'} the critical limit of {_fmt(limit)})"
        lines.append(f"CRITICAL: {a['test']} {_fmt(a['value'])} {a.get('unit') or ''}{rel} - {a.get('message')}".replace("  ", " "))
    return "\n".join(lines)


def not_assessed_summary(analysis: LabAnalysis) -> str:
    if not analysis.warnings:
        return ""
    return "Not assessed: " + "; ".join(analysis.warnings)


def decorate_explanation(text: Optional[str], analysis: LabAnalysis) -> Optional[str]:
    head = critical_summary(analysis)
    tail = not_assessed_summary(analysis)
    if not head and not tail:
        return text
    parts = [p for p in (head, text, tail) if p]
    return "\n\n".join(parts)
