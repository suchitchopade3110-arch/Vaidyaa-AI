"""Matching and unit comparators (P1 plan section 11)."""

from __future__ import annotations

from tools.eval.units import units_equivalent


def test_ma01_unit_tokens():
    eq = [
        ("x10^3/uL", "10³/µL"), ("10³/µL", "10^9/L"), ("K/µL", "x10^3/uL"), ("thou/mm3", "K/µL"),
        ("/cumm", "/µL"), ("/µL", "cells/mm3"), ("µIU/mL", "mIU/L"), ("µg/L", "ng/mL"),
        ("gm%", "g/dL"), ("", None), ("G/L", "10^9/L"), ("IU/L", "U/L"), ("mg%", "mg/dL"),
        ("lakh/cumm", "lakhs/cumm"), ("pg/mL", "ng/L"), ("mm/hr", "mm/1st hr"), ("s", "sec"),
    ]
    for a, b in eq:
        assert units_equivalent(a, b), (a, b)
    ne = [("mEq/L", "mmol/L"), ("mIU/mL", "mIU/L"), ("g/L", "g/dL"), ("g/L", "10^9/L"), ("/µL", "10^3/µL")]
    for a, b in ne:
        assert not units_equivalent(a, b), (a, b)
