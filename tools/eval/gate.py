"""Gate modes, baseline comparison and exit codes (P1 plan 6.9).

Judge-side module: imports nothing from ``app`` or ``tools.eval.adapters`` (GN-11).

Modes: ``none`` (compute only), ``report`` (targets shown, never fails the run), ``ratchet`` (no regression of
M1-M5 beyond the per-rendering count tolerance, and the crash count may not rise; M6 and M7 are never ratcheted)
and ``targets`` (every target on the gate split, plus the ratchet). Targets and the ratchet are exact integer
comparisons; nothing here loosens a target.

Exit codes: 0 pass, 1 gate failed, 2 harness or config error, 3 hygiene violation.
"""

from __future__ import annotations

import json
import pathlib
from fractions import Fraction
from typing import Any, Mapping

from tools.eval import targets as tg

EXIT_OK, EXIT_GATE_FAILED, EXIT_ERROR, EXIT_HYGIENE = 0, 1, 2, 3
MODES = ("none", "report", "ratchet", "targets")
DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "ratchet", "profile": "ci", "split": "test", "baseline": "eval/baseline.json",
    "tolerance": {"native": 0, "csv": 0, "scan": 1, "photo": 1, "rot90": 1},
}
NO_BASELINE_HINT = "no baseline; run `make eval-baseline` to record eval/baseline.json (and log a DECISIONS line)"


class GateConfigError(RuntimeError):
    """A harness or config problem (missing or incompatible baseline, bad config): exit code 2."""


