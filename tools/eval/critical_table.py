"""Independent Table C critical values, guard bands and caps (P1 plan section 3.5).

Basis: consensus critical-value lists (Kost GJ, JAMA 1990;263:704-7 and later
institutional surveys). Guard bands cover the legitimate spread between
institutions; the generator never emits a value inside a guard band, so
``is_critical`` is the same under any reasonable institutional policy.

GT-side module: no import from ``app``. Engine limits were not used here.
"""

from __future__ import annotations

from dataclasses import dataclass

from tools.eval import units as _units


@dataclass(frozen=True)
class CritEntry:
    low_guard: tuple[float, float] | None  # (critical below [0]; non-critical low values are above [1])
    crit_low: tuple[float, float] | None  # emitted critical-low range
    high_guard: tuple[float | None, float] | None  # (non-critical high below [0]; critical above [1])
    crit_high: tuple[float, float] | None  # emitted critical-high range
    # high_guard[0] is None when the lower edge is the printed upper reference limit (troponins)


_GLU = CritEntry((40, 55), (18, 39), (300, 500), (501, 750))

CRITICAL: dict[str, CritEntry] = {
    "potassium": CritEntry((2.5, 3.0), (1.8, 2.4), (6.0, 6.5), (6.6, 8.5)),
    "sodium": CritEntry((118, 125), (105, 117), (155, 160), (161, 175)),
    "glucose_fasting": _GLU,
    "glucose_pp": _GLU,
    "glucose_random": _GLU,
    "glucose_unspecified": _GLU,
    "glucose_capillary": _GLU,
    "calcium": CritEntry((6.0, 7.0), (4.5, 5.9), (12.0, 13.5), (13.6, 16.0)),
    "ionized_calcium": CritEntry((0.75, 0.90), (0.55, 0.74), (1.50, 1.65), (1.66, 1.90)),
    "magnesium": CritEntry((0.9, 1.2), (0.5, 0.8), (4.0, 5.0), (5.1, 7.0)),
    "phosphorus": CritEntry((0.9, 1.5), (0.4, 0.8), None, None),
    "hemoglobin": CritEntry((6.0, 8.0), (3.5, 5.9), (18.5, 21.0), (21.1, 23.0)),
    "wbc": CritEntry((1.0, 2.5), (0.3, 0.9), (30, 50), (50.5, 120)),
    "neutrophils_abs": CritEntry((0.5, 1.0), (0.05, 0.45), None, None),
    "platelets": CritEntry((20, 50), (3, 19), (750, 1000), (1010, 1400)),
    "inr": CritEntry(None, None, (3.5, 5.0), (5.1, 9.0)),
    "blood_ph": CritEntry((7.15, 7.25), (6.95, 7.14), (7.55, 7.65), (7.66, 7.75)),
    "pco2": CritEntry((15, 25), (10, 14), (60, 70), (71, 100)),
    "po2": CritEntry((40, 50), (28, 39), None, None),
    "bicarbonate": CritEntry((10, 15), (5, 9), (38, 45), (46, 55)),
    "creatinine": CritEntry(None, None, (5.0, 10.0), (10.1, 15.0)),
    "hs_troponin_t": CritEntry(None, None, (None, 100), (101, 3000)),
    "troponin_i": CritEntry(None, None, (None, 0.5), (0.51, 20)),
}

# (min, max) kept inside these bounds in conventional units; None = unbounded on that side
CAPS: dict[str, tuple[float | None, float | None]] = {
    "hematocrit": (22.0, 57.0),
    "bilirubin_total": (None, 9.9),
    "triglycerides": (None, 499.0),
    "alt": (None, 999.0),
    "ast": (None, 999.0),
    "amylase": (None, 999.0),
    "lipase": (None, 999.0),
    "ck": (None, 4999.0),
    "uric_acid": (None, 12.9),
    "aptt": (None, 69.0),
    "pt": (None, 29.0),
    "vitamin_d": (None, 99.9),
    "csf_glucose": (20.0, None),
    "phosphorus": (None, 7.9),  # "never >= 8.0"
    "neutrophils_abs": (None, 40.0),
}


def critical_direction(key: str, canonical_value: float, specimen: str = "serum") -> str | None:
    """'high', 'low' or None for a value in the analyte's conventional unit. Blood specimens only (S5).

    Troponin high-side critical limits are absolute (100 ng/L, 0.5 ng/mL), so no printed URL is needed.
    """
    if specimen != "serum":
        return None
    e = CRITICAL.get(key)
    if e is None:
        return None
    if e.low_guard is not None and canonical_value < e.low_guard[0]:
        return "low"
    if e.high_guard is not None and canonical_value > e.high_guard[1]:
        return "high"
    return None


def is_critical(key: str, value: float, unit: str | None, specimen: str = "serum") -> bool:
    """Table C on a printed (value, unit); converts to the conventional unit first."""
    return critical_flag(key, value, unit, specimen) is not None


def critical_flag(key: str, value: float, unit: str | None, specimen: str = "serum") -> str | None:
    """'CRITICAL_HIGH', 'CRITICAL_LOW' or None."""
    if specimen != "serum" or key not in CRITICAL:
        return None
    canon_key = "glucose_fasting" if key == "glucose_capillary" else key
    cv, _ = _units.to_canonical(canon_key, value, unit)
    d = critical_direction(key, cv, specimen)
    return None if d is None else "CRITICAL_" + d.upper()


def critical_rule(key: str, canonical_value: float) -> str:
    """Human-readable rule text, e.g. 'potassium > 6.5 (Table C)'."""
    e = CRITICAL[key]
    if critical_direction(key, canonical_value) == "low":
        return f"{key} < {e.low_guard[0]:g} (Table C)"
    return f"{key} > {e.high_guard[1]:g} (Table C)"


def guard_bands(key: str, printed_upper: float | None = None) -> list[tuple[float, float]]:
    """Never-emitted intervals in conventional units. Troponin guards start at the printed URL."""
    e = CRITICAL.get(key)
    out: list[tuple[float, float]] = []
    if e is None:
        return out
    if e.low_guard is not None:
        out.append(e.low_guard)
    if e.high_guard is not None:
        lo = e.high_guard[0]
        if lo is None:
            lo = printed_upper if printed_upper is not None else _default_upper(key)
        out.append((lo, e.high_guard[1]))
    return out


def _default_upper(key: str) -> float:
    return {"hs_troponin_t": 14.0, "troponin_i": 0.04}[key]


def in_guard(key: str, canonical_value: float, printed_upper: float | None = None) -> bool:
    """True when the value lies inside a guard band (closed interval); such values are never emitted."""
    return any(lo <= canonical_value <= hi for lo, hi in guard_bands(key, printed_upper))


def violates_cap(key: str, canonical_value: float) -> bool:
    cap = CAPS.get(key)
    if cap is None:
        return False
    lo, hi = cap
    return (lo is not None and canonical_value < lo) or (hi is not None and canonical_value > hi)
