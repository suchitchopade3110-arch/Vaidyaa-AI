"""Per-job records, additive cell counts, M1 to M7, diagnostics and Wilson intervals (P1 plan 6.8).

Judge-side module: imports nothing from ``app`` or ``tools.eval.adapters`` (GN-11). Jobs, extraction results and
scores are read by duck typing (``EvalJob``, ``ExtractionResult`` and ``ScoreResultLike`` attribute names).

Flow per job: ``job_record(job, gt, extraction, scorer)`` -> GT rows in scope (one page for a photo job) ->
``match.align`` -> ``review_sim.simulate`` -> ``scorer.score`` -> integer counts. Every count is additive, so shard
outputs and job records are summed with ``add_counts`` in any order and the rates are computed only at the end
(``cell_results``, ``cell_diagnostics``).

The definitions are the plan's conservative reading of PRD section 3 (DECISIONS 3, 4, 7):

* M1 silent errors / GT rows: wrong unflagged pairs + unflagged inventions + (unless review_all) uncovered omissions.
* M2 critical recall: a GT critical counts when its matched row is CRITICAL_* (either direction) or needs_review,
  or an unread line covers it. A review_all job covers nothing by itself.
* M3 correct pairs / (GT rows + unmatched extracted rows): inventions count against accuracy.
* M4 extracted CRITICAL_* rows that are unmatched or whose GT row is not critical / GT rows (before review).
* M5 confirmed headline (simulated clinician, then the scorer; three levels) == GT headline / jobs.
* M6 over jobs whose GT headline is normal and whose scope has no expected-needs_review row: status ok, rows
  extracted, none flagged, no unread line, no report-level item, not review_all, pre-review headline normal.
* M7 flagged items / (extracted rows + unread lines).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable, Mapping, Sequence

from tools.eval import clinical
from tools.eval import match as mt
from tools.eval import review_sim as rs
from tools.eval.match import CRITICAL_FLAGS, FIELDS, field

METRIC_IDS = ("M1", "M2", "M3", "M4", "M5", "M6", "M7")
HEADLINES = ("critical", "abnormal", "normal", "unknown")
Z95 = 1.959963984540054  # two-sided 95% normal quantile
TOP_SIGNATURES = 20
DECOY_OVERLAP = 0.5

# every additive integer count a job contributes (sparse breakdowns live in the dicts of _BREAKDOWNS)
_SCALARS = (
    "jobs", "review_all_jobs", "gt_rows", "ext_rows", "unread_lines", "pairs", "correct", "unmatched_gt",
    "unmatched_ext", "covered", "m1_wrong", "m1_invented", "m1_omitted", "gt_critical", "m2_num", "m2_post_num",
    "m4_num", "m5_num", "m5_pre_num", "m5_oracle_num", "m5_oracle_clinical_num", "m5_oracle_clinical_den", "m6_den",
    "m6_num", "m7_num", "m7_den", "amb_rows", "amb_flagged", "legible",
)
_BREAKDOWNS = (
    "crashes", "status", "field_ok", "family_m3", "signatures", "invented_by_decoy", "omitted_by_feature",
    "headline_confusion", "pre_review_confusion", "match_rules", "review_actions",
)


# ---------------------------------------------------------------- rates

def wilson(num: int, den: int, z: float = Z95) -> tuple[float | None, float | None]:
    """Wilson score interval for ``num`` successes in ``den`` trials (None, None when undefined).

    A count above its denominator (more inventions than GT rows) is not a proportion; it has no interval."""
    if den <= 0 or num < 0 or num > den:
        return None, None
    p = num / den
    z2 = z * z
    scale = 1.0 + z2 / den
    centre = (p + z2 / (2 * den)) / scale
    half = z * math.sqrt(p * (1 - p) / den + z2 / (4 * den * den)) / scale
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(num: int, den: int) -> dict[str, Any]:
    """``{num, den, value, ci_low, ci_high}``; value and interval are None (n/a) when the denominator is 0."""
    lo, hi = wilson(num, den)
    return {"num": int(num), "den": int(den), "value": (num / den) if den > 0 else None, "ci_low": lo, "ci_high": hi}


# ---------------------------------------------------------------- counts

def empty_counts() -> dict[str, Any]:
    c: dict[str, Any] = dict.fromkeys(_SCALARS, 0)
    for k in _BREAKDOWNS:
        c[k] = {}
    c["field_ok"] = dict.fromkeys(FIELDS, 0)
    return c


def add_counts(a: Mapping[str, Any], b: Mapping[str, Any]) -> dict[str, Any]:
    """Recursive sum of two count trees (ints and dicts of ints). Commutative and associative."""
    out: dict[str, Any] = {}
    for k in sorted(set(a) | set(b)):
        if k not in a:
            out[k] = _copy(b[k])
        elif k not in b:
            out[k] = _copy(a[k])
        elif isinstance(a[k], Mapping) and isinstance(b[k], Mapping):
            out[k] = add_counts(a[k], b[k])
        elif isinstance(a[k], Mapping) or isinstance(b[k], Mapping):
            raise TypeError(f"count {k!r} is a dict on one side only")
        else:
            out[k] = int(a[k]) + int(b[k])
    return out


def sum_counts(trees: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    total: dict[str, Any] = empty_counts()
    for t in trees:
        total = add_counts(total, t)
    return total


def _copy(v: Any) -> Any:
    return {k: _copy(x) for k, x in sorted(v.items())} if isinstance(v, Mapping) else int(v)


def _bump(d: dict[str, int], key: str, n: int = 1) -> None:
    d[key] = d.get(key, 0) + n


# ---------------------------------------------------------------- GT scope and headline

def job_pages(job: Any) -> tuple[int, ...]:
    return tuple(int(p) for p in (field(job, "pages") or ()))


def scope_rows(gt: Mapping[str, Any], pages: Sequence[int] = ()) -> list[Any]:
    """GT rows in a job's scope: every row, or only those on ``pages`` (a photo job aligns only its page)."""
    rows = list(gt.get("rows") or [])
    if not pages:
        return rows
    keep = set(pages)
    return [g for g in rows if int(field(g, "page")) in keep]


