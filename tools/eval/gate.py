"""Gate modes, baseline comparison and exit codes (P1 plan 6.9).

Judge-side module: imports nothing from ``app`` or ``tools.eval.adapters`` (GN-11).

Modes: ``none`` (compute only), ``report`` (targets shown, never fails the run), ``ratchet`` (no regression of
the ratcheted metrics (``targets.RATCHETED``: M1-M5, M2_post and M4_post) beyond the per-rendering count tolerance, and the
crash count may not rise; M6 and M7 are never ratcheted) and ``targets`` (every target on the gate split, plus the
ratchet). Targets and the ratchet are exact integer comparisons; nothing here loosens a target.

The baseline is only ever compared with a run of the same measuring stick (P1 review MED-1, MED-2):

* the same profile, sampling, shard (none), adapter, scorer and corpus fingerprint;
* the same judge digest: SHA-256 over the sources that define what is counted (``JUDGE_MODULES``: matching and
  comparators, review simulator, metric definitions, targets, this gate, job selection, the adapters and their
  needs_review rules, the OCR cache). Changing a metric definition therefore stops the ratchet with exit 2 instead
  of passing as an "improvement"; re-baseline deliberately and log a DECISIONS line;
* the same job count and the same GT-determined denominators in every cell (a subset run, such as one shard or an
  aggregate missing a shard file, is refused rather than compared).

Exit codes: 0 pass, 1 gate failed, 2 harness or config error, 3 hygiene violation.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
from fractions import Fraction
from typing import Any, Mapping, Sequence

from tools.eval import targets as tg

EXIT_OK, EXIT_GATE_FAILED, EXIT_ERROR, EXIT_HYGIENE = 0, 1, 2, 3
MODES = ("none", "report", "ratchet", "targets")
DEFAULT_CONFIG: dict[str, Any] = {
    "mode": "ratchet", "profile": "ci", "split": "test", "baseline": "eval/baseline.json",
    "tolerance": {"native": 0, "csv": 0, "scan": 1, "photo": 1, "rot90": 1},
    "zero_tolerance": ["M2", "M2_post"],
}
# Metrics that get zero ratchet tolerance on every rendering (P1 review LOW-3, plan 9.4): one critical lost is a
# regression even on an image rendering. A config file may add to this set but never shrink it (tighten only).
ZERO_TOLERANCE_FLOOR = ("M2", "M2_post")
NO_BASELINE_HINT = "no baseline; run `make eval-baseline` to record eval/baseline.json (and log a DECISIONS line)"
REBASELINE_HINT = ("the baseline is never silently reused. If the change is deliberate, re-record with "
                   "`make eval-baseline` and add a DECISIONS line naming what changed and why.")

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
# Sources whose change can change a gated count without changing the corpus (P1 review MED-2). The GT side
# (catalogue, Table C, clinical rules, units, scenarios, layouts, renderings) is covered by the split fingerprint
# (``generate._COMMON_MODULES``). report.py and hygiene.py only format or redact counts and are left out on purpose.
JUDGE_MODULES = (
    "tools/eval/match.py", "tools/eval/review_sim.py", "tools/eval/metrics.py", "tools/eval/targets.py",
    "tools/eval/gate.py", "tools/eval/run.py", "tools/eval/ocr_cache.py", "tools/eval/geometry.py",
)
JUDGE_GLOBS = ("tools/eval/adapters/*.py",)  # every adapter, scorer and v0 needs_review rule
# denominators fixed by the GT for a given corpus fingerprint and sampling (M3 and M7 depend on the extraction)
GT_DENOMINATORS = ("M1", "M2", "M2_post", "M4", "M5", "M6")


class GateConfigError(RuntimeError):
    """A harness or config problem (missing or incompatible baseline, bad config): exit code 2."""


def judge_paths(root: str | pathlib.Path | None = None) -> list[str]:
    """Repo-relative paths of the judge and adapter sources, sorted."""
    base = pathlib.Path(root) if root is not None else _REPO_ROOT
    paths = set(JUDGE_MODULES)
    for pattern in JUDGE_GLOBS:
        paths |= {p.relative_to(base).as_posix() for p in base.glob(pattern) if p.is_file()}
    return sorted(paths)


def judge_sources(root: str | pathlib.Path | None = None) -> dict[str, Any]:
    """``{digest, modules: {path: sha256}}`` of the judge and adapter sources (line endings normalised)."""
    base = pathlib.Path(root) if root is not None else _REPO_ROOT
    mods = {}
    for rel in judge_paths(base):
        data = (base / rel).read_bytes().replace(b"\r\n", b"\n")
        mods[rel] = hashlib.sha256(data).hexdigest()
    digest = hashlib.sha256(json.dumps(sorted(mods.items()), separators=(",", ":")).encode()).hexdigest()
    return {"digest": digest, "modules": mods}


def load_config(path: str | pathlib.Path | None) -> dict[str, Any]:
    """``eval/gate.json`` merged over the defaults; the defaults when the file is absent."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if path is not None and pathlib.Path(path).exists():
        try:
            data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
        except ValueError as exc:
            raise GateConfigError(f"{path}: not valid JSON ({exc})") from exc
        tol = {**cfg["tolerance"], **data.pop("tolerance", {})}
        zero = sorted(set(ZERO_TOLERANCE_FLOOR) | set(data.pop("zero_tolerance", [])))
        cfg.update(data)
        cfg["tolerance"] = tol
        cfg["zero_tolerance"] = zero
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