def load_config(path: str | pathlib.Path | None) -> dict[str, Any]:
    """``eval/gate.json`` merged over the defaults; the defaults when the file is absent."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path is not None and pathlib.Path(path).exists():
        try:
            data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except ValueError as exc:
            raise GateConfigError(f"{path}: not valid JSON ({exc})") from exc
        tol = {**cfg["tolerance"], **data.pop("tolerance", {})}
        cfg.update(data)
        cfg["tolerance"] = tol
    if cfg["mode"] not in MODES:
        raise GateConfigError(f"unknown gate mode {cfg['mode']!r}; known: {', '.join(MODES)}")
    return cfg


def load_baseline(path: str | pathlib.Path) -> dict[str, Any]:
    p = pathlib.Path(path)
    if not p.exists():
        raise GateConfigError(f"{p}: {NO_BASELINE_HINT}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise GateConfigError(f"{p}: not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or "results" not in data or "run" not in data:
        raise GateConfigError(f"{p}: not a metrics file; re-record it with `make eval-baseline`")
    return data


def check_context(current: Mapping[str, Any], baseline: Mapping[str, Any], split: str) -> None:
    """The baseline is never silently reused: a corpus fingerprint, profile, sampling, adapter or scorer that
    differs from the baseline raises ``GateConfigError`` (exit 2)."""
    mismatches: list[str] = []
    cr, br = current.get("run", {}), baseline.get("run", {})
    for key in ("profile", "samples", "adapter", "scorer"):
        if cr.get(key) != br.get(key):
            mismatches.append(key)
    cf = (current.get("corpus", {}).get(split) or {}).get("fingerprint")
    bf = (baseline.get("corpus", {}).get(split) or {}).get("fingerprint")
    if cf != bf:
        mismatches.append(f"{split} corpus fingerprint")
    if mismatches:
        raise GateConfigError(
            f"baseline does not match this run ({', '.join(mismatches)}); the baseline is never silently reused. "
            "If the change is deliberate, re-record with `make eval-baseline` and add a DECISIONS line.")


def _frac(r: Mapping[str, Any]) -> Fraction | None:
    return Fraction(int(r["num"]), int(r["den"])) if int(r["den"]) > 0 else None


def ratchet_failures(current: Mapping[str, Any], baseline: Mapping[str, Any], split: str,
                     tolerance: Mapping[str, int]) -> list[dict[str, Any]]:
    """Regressions of M1-M5 as counts on the current denominator, and any rise of the crash count."""
    cur = current["results"].get(split, {})
    base = baseline["results"].get(split)
    if base is None:
        raise GateConfigError(f"baseline has no results for split {split!r}; {NO_BASELINE_HINT}")
    unmeasured = sorted(set(base) - set(cur))
    if unmeasured:  # a subset run would compare only what it measured and hide the other cells' regressions
        raise GateConfigError(f"this run did not measure the baseline's {split} cell(s) {', '.join(unmeasured)}; "
                              "run every baseline rendering or pass --gate none")
    fails: list[dict[str, Any]] = []
    for rendering in sorted(cur):
        if rendering not in base:
            raise GateConfigError(f"baseline has no {split}/{rendering} cell; re-record it with `make eval-baseline`")
        tol = int(tolerance.get(rendering, 0))
        for m in tg.RATCHETED:
            c, b = cur[rendering][m], base[rendering][m]
            bf = _frac(b)
            if bf is None or int(c["den"]) <= 0:
                continue
            expected = bf * int(c["den"])
            regress = (int(c["num"]) - expected) if m in tg.LOWER_IS_BETTER else (expected - int(c["num"]))
            if regress > tol:
                fails.append({"kind": "ratchet", "split": split, "rendering": rendering, "metric": m,
                              "baseline": f"{b['num']}/{b['den']}", "current": f"{c['num']}/{c['den']}",
                              "regression_counts": float(regress), "tolerance": tol})
        cc, bc = sum(cur[rendering]["crashes"].values()), sum(base[rendering]["crashes"].values())
        if cc > bc:
            fails.append({"kind": "crashes", "split": split, "rendering": rendering, "metric": "crashes",
                          "baseline": bc, "current": cc, "tolerance": 0})
    return fails


def target_failures(current: Mapping[str, Any], split: str) -> list[dict[str, Any]]:
    cur = current["results"].get(split, {})
    fails: list[dict[str, Any]] = []
    for rendering in sorted(cur):
        for m in tg.METRICS:
            r = cur[rendering][m]
            if tg.passes(m, rendering, int(r["num"]), int(r["den"])) is False:
                fails.append({"kind": "target", "split": split, "rendering": rendering, "metric": m,
                              "target": tg.target_text(m, rendering), "current": f"{r['num']}/{r['den']}"})
    return fails


def evaluate(mode: str, current: Mapping[str, Any], *, split: str = "test", baseline: Mapping[str, Any] | None = None,
             tolerance: Mapping[str, int] | None = None) -> dict[str, Any]:
    """``{mode, passed, failures[]}``. ``ratchet`` and ``targets`` need a baseline (``GateConfigError`` when absent
    or incompatible). ``report`` and ``none`` never fail the run."""
    if mode not in MODES:
        raise GateConfigError(f"unknown gate mode {mode!r}")
    failures: list[dict[str, Any]] = []
    if mode in ("ratchet", "targets"):
        if baseline is None:
            raise GateConfigError(NO_BASELINE_HINT)
        if split not in current["results"]:
            raise GateConfigError(f"the {mode} gate needs the {split!r} split in this run")
        check_context(current, baseline, split)
        failures += ratchet_failures(current, baseline, split, tolerance or DEFAULT_CONFIG["tolerance"])
        if mode == "targets":
            failures += target_failures(current, split)
    return {"mode": mode, "passed": not failures, "failures": failures}


def exit_code(gate: Mapping[str, Any]) -> int:
    return EXIT_OK if gate.get("passed", True) else EXIT_GATE_FAILED


__all__ = [
    "DEFAULT_CONFIG", "EXIT_ERROR", "EXIT_GATE_FAILED", "EXIT_HYGIENE", "EXIT_OK", "GateConfigError", "MODES",
    "NO_BASELINE_HINT", "check_context", "evaluate", "exit_code", "load_baseline", "load_config",
    "ratchet_failures", "target_failures",
]