def gt_headline(gt: Mapping[str, Any], rows: Sequence[Any], pages: Sequence[int] = ()) -> str:
    """The GT headline of the job's scope (plan 4.2.5), checked against the GT ``expected`` block.

    A mismatch means the GT itself is inconsistent; it raises instead of scoring against a wrong truth."""
    h = clinical.headline([(bool(field(g, "is_critical")), str(field(g, "flag_true"))) for g in rows])
    exp = gt.get("expected") or {}
    all_pages = {int(field(g, "page")) for g in gt.get("rows") or []}
    if not pages or all_pages <= set(pages):
        want = exp.get("headline")
    elif len(pages) == 1:
        want = (exp.get("per_page_headline") or {}).get(str(pages[0]))
    else:
        want = None
    if want is not None and want != h:
        raise ValueError(f"GT {gt.get('report_id')}: headline {want!r} in expected but {h!r} from its rows")
    return h


def three_level(h: Any) -> str:
    """A scorer or product headline in critical | abnormal | normal | unknown; anything else is unknown."""
    s = str(h or "").strip().lower()
    return s if s in HEADLINES else "unknown"


def confirmed_headline(scorer: Any, rows: Sequence[Any], sex: str | None, age: int | None) -> tuple[str, list[str]]:
    """(headline, row flags) of a confirmed row set. An empty set confirms nothing, so its headline is unknown
    (plan 6.5, review_all row); the scorer is not asked to call an empty report normal."""
    if not rows:
        return "unknown", []
    score = scorer.score(list(rows), sex, age)
    flags = [str(f) for f in (field(score, "row_flags") or [])]
    return three_level(field(score, "headline")), flags


# ---------------------------------------------------------------- the job record

@dataclass
class JobRecord:
    """One job's additive counts plus identifiers and a small detail block (kept in memory; never written for
    heldout, plan 6.11)."""

    job_id: str
    report_id: str
    split: str
    rendering: str
    family: str
    counts: dict[str, Any]
    gt_headline: str = ""
    confirmed_headline: str = "unknown"
    pre_review_headline: str = "unknown"
    oracle_headline: str = "unknown"
    detail: dict[str, Any] = dc_field(default_factory=dict, repr=False)


def _kind(r: Any) -> str:
    return str(field(r, "kind") or "row")


def _flagged(r: Any) -> bool:
    return bool(field(r, "needs_review"))


def _is_critical_flag(f: Any) -> bool:
    return str(f or "").strip().upper() in CRITICAL_FLAGS


def _overlap(a: set[str], b: set[str]) -> float:
    """Overlap coefficient |a ∩ b| / min(|a|, |b|)."""
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


