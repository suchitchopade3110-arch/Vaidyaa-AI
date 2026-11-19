"""Unit normalisation, equivalence and conversion for the lab engine.

Pure functions, no I/O. The rules here are deliberately strict: a unit is
either *the same* as the analyte's canonical unit (spelling variants,
mEq/L for monovalent ions ...), *convertible* (explicit factor/function in
the analyte registry, or a cell-count multiplier), *missing* (resolved only
when it is unambiguous) or *unknown* -- and an unknown unit is never
compared against a range.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Tuple

# ── spelling variants of exactly the same unit (factor 1) ────────────────────
# Keys are the cleaned form produced by clean_unit (lower-case, "u" for micro,
# no spaces).
UNIT_ALIASES: Dict[str, str] = {
    "mg%": "mg/dl", "mg/100ml": "mg/dl", "mgs/dl": "mg/dl", "mg/dl.": "mg/dl",
    "g%": "g/dl", "gm%": "g/dl", "gms%": "g/dl", "gm/dl": "g/dl", "gms/dl": "g/dl",
    "g/100ml": "g/dl", "gram%": "g/dl", "grams/dl": "g/dl", "gram/dl": "g/dl", "grams": "g/dl",
    "gm/l": "g/l", "gms/l": "g/l",
    "mcg/dl": "ug/dl", "mcg/100ml": "ug/dl", "ug/100ml": "ug/dl",
    "mcg/ml": "ug/ml", "mcg/l": "ug/l",
    "ug/l": "ng/ml",  # 1 ug/L == 1 ng/mL
    "mcmol/l": "umol/l",
    "iu/l": "u/l", "units/l": "u/l", "unit/l": "u/l", "miu/ml": "u/l", "mu/ml": "u/l",
    "uiu/ml": "miu/l", "uu/ml": "miu/l", "mcgiu/ml": "miu/l", "mu/l": "miu/l",
    "iu/ml": "iu/ml", "u/ml": "iu/ml",
    "mm/hour": "mm/hr", "mm/h": "mm/hr", "mm/1sthr": "mm/hr", "mm/1sthour": "mm/hr",
    "mm/hr.": "mm/hr", "mm": "mm/hr",
    "ml/min/1.73m2": "ml/min/1.73m2",
    "secs": "sec", "seconds": "sec", "second": "sec", "s": "sec",
    "sec.": "sec", "secs.": "sec",
    "mm/hg": "mmhg",
    "fl": "fl", "femtoliter": "fl",
    "ratio": "", "units": "", "unit": "", "index": "", "au": "",
}

# Count units expressed as a multiplier relative to "/uL" (== /mm3 == /cumm).
_COUNT_RE = re.compile(
    r"^(?:(?P<thousand>(?:x?10\^?3)|thousands?|thou|th|k)/(?P<d1>ul)"
    r"|(?P<e9>x?10\^?9)/l"
    r"|(?P<lakh>lakhs?|lacs?)/ul"
    r"|(?P<million>(?:x?10\^?6)|millions?|mill|mil|m)/(?P<d2>ul)"
    r"|(?P<e12>x?10\^?12)/l"
    r"|/(?P<plain>ul)"
    r"|(?P<perl>/l)"
    r")$"
)


def clean_unit(unit: Any) -> str:
    text = str(unit or "").strip().lower()
    text = (text.replace("µ", "u").replace("μ", "u").replace("³", "3").replace("²", "2")
            .replace("×", "x").replace("⁶", "^6").replace("⁹", "^9").replace("¹²", "^12"))
    text = re.sub(r"\s+", "", text)
    text = text.replace("10*", "10^").replace("10e", "10^")
    text = re.sub(r"^mc(?=[a-z])", "u", text)
    text = re.sub(r"cu\.?mm|cmm|mm3", "ul", text)
    text = re.sub(r"cells?", "", text)
    text = text.rstrip(".,;)")
    text = text.lstrip("(")
    return text


def unit_key(unit: Any) -> str:
    """Canonical unit key: equivalent spellings map to one string."""
    text = clean_unit(unit)
    return UNIT_ALIASES.get(text, text)


def count_multiplier(unit: Any) -> Optional[float]:
    """Multiplier of a cell-count unit relative to /uL, or None if not a count unit."""
    m = _COUNT_RE.match(clean_unit(unit))
    if not m:
        return None
    if m.group("plain"):
        return 1.0
    if m.group("lakh"):
        return 1e5
    if m.group("thousand"):
        return 1e3
    if m.group("e9"):
        return 1e3  # 10^9/L == 10^3/uL
    if m.group("million"):
        return 1e6
    if m.group("e12"):
        return 1e6  # 10^12/L == 10^6/uL
    return None  # bare "/L" is not a count unit lab reports use


# ── bare units that are not recognisable by shape ────────────────────────────
_BARE_UNITS = {
    "fl", "pg", "iu", "miu", "uiu", "meq", "ratio", "sec", "secs", "seconds", "cells", "units", "unit",
    "mmhg", "mm", "%", "g%", "gm%", "mg%", "index", "au", "fl.", "pg.",
}
_UNIT_PREFIX = r"(?:mg|g|gm|gms|gram|grams|ug|mcg|ng|pg|kg|mmol|umol|mcmol|nmol|pmol|mol|meq|iu|miu|uiu|mu|u|l|dl|ml|fl|cells?|mm|ml|lakhs?|lacs?|thou|thousands?|millions?|mill|mil|k|m|x?10\^?\d{1,2}|10\*\d{1,2}|10e\d{1,2}|sec|secs|s|min|hr|h|mcl|µl|ul|mosm|osm|kpa|mmhg)"
_UNIT_SHAPE = re.compile(
    rf"^(?:{_UNIT_PREFIX}(?:\.\d+)?)?(?:/(?:[a-zµμ]+\.?|\d+(?:\.\d+)?[a-zµμ0-9]*))+$",
    re.I,
)


def is_unit_token(token: str) -> bool:
    """True when `token` is shaped like a measurement unit. An English word
    ("Visit", "Reference") is never a unit."""
    t = (token or "").strip().strip("()[],;")
    if not t:
        return False
    tl = clean_unit(t)
    if not tl:
        return False
    if tl in _BARE_UNITS or tl in UNIT_ALIASES and tl not in {"s", "m", "mm"}:
        return True
    if tl == "%":
        return True
    if count_multiplier(t) is not None:
        return True
    if "%" in t and re.fullmatch(r"[a-zA-Z]{0,4}%", t):
        return True
    if tl in ("h/l", "n/a", "y/n"):
        return False
    if "/" in t and not t.startswith(("//", "http")):
        raw = t.replace("µ", "u").replace("μ", "u")
        return bool(_UNIT_SHAPE.match(clean_unit(t)) or _UNIT_SHAPE.match(raw))
    return False


# ── numbers ─────────────────────────────────────────────────────────────────
_PLAIN_NUM = r"\d+(?:\.\d+)?|\.\d+"
_GROUPED_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?"
NUM_RE = rf"(?:{_GROUPED_NUM}|{_PLAIN_NUM})(?:[eE][+-]?\d+)?"
_NUM_FULL = re.compile(rf"^[+-]?(?:{NUM_RE})$")


def parse_number(token: str, decimal_comma: bool = False) -> Optional[float]:
    """Parse one numeric token. Handles grouped digits ("150,000", "2,50,000",
    "1,250.5"), scientific notation ("2.54E+04") and, for decimal-comma
    files, "6,8" / "12.000" (thousands dot). Anything ambiguous returns None
    -- the caller then reports the line as unread instead of guessing."""
    t = (token or "").strip().replace("−", "-").replace("–", "-")
    if not t:
        return None
    if decimal_comma:
        if re.fullmatch(r"[+-]?\d{1,3}(?:\.\d{3})+(?:,\d+)?", t):
            t = t.replace(".", "").replace(",", ".")
        elif re.fullmatch(r"[+-]?\d+,\d+(?:[eE][+-]?\d+)?", t):
            t = t.replace(",", ".")
        elif "," in t:
            return None
    if _NUM_FULL.match(t):
        try:
            return float(t.replace(",", ""))
        except ValueError:
            return None
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", t):
        return float(t)
    return None


# ── unit resolution ──────────────────────────────────────────────────────────
Converter = Callable[[float], float]


@dataclass
class UnitResolution:
    kind: str                      # "same" | "converted" | "inferred" | "unknown" | "missing_bad"
    fn: Optional[Converter]        # value -> canonical value (None when kind == "same")
    unit: str                      # unit the converted value is expressed in
    note: str = ""
    ok: bool = True
    keep_printed_unit: bool = False  # equivalent spelling: show the printed unit
    warn: bool = False               # the assumption behind this resolution must be shown as a warning


def _as_fn(conv: Any) -> Converter:
    if callable(conv):
        return conv
    factor = float(conv)
    return lambda v: v * factor


def _unitless(key: str) -> bool:
    return key == ""


def _plausible_bounds(analyte: Any) -> Tuple[Optional[float], Optional[float]]:
    los, his = [], []
    for lo, hi in analyte.all_ranges():
        if lo is not None:
            los.append(lo)
        if hi is not None:
            his.append(hi)
    if not los and not his:
        return None, None
    lo = min(los) if los else 0.0
    hi = max(his) if his else (max(los) * 6 if los else None)
    return lo, hi


def _in_window(value: float, lo: Optional[float], hi: Optional[float]) -> bool:
    if lo is None and hi is None:
        return True
    low = 0.2 * (lo or 0.0)
    high = 5.0 * hi if hi else (lo or 1.0) * 30
    return low <= value <= high


def _bound_consistent(printed: Optional[float], canonical: Optional[float]) -> Optional[bool]:
    if printed is None or canonical is None:
        return None
    if printed == 0 or canonical == 0:
        return None if (printed == 0 and canonical == 0) else None
    ratio = printed / canonical
    return 0.5 <= ratio <= 2.0


def _range_matches(fn: Optional[Converter], ref_low: Optional[float], ref_high: Optional[float], analyte: Any) -> bool:
    """Do the printed range bounds (after fn) look like the analyte's canonical range?"""
    can_lo, can_hi = _plausible_bounds(analyte)
    checks = []
    for printed, canon in ((ref_low, can_lo), (ref_high, can_hi)):
        if printed is None:
            continue
        p = fn(printed) if fn else printed
        checks.append(_bound_consistent(p, canon))
    known = [c for c in checks if c is not None]
    return bool(known) and all(known)