def _judge_digest(doc: Mapping[str, Any]) -> tuple[str | None, Mapping[str, str]]:
    judge = (doc.get("run") or {}).get("judge") or {}
    return judge.get("digest"), judge.get("modules") or {}


def check_context(current: Mapping[str, Any], baseline: Mapping[str, Any], split: str) -> None:
    """The baseline is never silently reused: a corpus fingerprint, profile, sampling, shard, adapter, scorer or
    judge digest that differs from the baseline raises ``GateConfigError`` (exit 2)."""
    mismatches: list[str] = []
    cr, br = current.get("run", {}), baseline.get("run", {})
    if cr.get("shard") or br.get("shard"):  # a shard is a subset: never gated against a whole-run baseline
        mismatches.append("shard (a --shard run is never ratcheted)")
    for key in ("profile", "samples", "adapter", "scorer"):
        if cr.get(key) != br.get(key):
            mismatches.append(key)
    cd, cmods = _judge_digest(current)
    bd, bmods = _judge_digest(baseline)
    if cd is None or cd != bd:
        changed = sorted(p for p in set(cmods) | set(bmods) if cmods.get(p) != bmods.get(p))
        what = ", ".join(changed) if changed else "digest missing"
        mismatches.append(f"judge/adapter definitions ({what})")
    cf = (current.get("corpus", {}).get(split) or {}).get("fingerprint")
    bf = (baseline.get("corpus", {}).get(split) or {}).get("fingerprint")
    if cf != bf:
        mismatches.append(f"{split} corpus fingerprint")
    if mismatches:
        raise GateConfigError(f"baseline does not match this run ({'; '.join(mismatches)}); {REBASELINE_HINT}")


def _frac(r: Mapping[str, Any]) -> Fraction | None:
    return Fraction(int(r["num"]), int(r["den"])) if int(r["den"]) > 0 else None


def check_denominators(current: Mapping[str, Any], baseline: Mapping[str, Any], split: str) -> None:
    """Every cell must hold the baseline's job count and GT-determined denominators (P1 review MED-1).

    For a given corpus fingerprint and sampling these are fixed by the GT alone, so any difference means the run
    measured a different set of jobs (one shard, a partial aggregate, a narrowed sample): ``GateConfigError``."""
    cur = current["results"].get(split, {})
    base = baseline["results"].get(split, {})
    diffs: list[str] = []
    for rendering in sorted(set(cur) & set(base)):
        c, b = cur[rendering], base[rendering]
        if int(c.get("jobs", -1)) != int(b.get("jobs", -2)):
            diffs.append(f"{rendering} jobs {c.get('jobs')} vs baseline {b.get('jobs')}")
        for m in GT_DENOMINATORS:
            if m in b and int(c[m]["den"]) != int(b[m]["den"]):
                diffs.append(f"{rendering} {m} denominator {c[m]['den']} vs baseline {b[m]['den']}")
    if diffs:
        raise GateConfigError(f"this run measured a different set of {split} jobs than the baseline "
                              f"({'; '.join(diffs)}); a subset run is never compared with the full baseline")