def _decoy_kind(e: Any, decoys: Sequence[Any]) -> str:
    text = field(e, "source_text") or " ".join(
        str(field(e, k) or "") for k in ("name_raw", "value_raw", "unit_raw"))
    et = set(mt.tokens(text))
    best, best_score = "none", 0.0
    for d in sorted(decoys, key=lambda d: (str(field(d, "kind")), str(field(d, "text")))):
        s = _overlap(et, set(mt.tokens(field(d, "text"))))
        if s >= DECOY_OVERLAP and s > best_score:
            best, best_score = str(field(d, "kind")), s
    return best


def _legible(g: Any, raw_tokens: Sequence[str]) -> bool:
    return mt.contains_tokens(raw_tokens, mt.tokens(field(g, "value_text")))


def job_record(job: Any, gt: Mapping[str, Any], extraction: Any, scorer: Any, *, oracle_scorer: Any = None,
               crash: str | None = None) -> JobRecord:
    """Score one job. ``scorer`` is the active scorer (M5, M5-oracle); ``oracle_scorer`` (the GT-rules
    ``ClinicalRulesScorer``) adds M5-oracle-clinical. ``crash`` is the exception class name when the adapter raised
    (use ``crash_record``)."""
    pages = job_pages(job)
    G = scope_rows(gt, pages)
    rows = list(field(extraction, "rows") or [])
    E = [r for r in rows if _kind(r) == "row"]
    U = [r for r in rows if _kind(r) == "unread_line"]
    review_all = bool(field(extraction, "review_all"))
    status = str(field(extraction, "status") or "failed")
    items = list(field(extraction, "report_review_items") or [])
    sex, age = field(job, "sex"), field(job, "age")

    a = mt.align(G, E, U)
    checks = {p.g: mt.compare(G[p.g], E[p.e]) for p in a.pairs}
    review = rs.simulate(G, E, U, a, review_all)
    gt_h = gt_headline(gt, G, pages)
    conf_h, conf_flags = confirmed_headline(scorer, review.rows, sex, age)
    oracle_rows = rs.corrected_rows(G)
    oracle_h, _ = confirmed_headline(scorer, oracle_rows, sex, age)
    pre_h = three_level(field(extraction, "pre_review_headline"))

    c = empty_counts()
    c["jobs"] = 1
    c["review_all_jobs"] = int(review_all)
    _bump(c["status"], "crash" if crash else status)
    if crash:
        _bump(c["crashes"], crash)
    c["gt_rows"], c["ext_rows"], c["unread_lines"] = len(G), len(E), len(U)
    c["pairs"], c["unmatched_gt"], c["unmatched_ext"] = len(a.pairs), len(a.unmatched_gt), len(a.unmatched_ext)
    c["covered"] = len(a.covered)
    for p in a.pairs:
        _bump(c["match_rules"], p.rule)
    for action, n in review.actions.items():
        if n:
            _bump(c["review_actions"], action, n)

    # M1 and M3 (and per-field accuracy, error signatures)
    family = str(gt.get("family") or "?")
    fam = c["family_m3"].setdefault(family, {"num": 0, "den": 0})
    for p in a.pairs:
        chk = checks[p.g]
        for f in FIELDS:
            c["field_ok"][f] += int(getattr(chk, f))
        if chk.correct:
            c["correct"] += 1
        else:
            _bump(c["signatures"], chk.signature)
            if not _flagged(E[p.e]):
                c["m1_wrong"] += 1
    c["m1_invented"] = sum(1 for ei in a.unmatched_ext if not _flagged(E[ei]))
    omitted = [gi for gi in a.unmatched_gt if gi not in a.covered]
    c["m1_omitted"] = 0 if review_all else len(omitted)
    fam["num"] += c["correct"]
    fam["den"] += len(G) + len(a.unmatched_ext)

    # M2 (before review) and M2-post (after review and scoring)
    e_of = {p.g: p.e for p in a.pairs}
    for gi, g in enumerate(G):
        if not field(g, "is_critical"):
            continue
        c["gt_critical"] += 1
        ei = e_of.get(gi)
        if (ei is not None and (_is_critical_flag(field(E[ei], "flag")) or _flagged(E[ei]))) or gi in a.covered:
            c["m2_num"] += 1
        ci = review.row_for_gt(gi)
        if ci is not None and ci < len(conf_flags) and _is_critical_flag(conf_flags[ci]):
            c["m2_post_num"] += 1

    # M4: critical flags on inventions or on rows whose GT is not critical (before review)
    g_of = {p.e: p.g for p in a.pairs}
    for ei, e in enumerate(E):
        if _is_critical_flag(field(e, "flag")):
            gi = g_of.get(ei)
            if gi is None or not field(G[gi], "is_critical"):
                c["m4_num"] += 1

    # M5 and its diagnostics
    c["m5_num"] = int(conf_h == gt_h)
    c["m5_pre_num"] = int(pre_h == gt_h)
    c["m5_oracle_num"] = int(oracle_h == gt_h)
    _bump(c["headline_confusion"], f"{gt_h}>{conf_h}")
    _bump(c["pre_review_confusion"], f"{gt_h}>{pre_h}")
    oracle_clinical_h = None
    if oracle_scorer is not None:
        oracle_clinical_h, _ = confirmed_headline(oracle_scorer, oracle_rows, sex, age)
        c["m5_oracle_clinical_den"] = 1
        c["m5_oracle_clinical_num"] = int(oracle_clinical_h == gt_h)

    # M6: a GT-normal job with no genuinely ambiguous row, shown as ready to confirm
    amb = [gi for gi, g in enumerate(G) if field(g, "expected_needs_review")]
    if gt_h == "normal" and not amb:
        c["m6_den"] = 1
        c["m6_num"] = int(status == "ok" and len(E) > 0 and not any(_flagged(e) for e in E) and not U and not items
                          and not review_all and pre_h == "normal")

    # M7: review burden
    c["m7_den"] = len(E) + len(U)
    c["m7_num"] = sum(1 for x in E + U if _flagged(x))

    # R-NR, legibility, inventions by decoy kind, omissions by GT feature
    c["amb_rows"] = len(amb)
    c["amb_flagged"] = sum(1 for gi in amb if gi in e_of and _flagged(E[e_of[gi]]))
    raw_tokens = mt.tokens(field(extraction, "raw_text"))
    c["legible"] = sum(1 for g in G if _legible(g, raw_tokens))
    decoys = [d for d in gt.get("decoys") or [] if not pages or int(field(d, "page") or 0) in set(pages)]
    for ei in a.unmatched_ext:
        _bump(c["invented_by_decoy"], _decoy_kind(E[ei], decoys))
    if not review_all:
        for gi in omitted:
            for feat in (field(G[gi], "features") or ["none"]):
                _bump(c["omitted_by_feature"], str(feat))

    detail = {
        "pairs": [{"row_id": field(G[p.g], "row_id"), "e": p.e, "rule": p.rule,
                   "signature": checks[p.g].signature, "needs_review": _flagged(E[p.e])} for p in a.pairs],
        "omitted": [field(G[gi], "row_id") for gi in omitted],
        "covered": [field(G[gi], "row_id") for gi in sorted(a.covered)],
        "invented": [{"e": ei, "name": field(E[ei], "name_raw"), "needs_review": _flagged(E[ei])}
                     for ei in a.unmatched_ext],
        "oracle_clinical_headline": oracle_clinical_h,
    }
    return JobRecord(
        job_id=str(field(job, "job_id")), report_id=str(field(job, "report_id")), split=str(field(job, "split")),
        rendering=str(field(job, "rendering")), family=family, counts=c, gt_headline=gt_h,
        confirmed_headline=conf_h, pre_review_headline=pre_h, oracle_headline=oracle_h, detail=detail,
    )