def resolve_unit(analyte: Any, printed_unit: Optional[str], value: Optional[float] = None,
                 ref_low: Optional[float] = None, ref_high: Optional[float] = None) -> UnitResolution:
    """Decide how a printed unit relates to the analyte's canonical unit."""
    canon = analyte.unit
    ckey = unit_key(canon)
    key = unit_key(printed_unit)
    printed_clean = (printed_unit or "").strip()

    if printed_clean and (key == ckey or (ckey == "" and key == "")):
        return UnitResolution("same", None, printed_clean or canon, keep_printed_unit=True)
    m_have, m_want = count_multiplier(printed_unit), count_multiplier(canon)
    if printed_clean and m_have is not None and m_want is not None:
        factor = m_have / m_want
        if factor == 1.0:
            return UnitResolution("same", None, printed_clean, keep_printed_unit=True)
        return UnitResolution("converted", lambda v, f=factor: v * f, canon,
                              note=f"reported as {{value}} {printed_clean}")
    if printed_clean and key in analyte.equivalents:
        conv = analyte.equivalents[key]
        if conv == 1.0:
            return UnitResolution("same", None, printed_clean, keep_printed_unit=True)
        return UnitResolution("converted", _as_fn(conv), canon, note=f"reported as {{value}} {printed_clean}")
    if printed_clean:
        # A printed "ratio"/"units" on a unit-bearing analyte is NOT the canonical unit.
        return UnitResolution("unknown", None, printed_clean, note=f"unit {printed_clean} not recognised for {analyte.display}", ok=False)

    # ---- printed unit empty -------------------------------------------------
    if ckey == "":
        return UnitResolution("same", None, canon)
    has_range = ref_low is not None or ref_high is not None
    candidates = [("", None)]  # canonical
    for ukey, conv in analyte.equivalents.items():
        if conv == 1.0 or count_multiplier(ukey) is not None:
            continue
        candidates.append((ukey, _as_fn(conv)))
    if has_range:
        good = [(label, fn) for label, fn in candidates if _range_matches(fn, ref_low, ref_high, analyte)]
        if len(good) == 1:
            label, fn = good[0]
            if fn is None:
                return UnitResolution("inferred", None, canon,
                                      note=f"unit not printed; assumed {canon} (printed range matches)")
            return UnitResolution("inferred", fn, canon,
                                  note=f"unit not printed; inferred {label} from the printed range (reported as {{value}} {label})")
        why = "printed range matches no single unit" if good else f"printed range does not match {canon}"
        return UnitResolution("missing_bad", None, "", note=f"unit missing; {why}", ok=False)
    # no printed range and no printed unit: plausibility windows on the canonical range.
    # A value that is a CRITICAL value in the canonical unit is never hidden behind a missing unit
    # (Creatinine 12.5, Troponin I 2.5, Triglycerides 880): it is assessed in the canonical unit
    # and the assumption is shown as a warning. Unless the canonical reading is physiologically
    # near-impossible AND an alternative unit makes it ordinary (Glucose 5.5 is mmol/L, not 5.5 mg/dL).
    lo, hi = _plausible_bounds(analyte)
    if value is None:
        return UnitResolution("missing_bad", None, "", note=f"unit missing; value implausible in {canon}", ok=False)
    canon_in = _in_window(value, lo, hi)
    below_window = bool(lo) and value < 0.2 * lo
    alt_plausible = []
    for label, fn in candidates[1:]:
        try:
            if _in_window(fn(value), lo, hi):
                alt_plausible.append(label)
        except Exception:
            continue
    assumed = f"unit not printed; assumed {canon}"
    if canonical_critical(analyte, value) is not None and not (below_window and alt_plausible):
        extra = f" (could also be {alt_plausible[0]})" if alt_plausible else ""
        return UnitResolution("inferred", None, canon, note=assumed + extra, warn=True)
    if canon_in and not alt_plausible:
        return UnitResolution("inferred", None, canon, note=assumed)
    if canon_in and alt_plausible:
        return UnitResolution("missing_bad", None, "",
                              note=f"unit missing; value plausible in both {canon} and {alt_plausible[0]}", ok=False)
    if alt_plausible or below_window:
        return UnitResolution("missing_bad", None, "", note=f"unit missing; value implausible in {canon}", ok=False)
    return UnitResolution("inferred", None, canon, note=assumed, warn=True)


