"""Writers for ``eval/metrics.json`` and ``eval/report.md`` (P1 plan 6.10).

Judge-side module: imports nothing from ``app`` or ``tools.eval.adapters`` (GN-11).

Both files are written atomically (temp file in the same directory, then rename), so a failed run leaves no
partial output. The report body is deterministic; the runtime section comes last and is marked non-deterministic.
The heldout section is rendered only from the redacted cells (``hygiene.redact_heldout``) through a fixed
template: no per-report, per-family, per-analyte or confusion breakdown, and no free text from the run.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any, Mapping

from tools.eval import hygiene
from tools.eval import targets as tg

METRICS_SCHEMA = "vaidya-eval-metrics/1"
BANNER = ("P1 baseline — targets are expected to fail; recorded as measured; see PRD §11.3")
SPLIT_ORDER = ("dev", "test", "heldout")
RENDERING_ORDER = tg.RENDERINGS
METRIC_NAMES = {
    "M1": "silent-error rate", "M2": "critical recall",
    "M2_post": "critical handling after confirmation (S3)", "M3": "row accuracy before review",
    "M4": "false-critical rate", "M4_post": "false-critical rate after confirmation", "M5": "headline on confirmed", "M6": "normal → ready",
    "M7": "review burden",
}
LIMITATIONS = (
    "Per-token OCR confidence, column-parser origin, the plausibility check and unread lines as rows are not "
    "surfaced by the current product, so v0 never flags on them and never covers an omission (plan 6.3.3).",
    "Qualitative and titre rows always carry no analyte key in the product; the name comparator runs in "
    "key-optional mode for them (plan 6.7).",
    "The v0 scorer does not model the ensemble, which can raise a no-anomaly report to abnormal; the effect is "
    "visible in M5-pre. P2 replaces this scorer with the real confirmed-rows scoring (plan 6.4).",
    "Legacy urine rows are never flagged for review: the product shows no uncertainty signal for them.",
    "The evaluation measures the offline, no-LLM configuration of the product.",
)


# ---------------------------------------------------------------- atomic writes

def atomic_write_text(path: str | pathlib.Path, text: str) -> None:
    """Write ``text`` to ``path`` via a temp file and rename; remove the temp file on any failure."""
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + f".{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, p)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def dumps_metrics(metrics: Mapping[str, Any]) -> str:
    return json.dumps(metrics, sort_keys=True, indent=2, ensure_ascii=False) + "\n"


def write_metrics(path: str | pathlib.Path, metrics: Mapping[str, Any]) -> None:
    atomic_write_text(path, dumps_metrics(metrics))


def write_report(path: str | pathlib.Path, metrics: Mapping[str, Any]) -> None:
    atomic_write_text(path, render_report(metrics))


def deterministic_sections(metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Everything except the non-deterministic ``runtime`` section (compared by HA-07 and HA-09)."""
    return {k: v for k, v in metrics.items() if k != "runtime"}


# ---------------------------------------------------------------- formatting

def pct(v: Any) -> str:
    return "n/a" if v is None else f"{float(v) * 100:.2f}%"


def _ci(r: Mapping[str, Any]) -> str:
    lo, hi = r.get("ci_low"), r.get("ci_high")
    return "n/a" if lo is None or hi is None else f"{pct(lo)} to {pct(hi)}"


def verdict(r: Mapping[str, Any]) -> str:
    if r.get("target") == "info":
        return "info"
    p = r.get("pass")
    return "n/a" if p is None else ("PASS" if p else "FAIL")


def _metric_rows(split: str, results: Mapping[str, Mapping[str, Any]]) -> list[str]:
    rows = []
    for rendering in [r for r in RENDERING_ORDER if r in results]:
        cell = results[rendering]
        for m in tg.METRICS:
            r = cell[m]
            rows.append(f"| {split} | {rendering} | {m} {METRIC_NAMES[m]} | {r.get('target', '')} | "
                        f"{pct(r['value'])} | {r['num']}/{r['den']} | {_ci(r)} | {verdict(r)} |")
    return rows