@dataclass
class _NoExtraction:
    job_id: str
    status: str = "failed"
    rows: list = dc_field(default_factory=list)
    report_review_items: list = dc_field(default_factory=list)
    review_all: bool = False
    pre_review_headline: str = "unknown"
    raw_text: str = ""


def crash_record(job: Any, gt: Mapping[str, Any], exc_class: str, scorer: Any, *, oracle_scorer: Any = None
                 ) -> JobRecord:
    """A job whose adapter raised. Recorded by exception class name only (plan 6.11).

    Conservative: a crash is treated as an extraction that produced nothing and told the clinician nothing
    (``review_all`` False), so its GT rows count as silent omissions (M1), its criticals as missed (M2) and its
    headline as unknown (M5). It is never excluded from the denominators."""
    return job_record(job, gt, _NoExtraction(str(field(job, "job_id"))), scorer, oracle_scorer=oracle_scorer,
                      crash=str(exc_class))


# ---------------------------------------------------------------- cells

def aggregate(records: Iterable[JobRecord]) -> dict[str, dict[str, dict[str, Any]]]:
    """Additive counts per split and rendering: ``{split: {rendering: counts}}`` (order-independent)."""
    cells: dict[tuple[str, str], dict[str, Any]] = {}
    for r in records:
        key = (r.split, r.rendering)
        cells[key] = add_counts(cells.get(key) or empty_counts(), r.counts)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for (split, rendering) in sorted(cells):
        out.setdefault(split, {})[rendering] = cells[(split, rendering)]
    return out