def ratchet_failures(current: Mapping[str, Any], baseline: Mapping[str, Any], split: str,
                     tolerance: Mapping[str, int], zero_tolerance: Sequence[str] = ZERO_TOLERANCE_FLOOR
                     ) -> list[dict[str, Any]]:
    """Regressions of the ratcheted metrics as counts, and any rise of the crash count. The per-rendering count
    ``tolerance`` does not apply to the ``zero_tolerance`` metrics (never below ``ZERO_TOLERANCE_FLOOR``). The run must cover every
    baseline cell with the same jobs and GT denominators (``check_denominators``)."""
    cur = current["results"].get(split, {})
    base = baseline["results"].get(split)
    if base is None:
        raise GateConfigError(f"baseline has no results for split {split!r}; {NO_BASELINE_HINT}")
    unmeasured = sorted(set(base) - set(cur))
    if unmeasured:  # a subset run would compare only what it measured and hide the other cells' regressions
        raise GateConfigError(f"this run did not measure the baseline's {split} cell(s) {', '.join(unmeasured)}; "
                              "run every baseline rendering or pass --gate none")
    extra = sorted(set(cur) - set(base))
    if extra:
        raise GateConfigError(f"baseline has no {split}/{extra[0]} cell; re-record it with `make eval-baseline`")
    check_denominators(current, baseline, split)
    fails: list[dict[str, Any]] = []
    zero = set(ZERO_TOLERANCE_FLOOR) | set(zero_tolerance)
    for rendering in sorted(cur):
        for m in tg.RATCHETED:
            tol = 0 if m in zero else int(tolerance.get(rendering, 0))
            if m not in base[rendering] or m not in cur[rendering]:
                raise GateConfigError(f"{split}/{rendering} has no {m} in the run or the baseline; {REBASELINE_HINT}")
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


def crash_failures(current: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One failure per cell (any split) whose jobs crashed. Used by the shard aggregate, whose nightly run must not
    end green when a job crashed (P1 review LOW-7). Crash counts are aggregate (exception class names only)."""
    fails: list[dict[str, Any]] = []
    for split in sorted(current.get("results", {})):
        for rendering, cell in sorted(current["results"][split].items()):
            n = sum(int(v) for v in (cell.get("crashes") or {}).values())
            if n:
                fails.append({"kind": "crashes", "split": split, "rendering": rendering, "metric": "crashes",
                              "current": n, "target": "0 crashed jobs"})
    return fails


def evaluate(mode: str, current: Mapping[str, Any], *, split: str = "test", baseline: Mapping[str, Any] | None = None,
             tolerance: Mapping[str, int] | None = None, zero_tolerance: Sequence[str] = ZERO_TOLERANCE_FLOOR
             ) -> dict[str, Any]:
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
        failures += ratchet_failures(current, baseline, split, tolerance or DEFAULT_CONFIG["tolerance"],
                                    zero_tolerance)
        if mode == "targets":
            failures += target_failures(current, split)
    return {"mode": mode, "passed": not failures, "failures": failures}


def exit_code(gate: Mapping[str, Any]) -> int:
    return EXIT_OK if gate.get("passed", True) else EXIT_GATE_FAILED


__all__ = [
    "DEFAULT_CONFIG", "EXIT_ERROR", "EXIT_GATE_FAILED", "EXIT_HYGIENE", "EXIT_OK", "GT_DENOMINATORS",
    "GateConfigError", "JUDGE_GLOBS", "JUDGE_MODULES", "MODES", "NO_BASELINE_HINT", "REBASELINE_HINT",
    "ZERO_TOLERANCE_FLOOR",
    "check_context", "check_denominators", "crash_failures", "evaluate", "exit_code", "judge_paths", "judge_sources",
    "load_baseline", "load_config", "ratchet_failures", "target_failures",
]
