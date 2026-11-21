"""Immutable PRD section 3 targets per rendering (P1 plan 6.9).

Judge-side module: imports nothing from ``app`` or ``tools.eval.adapters`` (GN-11).

Values are exact fractions, so "0.5%" is exactly 1/200 and a 0.6% rate fails it; they are never rounded, tuned or
loosened to make a run pass (CL-31 pins them to the PRD). ``None`` marks an informational cell (no target).
"""

from __future__ import annotations

from fractions import Fraction
from types import MappingProxyType
from typing import Any, Mapping

RENDERINGS = ("native", "csv", "scan", "photo", "rot90")
METRICS = ("M1", "M2", "M3", "M4", "M5", "M6", "M7")

LE, GE, EQ = "<=", ">=", "=="


def _pct(num: int, den: int = 1000) -> Fraction:
    return Fraction(num, den)


def _t(op: str, value: Fraction) -> Mapping[str, Any]:
    return MappingProxyType({"op": op, "value": value})


_M1 = {"native": _t(LE, _pct(5)), "csv": _t(LE, _pct(5)), "scan": _t(LE, _pct(15)), "rot90": _t(LE, _pct(15)),
       "photo": _t(LE, _pct(15))}
_M2 = dict.fromkeys(RENDERINGS, _t(EQ, Fraction(1)))
_M3 = {"native": _t(GE, _pct(92, 100)), "csv": _t(GE, _pct(92, 100)), "scan": _t(GE, _pct(80, 100)),
       "rot90": _t(GE, _pct(80, 100)), "photo": _t(GE, _pct(70, 100))}
_M4 = dict.fromkeys(RENDERINGS, _t(LE, _pct(5)))
_M5 = dict.fromkeys(RENDERINGS, _t(GE, _pct(98, 100)))
_M6 = {"native": _t(GE, _pct(90, 100)), "csv": _t(GE, _pct(90, 100)), "scan": None, "rot90": None, "photo": None}
_M7 = {"native": _t(LE, _pct(15, 100)), "csv": _t(LE, _pct(15, 100)), "scan": None, "rot90": None, "photo": None}

TARGETS: Mapping[str, Mapping[str, Mapping[str, Any] | None]] = MappingProxyType({
    m: MappingProxyType(d) for m, d in (("M1", _M1), ("M2", _M2), ("M3", _M3), ("M4", _M4), ("M5", _M5),
                                        ("M6", _M6), ("M7", _M7))
})

RATCHETED = ("M1", "M2", "M3", "M4", "M5")  # M6 and M7 are target-only (plan 6.9)
LOWER_IS_BETTER = frozenset({"M1", "M4", "M7"})


def target_for(metric: str, rendering: str) -> Mapping[str, Any] | None:
    return TARGETS[metric][rendering]


def target_text(metric: str, rendering: str) -> str:
    """'<= 0.5%', '= 100%', '>= 92%' or 'info'."""
    t = target_for(metric, rendering)
    if t is None:
        return "info"
    pct = float(t["value"] * 100)
    shown = f"{pct:g}%"
    return f"{'=' if t['op'] == EQ else t['op']} {shown}"


def passes(metric: str, rendering: str, num: int, den: int) -> bool | None:
    """True/False against the target, None when the cell is informational or has no denominator (n/a).

    Exact integer arithmetic on (num, den); no floating point."""
    t = target_for(metric, rendering)
    if t is None or den <= 0:
        return None
    rate = Fraction(num, den)
    if t["op"] == LE:
        return rate <= t["value"]
    if t["op"] == GE:
        return rate >= t["value"]
    return rate == t["value"]


def annotate(cell: Mapping[str, Any], rendering: str) -> dict[str, Any]:
    """A copy of a ``metrics.cell_results`` cell with ``target`` (text) and ``pass`` added to each of M1..M7."""
    out = dict(cell)
    for m in METRICS:
        r = dict(cell[m])
        r["target"] = target_text(m, rendering)
        r["pass"] = passes(m, rendering, int(r["num"]), int(r["den"]))
        out[m] = r
    return out


__all__ = ["EQ", "GE", "LE", "LOWER_IS_BETTER", "METRICS", "RATCHETED", "RENDERINGS", "TARGETS", "annotate",
           "passes", "target_for", "target_text"]