def cell_results(counts: Mapping[str, Any]) -> dict[str, Any]:
    """M1-M7 for one split x rendering cell, plus the M1 components, legibility, job and crash counts (the part
    of a cell that heldout may report, plan 6.11). Targets and pass/fail are added by the gate."""
    c = counts
    m1 = c["m1_wrong"] + c["m1_invented"] + c["m1_omitted"]
    return {
        "M1": rate(m1, c["gt_rows"]),
        "M2": rate(c["m2_num"], c["gt_critical"]),
        "M3": rate(c["correct"], c["gt_rows"] + c["unmatched_ext"]),
        "M4": rate(c["m4_num"], c["gt_rows"]),
        "M5": rate(c["m5_num"], c["jobs"]),
        "M6": rate(c["m6_num"], c["m6_den"]),
        "M7": rate(c["m7_num"], c["m7_den"]),
        "components": {"M1": {"wrong": c["m1_wrong"], "invented": c["m1_invented"], "omitted": c["m1_omitted"]}},
        "legibility": rate(c["legible"], c["gt_rows"]),
        "jobs": c["jobs"],
        "crashes": dict(sorted(c["crashes"].items())),
    }


def cell_diagnostics(counts: Mapping[str, Any]) -> dict[str, Any]:
    """Non-gating diagnostics (dev and test only, plan 6.8, 6.11)."""
    c = counts
    sigs = sorted(c["signatures"].items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "M2_post": rate(c["m2_post_num"], c["gt_critical"]),
        "M5_pre": rate(c["m5_pre_num"], c["jobs"]),
        "M5_oracle": rate(c["m5_oracle_num"], c["jobs"]),
        "M5_oracle_clinical": rate(c["m5_oracle_clinical_num"], c["m5_oracle_clinical_den"]),
        "R_NR": rate(c["amb_flagged"], c["amb_rows"]),
        "recall": rate(c["pairs"], c["gt_rows"]),
        "precision": rate(c["pairs"], c["ext_rows"]),
        "field_accuracy": {f: rate(c["field_ok"].get(f, 0), c["pairs"]) for f in FIELDS},
        "per_family_M3": {k: rate(v["num"], v["den"]) for k, v in sorted(c["family_m3"].items())},
        "top_error_signatures": [[s, n] for s, n in sigs[:TOP_SIGNATURES]],
        "invented_by_decoy": dict(sorted(c["invented_by_decoy"].items())),
        "omitted_by_feature": dict(sorted(c["omitted_by_feature"].items())),
        "headline_confusion": dict(sorted(c["headline_confusion"].items())),
        "pre_review_confusion": dict(sorted(c["pre_review_confusion"].items())),
        "match_rules": dict(sorted(c["match_rules"].items())),
        "review_actions": dict(sorted(c["review_actions"].items())),
        "status": dict(sorted(c["status"].items())),
        "review_all_jobs": c["review_all_jobs"],
    }


def runtime_summary(seconds: Sequence[float]) -> dict[str, Any]:
    """Nearest-rank p50 and p95 of job wall times (non-deterministic; the report's runtime section only)."""
    xs = sorted(float(s) for s in seconds)
    if not xs:
        return {"n": 0, "p50": None, "p95": None}

    def pct(q: float) -> float:
        return xs[max(0, math.ceil(q * len(xs)) - 1)]

    return {"n": len(xs), "p50": pct(0.50), "p95": pct(0.95)}


__all__ = [
    "HEADLINES", "JobRecord", "METRIC_IDS", "Z95", "add_counts", "aggregate", "cell_diagnostics", "cell_results",
    "confirmed_headline", "crash_record", "empty_counts", "gt_headline", "job_pages", "job_record", "rate",
    "runtime_summary", "scope_rows", "sum_counts", "three_level", "wilson",
]