def canonical_critical(analyte: Any, value: float):
    """The first critical (priority 1) threshold `value` crosses in the analyte's canonical unit."""
    for c in getattr(analyte, "criticals", ()):
        if getattr(c, "priority", 1) > 1:
            continue
        if c.direction == "below" and (value <= c.limit if c.inclusive else value < c.limit):
            return c
        if c.direction == "above" and value > c.limit:
            return c
    return None


def fmt_num(x: float) -> str:
    text = repr(float(x))
    if "e" in text or "E" in text:
        text = f"{float(x):.10f}".rstrip("0").rstrip(".")
    elif text.endswith(".0"):
        text = text[:-2]
    return text


def to_canonical(analyte: Any, value: float, printed_unit: Optional[str],
                 ref_low: Optional[float] = None, ref_high: Optional[float] = None) -> Tuple[Optional[float], str, str, bool]:
    """(value, unit, note, ok). ok=False -> the caller must not score the value."""
    res = resolve_unit(analyte, printed_unit, value, ref_low, ref_high)
    if not res.ok:
        return None, (printed_unit or ""), res.note, False
    out = res.fn(value) if res.fn else value
    note = res.note.replace("{value}", fmt_num(value)) if res.note else ""
    unit = (printed_unit or "").strip() if res.keep_printed_unit else (res.unit or analyte.unit)
    return out, unit, note, True


def units_equivalent(a: Any, b: Any, analyte_key: Any = "") -> bool:
    """True when two unit strings denote the same unit (spelling variants,
    mEq/L<->mmol/L for monovalent ions, count multipliers equal). Empty
    units never match."""
    ka, kb = unit_key(a), unit_key(b)
    if not clean_unit(a) or not clean_unit(b):
        return False
    if ka == kb:
        return True
    if {ka, kb} == {"mmol/l", "meq/l"} and is_monovalent(analyte_key):
        return True
    ma, mb = count_multiplier(a), count_multiplier(b)
    return ma is not None and ma == mb


_MONOVALENT_TOKENS = {"sodium", "potassium", "chloride", "bicarbonate", "bicarb", "hco3", "na", "k", "cl"}


def is_monovalent(test_key: Any) -> bool:
    tokens = set(re.split(r"[^a-z0-9]+", str(test_key or "").lower()))
    return bool(tokens & _MONOVALENT_TOKENS) and not (tokens & {"calcium", "magnesium", "ca", "mg"})