_TABLE_HEAD = ["| Split | Rendering | Metric | Target | Value | Count | 95% CI | Result |",
               "|---|---|---|---|---|---|---|---|"]


def _order(d: Mapping[str, Any], order: tuple[str, ...]) -> list[str]:
    return [k for k in order if k in d]


def _kv(d: Mapping[str, Any], limit: int | None = None) -> str:
    items = sorted(d.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
    if limit:
        items = items[:limit]
    return ", ".join(f"{k}={v}" for k, v in items) or "none"


# ---------------------------------------------------------------- sections

def _header(m: Mapping[str, Any]) -> list[str]:
    run, corpus = m["run"], m.get("corpus", {})
    ad, sc = run.get("adapter", {}), run.get("scorer", {})
    lines = ["## Run", "",
             f"- Profile: `{run.get('profile')}`; splits: {', '.join(run.get('splits', []))}; "
             f"renderings: {', '.join(run.get('renderings', []))}",
             f"- Sampling: `{json.dumps(run.get('samples'), sort_keys=True)}`",
             f"- Adapter: `{ad.get('name')}` version `{ad.get('version')}`",
             f"- Scorer: `{sc.get('name')}` version `{sc.get('version')}`",
             f"- Gate mode: `{run.get('gate_mode')}`; git SHA: `{run.get('git_sha')}`",
             f"- Judge digest (matching, review simulator, metric definitions, targets, gate, job selection, "
             f"adapters): `{(run.get('judge') or {}).get('digest')}`"]
    for split in _order(corpus, SPLIT_ORDER):
        c = corpus[split]
        lines.append(f"- Corpus {split}: fingerprint `{c.get('fingerprint')}`, seed {c.get('seed')}, "
                     f"{c.get('reports')} reports")
    return lines


def _diag_section(split: str, rendering: str, d: Mapping[str, Any]) -> list[str]:
    def rt(k: str) -> str:
        r = d[k]
        return f"{pct(r['value'])} ({r['num']}/{r['den']})"

    lines = [f"#### {split} / {rendering}", "",
             f"- P-BOX {rt('P_BOX')}; M5-pre {rt('M5_pre')}; M5-oracle {rt('M5_oracle')}; "
             f"M5-oracle-clinical {rt('M5_oracle_clinical')}; R-NR {rt('R_NR')}",
             f"- Recall {rt('recall')}; precision {rt('precision')}",
             f"- Invented rows by decoy kind: {_kv(d['invented_by_decoy'])}",
             f"- Omitted rows by GT feature: {_kv(d['omitted_by_feature'])}",
             f"- Headline confusion (GT>confirmed): {_kv(d['headline_confusion'])}",
             f"- Pre-review confusion (GT>product): {_kv(d['pre_review_confusion'])}",
             f"- Job status: {_kv(d['status'])}"]
    fam = d["per_family_M3"]
    if fam:
        lines += ["", "| Family | M3 | Count |", "|---|---|---|"]
        lines += [f"| {k} | {pct(v['value'])} | {v['num']}/{v['den']} |" for k, v in sorted(fam.items())]
    sigs = d["top_error_signatures"]
    if sigs:
        lines += ["", "Top error signatures:", ""]
        lines += [f"{i}. `{s}` × {n}" for i, (s, n) in enumerate(sigs, 1)]
    lines.append("")
    return lines


def _heldout_section(results: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Fixed template over the whitelisted cells only."""
    red = hygiene.redact_heldout_results(results)
    lines = ["## Heldout (aggregate only)", "",
             "Aggregate metrics only. No per-report, per-family, per-analyte or confusion output exists for this "
             "split (plan 6.11).", ""]
    lines += _TABLE_HEAD + _metric_rows("heldout", red)
    lines += ["", "| Rendering | Jobs | Legibility | M1 wrong | M1 invented | M1 omitted | Crashes by class |",
              "|---|---|---|---|---|---|---|"]
    for rendering in _order(red, RENDERING_ORDER):
        c = red[rendering]
        comp = c.get("components", {}).get("M1", {})
        lines.append(f"| {rendering} | {c.get('jobs')} | {pct(c['legibility']['value'])} | "
                     f"{comp.get('wrong')} | {comp.get('invented')} | {comp.get('omitted')} | "
                     f"{_kv(c.get('crashes', {}))} |")
    lines.append("")
    return lines


def _runtime(rt: Mapping[str, Any]) -> list[str]:
    lines = ["## Runtime (non-deterministic)", "",
             f"- Wall time: {rt.get('wall_s')} s; OCR cache hits: {rt.get('ocr_cache', {}).get('hits')}, "
             f"misses: {rt.get('ocr_cache', {}).get('misses')}"]
    per = rt.get("per_rendering", {})
    for rendering in _order(per, RENDERING_ORDER):
        p = per[rendering]
        lines.append(f"- {rendering}: n={p.get('n')}, p50 {p.get('p50')} s, p95 {p.get('p95')} s")
    lines.append("")
    return lines


def render_report(m: Mapping[str, Any]) -> str:
    results, diag = m["results"], m.get("diagnostics", {})
    out = ["# Eval report", "", f"> {BANNER}", ""] + _header(m)
    out += ["", "## Metrics (M1-M7, M2_post and M4_post per split and rendering)", "",
            "M2 is PRD section 3 as written (flagged CRITICAL or marked needs_review, before confirmation). M2_post "
            "is the S3 final state: the confirmed row of a GT critical is scored CRITICAL (target 100%, ratcheted). "
            "M4_post (ratcheted, target as M4) counts critical flags on confirmed rows whose GT is not critical. "
            "P-BOX (diagnostic) is the share of matched rows whose reported box overlaps the GT line vertically.", ""] + _TABLE_HEAD
    for split in _order(results, SPLIT_ORDER):
        if split != hygiene.HELDOUT:
            out += _metric_rows(split, results[split])
    out += ["", "## M1 components (wrong / invented / omitted)", "",
            "| Split | Rendering | Wrong | Invented | Omitted |", "|---|---|---|---|---|"]
    for split in _order(results, SPLIT_ORDER):
        for rendering in _order(results[split], RENDERING_ORDER):
            c = results[split][rendering]["components"]["M1"]
            out.append(f"| {split} | {rendering} | {c['wrong']} | {c['invented']} | {c['omitted']} |")
    out += ["", "## Dev and test diagnostics (non-gating)", ""]
    for split in _order(diag, SPLIT_ORDER):
        for rendering in _order(diag[split], RENDERING_ORDER):
            out += _diag_section(split, rendering, diag[split][rendering])
    if hygiene.HELDOUT in results:
        out += _heldout_section(results[hygiene.HELDOUT])
    gate = m.get("gate", {})
    out += ["## Gate", "", f"- Mode `{gate.get('mode')}`: {'PASS' if gate.get('passed') else 'FAIL'}"]
    for f in gate.get("failures", []):
        out.append(f"- {f['kind']}: {f['split']}/{f['rendering']} {f['metric']} "
                   f"(current {f['current']}, {f.get('target') or 'baseline ' + str(f.get('baseline'))})")
    out += ["", "## Known v0 limitations", ""] + [f"- {t}" for t in LIMITATIONS] + [""]
    if "runtime" in m:
        out += _runtime(m["runtime"])
    return "\n".join(out) + "\n"


__all__ = ["BANNER", "METRICS_SCHEMA", "atomic_write_text", "deterministic_sections", "dumps_metrics", "pct",
           "render_report", "verdict", "write_metrics", "write_report"]
