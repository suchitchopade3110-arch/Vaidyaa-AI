"""Independent unit-spelling normaliser and converter (P1 section 6.7).

Judge/GT side: imports nothing from ``app``. Unit equivalence classes follow the
plan text; conversions use the per-analyte factors in ``catalogue``.
"""

from __future__ import annotations

import re

# canonical token -> spellings (already in normalised form, see ``_norm``)
_CLASSES: dict[str, tuple[str, ...]] = {
    "g/dL": ("g/dl", "gm/dl", "g%", "gm%"),
    "mg/dL": ("mg/dl", "mg%"),
    "umol/L": ("umol/l",),
    "10^3/uL": ("10^3/ul", "x10^3/ul", "k/ul", "thou/mm3", "10^9/l", "x10^9/l", "x10^3/mm3", "10^3/mm3"),
    "/uL": ("/ul", "cells/ul", "/cumm", "cells/cumm", "/mm3", "cells/mm3", "/cmm"),
    "million/uL": ("million/ul", "mill/cumm", "10^6/ul", "x10^6/ul", "10^12/l", "x10^12/l", "million/cumm"),
    "lakh/cumm": ("lakh/cumm", "lakhs/cumm"),
    "U/L": ("u/l", "iu/l"),
    "uIU/mL": ("uiu/ml", "miu/l"),
    "ng/mL": ("ng/ml", "ug/l"),
    "pg/mL": ("pg/ml", "ng/l"),
    "ug/dL": ("ug/dl", "mcg/dl"),
    "mm/hr": ("mm/hr", "mm/1sthr", "mm/h", "mm/1sthour"),
    "s": ("s", "sec", "secs", "seconds"),
    "/hpf": ("/hpf", "cells/hpf"),
    "mmol/L": ("mmol/l",),
    "mEq/L": ("meq/l",),
    "mIU/mL": ("miu/ml",),
    "mmol/mol": ("mmol/mol",),
    "%": ("%",),
    "mg/L": ("mg/l",),
    "g/L": ("g/l",),
    "fL": ("fl",),
    "pg": ("pg",),
    "mmHg": ("mmhg",),
    "ng/dL": ("ng/dl",),
    "ug/mL": ("ug/ml", "ug/mlfeu", "mcg/ml"),
    "IU/mL": ("iu/ml",),
    "mg/g": ("mg/g",),
    "mL/min/1.73m2": ("ml/min/1.73m2", "ml/min/1.73m^2", "ml/min"),
}

_LOOKUP: dict[str, str] = {}
for _canon, _spellings in _CLASSES.items():
    for _s in _spellings:
        _LOOKUP[_s] = _canon


def _norm(s: str) -> str:
    s = s.strip()
    s = s.replace("µ", "u").replace("μ", "u").replace("×", "x")
    s = s.replace("³", "^3").replace("⁶", "^6").replace("²", "^2")
    s = s.replace("–", "-")
    s = s.lower()
    s = re.sub(r"\s+", "", s)
    s = s.replace("(", "").replace(")", "")
    return s


def canon_unit(unit: str | None) -> str:
    """Canonical token for a printed unit spelling; unknown spellings normalise to themselves."""
    if unit is None:
        return ""
    raw = unit.strip()
    if raw == "":
        return ""
    # "G/L" (giga per litre, a count unit) must not collide with g/L (grams per litre)
    if raw == "G/L":
        return "10^3/uL"
    n = _norm(raw)
    return _LOOKUP.get(n, n)


def units_equivalent(a: str | None, b: str | None) -> bool:
    """Spelling-level equivalence (MA-01). Empty and None are equal."""
    return canon_unit(a) == canon_unit(b)


# ---------------------------------------------------------------- conversions

def _factor_to_conventional(key: str, unit: str | None) -> float | None:
    """Multiplier turning a value in ``unit`` into the analyte's conventional (canonical) unit.

    None when the unit is not recognised for this analyte.
    """
    from tools.eval.catalogue import CATALOGUE

    a = CATALOGUE[key]
    cu = canon_unit(unit)
    if cu == "" or cu == canon_unit(a.unit):
        return 1.0
    if a.si_unit and cu == canon_unit(a.si_unit) and a.si_factor:
        return 1.0 / a.si_factor
    for spelling, mult in a.alt_units.items():
        if cu == canon_unit(spelling):
            return mult
    return None


def to_canonical(key: str, value: float, unit: str | None) -> tuple[float, str]:
    """Convert (value, unit) to the analyte's conventional unit. Raises ValueError for an unknown unit."""
    from tools.eval.catalogue import CATALOGUE

    a = CATALOGUE[key]
    cu = canon_unit(unit)
    if key == "hba1c" and cu == "mmol/mol":
        return value / 10.929 + 2.15, a.unit
    f = _factor_to_conventional(key, unit)
    if f is None:
        raise ValueError(f"unit {unit!r} not convertible for {key}")
    return value * f, a.unit


def from_canonical(key: str, value: float, unit: str | None) -> float:
    """Convert a conventional-unit value to ``unit``."""
    cu = canon_unit(unit)
    if key == "hba1c" and cu == "mmol/mol":
        return (value - 2.15) * 10.929
    f = _factor_to_conventional(key, unit)
    if f is None:
        raise ValueError(f"unit {unit!r} not convertible for {key}")
    return value / f


def decimals_for(key: str, unit: str | None) -> int:
    """Printed decimals for the analyte in the given unit."""
    from tools.eval.catalogue import CATALOGUE

    a = CATALOGUE[key]
    cu = canon_unit(unit)
    if cu in ("", canon_unit(a.unit)):
        return a.decimals
    if a.si_unit and cu == canon_unit(a.si_unit):
        return a.si_decimals
    return a.alt_decimals.get(cu, a.decimals)
