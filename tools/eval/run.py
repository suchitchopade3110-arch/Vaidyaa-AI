"""Harness CLI: ``python -m tools.eval.run`` and ``... run aggregate`` (P1 plan 6.1, 6.9 to 6.12, 7.2, 8).

Per job: ``adapter.extract`` -> ``match.align`` -> ``review_sim.simulate`` -> ``scorer.score`` ->
``metrics.job_record`` (all inside ``metrics.job_record``); job counts are summed per (split, rendering) cell, and
the gate and the report are computed from those additive counts only. Adapters are reached only through the
registry (``tools.eval.adapters``); this is the one module that may do so.

Profiles: ``ci`` (test), ``dev``, ``full``, ``heldout``, ``smoke`` (``config.PROFILES``). ``--splits``,
``--renderings``, ``--sample`` (``N`` = first N reports by id, or ``scan=0.5,photo=0.5`` fractions),
``--sample-n N``, ``--shard k/n`` and ``--jobs N`` override them. A run that overrides anything and does not name
a gate defaults to ``--gate none``, so a partial run is never compared with the full baseline by accident.

Exit codes: 0 pass; 1 gate failed; 2 harness or config error (``HarnessConfigError``, missing baseline, corpus
or file, fingerprint mismatch, import failure); 3 hygiene violation (heldout).

Heldout hygiene (plan 6.11): heldout jobs run inside ``hygiene.quiet()``, record exceptions by class name only,
never leave the worker as anything but additive counts, and reach disk only as whitelisted, redacted cells.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import multiprocessing as mp
import pathlib
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from tools.eval import adapters as registry
from tools.eval import clinical, gate, hygiene, report
from tools.eval import config as cfg
from tools.eval import generate as gen
from tools.eval import metrics as me
from tools.eval import targets as tg
from tools.eval.adapters.base import EvalJob

SHARD_SCHEMA = "vaidya-eval-shard/1"
SAMPLE_SALT = "img-sample-v1"
SPLIT_ORDER = ("dev", "test", "heldout")
IMAGE_RENDERINGS = ("scan", "photo", "rot90")
PROFILE_GATE = {"ci": "config", "dev": "none", "full": "report", "heldout": "report", "smoke": "none"}
DEFAULT_GATE_FILE = cfg.EVAL_DIR / "gate.json"


_STATE: dict[str, bool] = {"heldout": False}


class HarnessError(RuntimeError):
    """A harness problem that is not a gate result (missing corpus, bad arguments): exit code 2."""


# ---------------------------------------------------------------- sampling (plan 6.12)

def _sample_key(report_id: str) -> str:
    return hashlib.sha256(f"{report_id}|{SAMPLE_SALT}".encode()).hexdigest()


def sample_ids(entries: Sequence[Mapping[str, Any]], fraction: float) -> list[str]:
    """Stratified by scenario class: within each class the reports are ranked by SHA-256(``id|img-sample-v1``) and
    the first round(fraction x class size) are taken (round half up). Stable, and independent of every report
    outside the class. Returns sorted ids."""
    if fraction >= 1.0:
        return sorted(str(e["id"]) for e in entries)
    by_class: dict[str, list[str]] = {}
    for e in entries:
        by_class.setdefault(str(e["scenario_class"]), []).append(str(e["id"]))
    picked: list[str] = []
    for cls in sorted(by_class):
        ids = sorted(by_class[cls], key=_sample_key)
        picked += ids[: int(fraction * len(ids) + 0.5)]
    return sorted(picked)


# ---------------------------------------------------------------- run configuration

def parse_sample(value: str | None) -> tuple[dict[str, float], int | None]:
    """``--sample``: an integer N (first N reports) or ``rendering=fraction,...``."""
    if value is None or value == "":
        return {}, None
    if value.isdigit():
        return {}, int(value)
    fractions: dict[str, float] = {}
    for part in value.split(","):
        k, sep, v = part.partition("=")
        if not sep or k.strip() not in tg.RENDERINGS:
            raise HarnessError(f"--sample: expected N or rendering=fraction pairs, got {value!r}")
        try:
            f = float(v)
        except ValueError:
            raise HarnessError(f"--sample: bad fraction {v!r}") from None
        if not 0.0 <= f <= 1.0:
            raise HarnessError(f"--sample: fraction {f} outside [0, 1]")
        fractions[k.strip()] = f
    return fractions, None


def parse_shard(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        k, n = (int(x) for x in value.split("/"))
    except ValueError:
        raise HarnessError(f"--shard: expected k/n, got {value!r}") from None
    if not 1 <= k <= n:
        raise HarnessError(f"--shard: need 1 <= k <= n, got {value!r}")
    return k, n


def _csv_list(value: str | None) -> list[str] | None:
    return [x for x in value.split(",") if x] if value else None


def resolve(args: argparse.Namespace) -> dict[str, Any]:
    """Profile plus overrides as one plain dict."""
    profile = cfg.PROFILES.get(args.profile)
    if profile is None:
        raise HarnessError(f"unknown profile {args.profile!r}; known: {', '.join(sorted(cfg.PROFILES))}")
    splits = _csv_list(args.splits) or list(profile["splits"])
    bad = [s for s in splits if s not in cfg.SPLIT_SIZES]
    if bad:
        raise HarnessError(f"unknown split(s): {','.join(bad)}")
    renderings = _csv_list(args.renderings) or list(tg.RENDERINGS)
    bad = [r for r in renderings if r not in tg.RENDERINGS]
    if bad:
        raise HarnessError(f"unknown rendering(s): {','.join(bad)}")
    fractions = {r: float(profile[r]) for r in tg.RENDERINGS}
    over, first = parse_sample(args.sample)
    fractions.update(over)
    if args.sample_n is not None:
        first = int(args.sample_n)
    if first is None:
        first = profile.get("first")
    overridden = bool(args.splits or args.renderings or args.sample or args.sample_n is not None or args.shard)
    return {
        "profile": args.profile, "splits": [s for s in SPLIT_ORDER if s in splits],
        "renderings": [r for r in tg.RENDERINGS if r in renderings],
        "samples": {"fractions": {r: fractions[r] for r in tg.RENDERINGS}, "first": first},
        "shard": parse_shard(args.shard), "overridden": overridden,
    }


# ---------------------------------------------------------------- jobs

def _gt_path(root: pathlib.Path, split: str, ent: Mapping[str, Any]) -> pathlib.Path:
    return root / split / ent["files"]["gt"]["path"]


def build_jobs(split: str, manifest: Mapping[str, Any], root: pathlib.Path, run: Mapping[str, Any],
               gts: Mapping[str, Mapping[str, Any]] | None = None) -> list[tuple[EvalJob, pathlib.Path]]:
    """Sorted ``(EvalJob, gt_path)`` for one split: the plan 6.1 job units for the selected renderings.

    ``gts`` is only used to read the input sex and age; when absent they are read from the GT file."""
    entries = sorted(manifest["reports"], key=lambda e: e["id"])
    if run["samples"]["first"]:
        entries = entries[: int(run["samples"]["first"])]
    fractions = run["samples"]["fractions"]
    jobs: list[tuple[EvalJob, pathlib.Path]] = []
    for rendering in run["renderings"]:
        if rendering == "csv":
            chosen = [e for e in entries if e["is_csv"]]
            chosen = [e for e in chosen if e["id"] in set(sample_ids(chosen, fractions["csv"]))]
        elif rendering == "native":
            chosen = [e for e in entries if not e["is_csv"]]
            chosen = [e for e in chosen if e["id"] in set(sample_ids(chosen, fractions["native"]))]
        elif rendering == "rot90":
            chosen = [e for e in entries if e["rot90"] and not e["is_csv"]]
            chosen = [e for e in chosen if e["id"] in set(sample_ids(chosen, fractions["rot90"]))]
        else:  # scan, photo: sampled among all non-csv reports
            pool = [e for e in entries if not e["is_csv"]]
            keep = set(sample_ids(pool, fractions[rendering]))
            chosen = [e for e in pool if e["id"] in keep]
        for e in chosen:
            gtp = _gt_path(root, split, e)
            sex, age = _demographics(gtp)
            files = e["files"].get(rendering)
            if rendering == "photo":
                for n, page in enumerate((files or {}).get("pages", []), 1):
                    jobs.append((EvalJob(f"{e['id']}:photo:p{n}", e["id"], split, "photo",
                                         (str(root / split / page["path"]),), (n,), sex, age), gtp))
            else:
                path = str(root / split / files["path"]) if files else ""
                jobs.append((EvalJob(f"{e['id']}:{rendering}", e["id"], split, rendering, (path,), (), sex, age), gtp))
    jobs.sort(key=lambda t: t[0].job_id)
    return jobs


def _demographics(gt_path: pathlib.Path) -> tuple[str | None, int | None]:
    p = json.loads(gt_path.read_text(encoding="utf-8"))["patient"]
    return p.get("input_sex"), p.get("input_age")


def apply_shard(jobs: list[tuple[EvalJob, pathlib.Path]], shard: tuple[int, int] | None
                ) -> list[tuple[EvalJob, pathlib.Path]]:
    """Deterministic partition of each cell's (sorted) jobs: the i-th job of a cell goes to shard i mod n."""
    if shard is None:
        return jobs
    k, n = shard
    seen: dict[tuple[str, str], int] = {}
    out = []
    for job, gtp in jobs:
        key = (job.split, job.rendering)
        i = seen.get(key, 0)
        seen[key] = i + 1
        if i % n == k - 1:
            out.append((job, gtp))
    return out


def _needed_images(manifest: Mapping[str, Any], run: Mapping[str, Any]) -> dict[tuple[str, ...], list[str]]:
    """Report ids grouped by the exact set of image renderings this run needs for them."""
    entries = sorted(manifest["reports"], key=lambda e: e["id"])
    if run["samples"]["first"]:
        entries = entries[: int(run["samples"]["first"])]
    fr = run["samples"]["fractions"]
    pool = [e for e in entries if not e["is_csv"]]
    need: dict[str, set[str]] = {}
    for r in ("scan", "photo"):
        if r in run["renderings"]:
            for i in sample_ids(pool, fr[r]):
                need.setdefault(i, set()).add(r)
    if "rot90" in run["renderings"]:
        r90 = [e for e in pool if e["rot90"]]
        for i in sample_ids(r90, fr["rot90"]):
            need.setdefault(i, set()).add("rot90")
    groups: dict[tuple[str, ...], list[str]] = {}
    for i, rs in sorted(need.items()):
        groups.setdefault(tuple(sorted(rs)), []).append(i)
    return groups


def prepare_corpus(split: str, run: Mapping[str, Any], root: pathlib.Path, *, materialize: bool
                   ) -> Mapping[str, Any]:
    """Make (or, with ``materialize=False``, just check) the files a run needs; return the manifest."""
    fp = gen.fingerprint(split)
    if materialize:
        try:
            manifest = gen.materialize(split, "native", root=root)
            for rset, ids in _needed_images(manifest, run).items():
                manifest = gen.materialize(split, list(rset), root=root, ids=ids)
        except Exception as exc:  # noqa: BLE001
            detail = hygiene.crash_class(exc) if hygiene.is_heldout(split) else repr(exc)
            raise HarnessError(f"{split}: corpus generation failed ({detail})") from None
    else:
        manifest = gen.load_manifest(split, root)
        if manifest is None:
            raise HarnessError(f"{split}: no corpus manifest under {root}; run `make eval-corpus` "
                               "(python -m tools.eval.generate)")
    if manifest.get("fingerprint") != fp:
        raise HarnessError(f"{split}: corpus fingerprint does not match the generator "
                           "(stale corpus); regenerate with `make eval-corpus`")
    return manifest


def check_files(split: str, jobs: Sequence[tuple[EvalJob, pathlib.Path]]) -> None:
    """Every input file and GT file of every job must exist before any work starts (exit 2 otherwise)."""
    missing = 0
    for job, gtp in jobs:
        if not gtp.exists() or not all(f and pathlib.Path(f).exists() for f in job.files):
            missing += 1
    if missing:
        raise HarnessError(f"{split}: {missing} job input file(s) missing from the corpus; "
                           "regenerate with `make eval-corpus`")


# ---------------------------------------------------------------- one job

def process_job(adapter: Any, scorer: Any, oracle: Any, job: EvalJob, gt: Mapping[str, Any]
                ) -> tuple[me.JobRecord, float, tuple[int, int]]:
    """Run one job. Heldout jobs run quietly and record exceptions by class name only. Returns the record, the wall
    seconds and the OCR cache (hits, misses) of the job."""
    ctx = hygiene.quiet() if hygiene.is_heldout(job.split) else contextlib.nullcontext()
    t0 = time.perf_counter()
    hits = misses = 0
    with ctx:
        ext, crash = None, None
        try:
            ext = adapter.extract(job)
        except Exception as exc:  # noqa: BLE001
            crash = hygiene.crash_class(exc)
        if crash is None:
            cache = (getattr(ext, "timing", None) or {}).get("ocr_cache") or {}
            hits, misses = int(cache.get("hits", 0)), int(cache.get("misses", 0))
            try:
                rec = me.job_record(job, gt, ext, scorer, oracle_scorer=oracle)
            except ValueError:
                raise
            except Exception as exc:  # noqa: BLE001
                crash = hygiene.crash_class(exc)
        if crash is not None:
            rec = me.crash_record(job, gt, crash, scorer, oracle_scorer=oracle)
    return rec, time.perf_counter() - t0, (hits, misses)


def is_failure(rec: me.JobRecord) -> bool:
    c = rec.counts
    return bool(c["m1_wrong"] or c["m1_invented"] or c["m1_omitted"] or c["m2_num"] < c["gt_critical"]
                or c["crashes"] or rec.confirmed_headline != rec.gt_headline)


def dump_failure(out_dir: pathlib.Path, rec: me.JobRecord) -> None:
    """Per-job failure dump (dev and test only); the heldout guard is the choke point."""
    hygiene.guard_per_report_output(rec.split)
    doc = {"job_id": rec.job_id, "family": rec.family, "gt_headline": rec.gt_headline,
           "confirmed_headline": rec.confirmed_headline, "pre_review_headline": rec.pre_review_headline,
           "oracle_headline": rec.oracle_headline,
           "counts": {k: v for k, v in rec.counts.items() if not isinstance(v, dict) and v},
           "detail": rec.detail}
    path = out_dir / f"{rec.job_id.replace(':', '_')}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    report.atomic_write_text(path, json.dumps(doc, sort_keys=True, indent=2) + "\n")


# ---------------------------------------------------------------- worker pool

_W: dict[str, Any] = {}


def _init_worker(adapter_name: str, scorer_name: str, ocr_cache: str) -> None:
    try:
        _W["adapter"] = registry.get_adapter(adapter_name, ocr_cache=None if ocr_cache == "off" else "default")
        _W["scorer"] = registry.get_scorer(scorer_name)
        _W["oracle"] = clinical.ClinicalRulesScorer()
    except Exception as exc:  # noqa: BLE001 - reported by the parent; raising here would respawn workers forever
        _W["error"] = (type(exc).__name__, str(exc))


def _work(task: tuple[EvalJob, str]) -> dict[str, Any]:
    if "error" in _W:
        return {"error": _W["error"]}
    job, gt_path = task
    try:
        gt = json.loads(pathlib.Path(gt_path).read_text(encoding="utf-8"))
        rec, secs, cache = process_job(_W["adapter"], _W["scorer"], _W["oracle"], job, gt)
    except Exception as exc:  # noqa: BLE001
        return {"error": (type(exc).__name__, "details withheld" if hygiene.is_heldout(job.split) else str(exc))}
    heldout = hygiene.is_heldout(job.split)
    return {"split": job.split, "rendering": job.rendering, "counts": rec.counts, "secs": secs, "cache": cache,
            "ident": (_W["adapter"].name, _W["adapter"].version, _W["scorer"].name, _W["scorer"].version),
            "record": None if heldout else rec}


def _config_error_type() -> type[BaseException]:
    from tools.eval.adapters.pipeline_v0 import HarnessConfigError

    return HarnessConfigError


class _Outcome:
    def __init__(self) -> None:
        self.cells: dict[tuple[str, str], dict[str, Any]] = {}
        self.seconds: dict[str, list[float]] = {}
        self.hits = self.misses = 0
        self.ident: tuple[str, str, str, str] | None = None
        self.records: list[me.JobRecord] = []

    def add(self, split: str, rendering: str, counts: Mapping[str, Any], secs: float, cache: tuple[int, int],
            record: me.JobRecord | None) -> None:
        key = (split, rendering)
        self.cells[key] = me.add_counts(self.cells.get(key) or me.empty_counts(), counts)
        self.seconds.setdefault(rendering, []).append(secs)
        self.hits += cache[0]
        self.misses += cache[1]
        if record is not None:
            self.records.append(record)


def execute(tasks: list[tuple[EvalJob, pathlib.Path]], args: argparse.Namespace, *, adapter: Any = None,
            scorer: Any = None, oracle: Any = None, keep_records: bool = False) -> _Outcome:
    out = _Outcome()
    if not tasks:
        return out
    if adapter is None and args.jobs > 1:
        ctx = mp.get_context("spawn")
        with ctx.Pool(args.jobs, initializer=_init_worker,
                      initargs=(args.adapter, args.scorer, args.ocr_cache)) as pool:
            results = pool.imap(_work, [(j, str(g)) for j, g in tasks], 1)
            for res in results:
                if "error" in res:
                    _raise_worker_error(res["error"])
                out.ident = out.ident or tuple(res["ident"])
                out.add(res["split"], res["rendering"], res["counts"], res["secs"], tuple(res["cache"]),
                        res["record"] if keep_records else None)
        return out
    own = adapter is None
    try:
        if own:
            adapter = registry.get_adapter(args.adapter, ocr_cache=None if args.ocr_cache == "off" else "default")
        if scorer is None:
            scorer = registry.get_scorer(args.scorer)
        oracle = oracle or clinical.ClinicalRulesScorer()
        out.ident = (str(adapter.name), str(adapter.version), str(scorer.name), str(scorer.version))
        gts: dict[str, Mapping[str, Any]] = {}
        for job, gtp in tasks:
            gt = gts.get(str(gtp))
            if gt is None:
                gt = gts[str(gtp)] = json.loads(pathlib.Path(gtp).read_text(encoding="utf-8"))
            rec, secs, cache = process_job(adapter, scorer, oracle, job, gt)
            out.add(job.split, job.rendering, rec.counts, secs, cache, rec if keep_records else None)
    finally:
        if own and hasattr(adapter, "close"):
            adapter.close()
    return out


def _raise_worker_error(err: tuple[str, str]) -> None:
    name, msg = err
    if name == _config_error_type().__name__:
        raise _config_error_type()(msg)
    raise HarnessError(f"worker failed: {name}: {msg}")


# ---------------------------------------------------------------- metrics document

def git_sha() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cfg.REPO_ROOT, capture_output=True, text=True,
                             timeout=10, check=False)
        return res.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def cell_doc(split: str, rendering: str, counts: Mapping[str, Any]) -> dict[str, Any]:
    cell = tg.annotate(me.cell_results(counts), rendering)
    return hygiene.redact_heldout(cell) if hygiene.is_heldout(split) else cell


def build_metrics(run: Mapping[str, Any], cells: Mapping[tuple[str, str], Mapping[str, Any]] | None,
                  redacted: Mapping[tuple[str, str], Mapping[str, Any]] | None, corpus: Mapping[str, Any],
                  ident: Sequence[str] | None, gate_mode: str, runtime: Mapping[str, Any]) -> dict[str, Any]:
    """The ``metrics.json`` document. ``cells`` holds additive counts (dev, test); ``redacted`` holds heldout
    whitelisted cells (never counts)."""
    results: dict[str, dict[str, Any]] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    for (split, rendering), counts in sorted((cells or {}).items()):
        results.setdefault(split, {})[rendering] = cell_doc(split, rendering, counts)
        if not hygiene.is_heldout(split):
            diagnostics.setdefault(split, {})[rendering] = me.cell_diagnostics(counts)
    for (split, rendering), cell in sorted((redacted or {}).items()):
        results.setdefault(split, {})[rendering] = hygiene.redact_heldout(tg.annotate(cell, rendering))
    adapter, scorer = ({"name": ident[0], "version": ident[1]}, {"name": ident[2], "version": ident[3]}) \
        if ident else ({"name": None, "version": None}, {"name": None, "version": None})
    return {
        "schema": report.METRICS_SCHEMA,
        "run": {"profile": run["profile"], "splits": list(run["splits"]), "renderings": list(run["renderings"]),
                "samples": run["samples"], "adapter": adapter, "scorer": scorer, "gate_mode": gate_mode,
                "git_sha": git_sha()},
        "corpus": dict(sorted(corpus.items())),
        "results": results,
        "diagnostics": diagnostics,
        "gate": {"mode": gate_mode, "passed": True, "failures": []},
        "runtime": dict(runtime),
    }


def runtime_doc(out: _Outcome, wall: float) -> dict[str, Any]:
    return {"wall_s": round(wall, 2), "ocr_cache": {"hits": out.hits, "misses": out.misses},
            "per_rendering": {r: me.runtime_summary(s) for r, s in sorted(out.seconds.items())}}


def apply_gate(metrics: dict[str, Any], mode: str, gate_cfg: Mapping[str, Any], baseline_path: pathlib.Path
               ) -> dict[str, Any]:
    baseline = gate.load_baseline(baseline_path) if mode in ("ratchet", "targets") else None
    res = gate.evaluate(mode, metrics, split=gate_cfg["split"], baseline=baseline, tolerance=gate_cfg["tolerance"])
    metrics["gate"] = res
    return res


def resolve_gate_mode(args: argparse.Namespace, run: Mapping[str, Any], gate_cfg: Mapping[str, Any]) -> str:
    if args.gate:
        return args.gate
    if run["overridden"]:
        return "none"
    mode = PROFILE_GATE.get(run["profile"], "none")
    return gate_cfg["mode"] if mode == "config" else mode


def corpus_doc(split: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    return {"fingerprint": manifest["fingerprint"], "seed": manifest["seed"], "n": manifest["n"],
            "reports": len(manifest["reports"])}


# ---------------------------------------------------------------- output

def write_outputs(metrics: Mapping[str, Any], out_dir: pathlib.Path) -> None:
    """Both files or neither: each is atomic, and metrics.json is written last."""
    report.write_report(out_dir / "report.md", metrics)
    report.write_metrics(out_dir / "metrics.json", metrics)


def summary_lines(metrics: Mapping[str, Any]) -> list[str]:
    """Aggregate-only console summary (safe for heldout)."""
    lines = []
    for split in SPLIT_ORDER:
        for rendering, cell in sorted(metrics["results"].get(split, {}).items()):
            parts = [f"{m} {report.pct(cell[m]['value'])} {report.verdict(cell[m])}" for m in tg.METRICS]
            crashes = sum(cell["crashes"].values()) if "crashes" in cell else 0
            lines.append(f"{split}/{rendering}: jobs={cell['jobs']} crashes={crashes} " + "; ".join(parts))
    return lines


# ---------------------------------------------------------------- shards and aggregate

def shard_doc(run: Mapping[str, Any], corpus: Mapping[str, Any], out: _Outcome, ident: Sequence[str] | None,
              wall: float) -> dict[str, Any]:
    cells: dict[str, dict[str, Any]] = {}
    for (split, rendering), counts in sorted(out.cells.items()):
        if hygiene.is_heldout(split):  # never counts, never job records: the redacted cell only
            cells.setdefault(split, {})[rendering] = {"redacted": hygiene.redact_heldout(me.cell_results(counts))}
        else:
            cells.setdefault(split, {})[rendering] = {"counts": counts}
    return {"schema": SHARD_SCHEMA, "run": {k: run[k] for k in ("profile", "splits", "renderings", "samples")},
            "corpus": dict(sorted(corpus.items())), "ident": list(ident) if ident else None, "cells": cells,
            "runtime": {"seconds": {r: s for r, s in sorted(out.seconds.items())}, "hits": out.hits,
                        "misses": out.misses, "wall": wall}}


def _merge_redacted(a: dict[str, Any] | None, b: Mapping[str, Any]) -> dict[str, Any]:
    """Sum two redacted heldout cells (all fields are counts); intervals are recomputed from the sums."""
    if a is None:
        a = {k: v for k, v in b.items()}
        return a
    out: dict[str, Any] = {}
    for m in me.METRIC_IDS:
        out[m] = me.rate(int(a[m]["num"]) + int(b[m]["num"]), int(a[m]["den"]) + int(b[m]["den"]))
    out["legibility"] = me.rate(int(a["legibility"]["num"]) + int(b["legibility"]["num"]),
                                int(a["legibility"]["den"]) + int(b["legibility"]["den"]))
    out["components"] = {"M1": {k: int(a["components"]["M1"][k]) + int(b["components"]["M1"][k])
                                for k in ("wrong", "invented", "omitted")}}
    out["jobs"] = int(a["jobs"]) + int(b["jobs"])
    crashes = dict(a["crashes"])
    for k, v in b["crashes"].items():
        crashes[k] = crashes.get(k, 0) + int(v)
    out["crashes"] = dict(sorted(crashes.items()))
    return out


def aggregate_shards(in_dir: pathlib.Path) -> dict[str, Any]:
    """Merge shard files into the inputs of ``build_metrics``."""
    files = sorted(in_dir.glob("*.json"))
    if not files:
        raise HarnessError(f"{in_dir}: no shard files")
    shards = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            raise HarnessError(f"{f.name}: not valid JSON") from None
        if doc.get("schema") != SHARD_SCHEMA:
            raise HarnessError(f"{f.name}: not a {SHARD_SCHEMA} shard")
        shards.append(doc)
    first = shards[0]
    for s in shards[1:]:
        for key in ("profile", "samples"):
            if s["run"][key] != first["run"][key]:
                raise HarnessError(f"shards disagree on run.{key}")
        if s["ident"] != first["ident"]:
            raise HarnessError("shards were produced by different adapters or scorers")
    cells: dict[tuple[str, str], Any] = {}
    redacted: dict[tuple[str, str], Any] = {}
    corpus: dict[str, Any] = {}
    seconds: dict[str, list[float]] = {}
    hits = misses = 0
    wall = 0.0
    for s in shards:
        for split, c in s["corpus"].items():
            if corpus.setdefault(split, c) != c:
                raise HarnessError(f"shards disagree on the {split} corpus fingerprint")
        for split, by_r in s["cells"].items():
            for rendering, cell in by_r.items():
                key = (split, rendering)
                if "counts" in cell:
                    cells[key] = me.add_counts(cells.get(key) or me.empty_counts(), cell["counts"])
                else:
                    redacted[key] = _merge_redacted(redacted.get(key), cell["redacted"])
        for r, secs in s["runtime"]["seconds"].items():
            seconds.setdefault(r, []).extend(secs)
        hits += int(s["runtime"]["hits"])
        misses += int(s["runtime"]["misses"])
        wall += float(s["runtime"]["wall"])
    run = {
        "profile": first["run"]["profile"], "samples": first["run"]["samples"],
        "splits": [x for x in SPLIT_ORDER if any(x in s["run"]["splits"] for s in shards)],
        "renderings": [r for r in tg.RENDERINGS if any(r in s["run"]["renderings"] for s in shards)],
    }
    out = _Outcome()
    out.seconds, out.hits, out.misses = seconds, hits, misses
    return {"run": run, "cells": cells, "redacted": redacted, "corpus": corpus, "ident": first["ident"],
            "outcome": out, "wall": wall}


# ---------------------------------------------------------------- CLI

def _add_common(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--gate", choices=gate.MODES, default=None, help="gate mode (default: per profile)")
    ap.add_argument("--gate-config", default=str(DEFAULT_GATE_FILE))
    ap.add_argument("--baseline", default=None, help="baseline file (default: gate config)")
    ap.add_argument("--out", default=str(cfg.EVAL_DIR), help="directory for metrics.json and report.md")


def run_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m tools.eval.run", description="P1 eval harness (plan 6)")
    ap.add_argument("--profile", default="ci", choices=sorted(cfg.PROFILES))
    ap.add_argument("--splits", default=None, help="comma list overriding the profile's splits")
    ap.add_argument("--renderings", default=None, help="comma list of native,csv,scan,photo,rot90")
    ap.add_argument("--sample", default=None, help="N (first N reports) or rendering=fraction,...")
    ap.add_argument("--sample-n", type=int, default=None, help="first N reports of each split")
    ap.add_argument("--shard", default=None, help="k/n: run the k-th of n partitions of each cell")
    ap.add_argument("--shard-out", default=None, help="write this run's shard file instead of metrics and report")
    ap.add_argument("--jobs", type=int, default=1, help="worker processes (spawn); default 1")
    ap.add_argument("--adapter", default=registry.DEFAULT_ADAPTER)
    ap.add_argument("--scorer", default=registry.DEFAULT_SCORER)
    ap.add_argument("--ocr-cache", choices=("on", "off"), default="on")
    ap.add_argument("--corpus-dir", default=str(cfg.CORPUS_DIR))
    ap.add_argument("--no-materialize", action="store_true",
                    help="never generate corpus files; a missing file or stale manifest exits 2")
    ap.add_argument("--dump-failures", default=None, help="directory for per-job failure dumps (never heldout)")
    ap.add_argument("--update-baseline", "--record-baseline", dest="update_baseline", action="store_true",
                    help="record this run as the baseline (needs a DECISIONS line)")
    _add_common(ap)
    return ap


def aggregate_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python -m tools.eval.run aggregate", description="merge shard files")
    ap.add_argument("--in", dest="in_dir", required=True)
    _add_common(ap)
    return ap


def _baseline_path(args: argparse.Namespace, gate_cfg: Mapping[str, Any]) -> pathlib.Path:
    p = pathlib.Path(args.baseline or gate_cfg["baseline"])
    return p if p.is_absolute() else cfg.REPO_ROOT / p


def _finish(metrics: dict[str, Any], mode: str, args: argparse.Namespace, gate_cfg: Mapping[str, Any],
            *, update_baseline: bool = False) -> int:
    base_path = _baseline_path(args, gate_cfg)
    if update_baseline:
        metrics["gate"] = {"mode": "none", "passed": True, "failures": []}
        recorded = {k: v for k, v in metrics.items() if k not in ("runtime", "gate")}
        recorded["run"] = dict(recorded["run"], gate_mode="baseline")
        report.write_metrics(base_path, recorded)
        print(f"baseline recorded: {base_path}")
    else:
        apply_gate(metrics, mode, gate_cfg, base_path)
    out_dir = pathlib.Path(args.out)
    write_outputs(metrics, out_dir)
    for line in summary_lines(metrics):
        print(line)
    g = metrics["gate"]
    print(f"gate {g['mode']}: {'PASS' if g['passed'] else 'FAIL'}")
    for f in g["failures"]:
        print(f"  {f['kind']} {f['split']}/{f['rendering']} {f['metric']}: current {f['current']} "
              f"({f.get('target') or 'baseline ' + str(f.get('baseline'))})")
    print(f"wrote {out_dir / 'metrics.json'} and {out_dir / 'report.md'}")
    return gate.exit_code(g)


def run_main(argv: Sequence[str], *, adapter: Any = None, scorer: Any = None, oracle: Any = None) -> int:
    args = run_parser().parse_args(list(argv))
    run = resolve(args)
    _STATE["heldout"] = "heldout" in run["splits"]
    dump = args.dump_failures
    if dump is None and run["profile"] == "dev" and "heldout" not in run["splits"]:
        dump = str(cfg.EVAL_DIR / "failures" / "dev")
    hygiene.check_cli(run["splits"], args.dump_failures)  # before any work
    gate_cfg = gate.load_config(args.gate_config)
    mode = resolve_gate_mode(args, run, gate_cfg)
    if args.update_baseline and args.shard_out:
        raise HarnessError("--update-baseline cannot be combined with --shard-out")
    if mode in ("ratchet", "targets") and not args.update_baseline and not args.shard_out:
        if gate_cfg["split"] not in run["splits"]:
            raise HarnessError(f"the {mode} gate needs the {gate_cfg['split']!r} split; pass --gate none or "
                               f"--splits {gate_cfg['split']}")
        gate.load_baseline(_baseline_path(args, gate_cfg))  # fail fast, before any work
    root = pathlib.Path(args.corpus_dir)
    t0 = time.perf_counter()
    manifests: dict[str, Mapping[str, Any]] = {}
    tasks: list[tuple[EvalJob, pathlib.Path]] = []
    for split in run["splits"]:
        manifests[split] = prepare_corpus(split, run, root, materialize=not args.no_materialize)
        jobs = apply_shard(build_jobs(split, manifests[split], root, run), run["shard"])
        check_files(split, jobs)
        tasks += jobs
    out = execute(tasks, args, adapter=adapter, scorer=scorer, oracle=oracle, keep_records=bool(dump))
    if dump:
        d = pathlib.Path(dump)
        for rec in sorted(out.records, key=lambda r: r.job_id):
            if is_failure(rec):
                dump_failure(d, rec)
    corpus = {s: corpus_doc(s, m) for s, m in manifests.items()}
    wall = time.perf_counter() - t0
    if args.shard_out:
        report.atomic_write_text(args.shard_out, json.dumps(shard_doc(run, corpus, out, out.ident, wall),
                                                            sort_keys=True, indent=2) + "\n")
        print(f"wrote shard {args.shard_out}")
        return gate.EXIT_OK
    counts = {k: v for k, v in out.cells.items() if not hygiene.is_heldout(k[0])}
    redacted = {k: hygiene.redact_heldout(me.cell_results(v)) for k, v in out.cells.items()
                if hygiene.is_heldout(k[0])}
    metrics = build_metrics(run, counts, redacted, corpus, out.ident, mode, runtime_doc(out, wall))
    return _finish(metrics, mode, args, gate_cfg, update_baseline=args.update_baseline)


def aggregate_main(argv: Sequence[str]) -> int:
    args = aggregate_parser().parse_args(list(argv))
    gate_cfg = gate.load_config(args.gate_config)
    merged = aggregate_shards(pathlib.Path(args.in_dir))
    mode = args.gate or "report"
    run = dict(merged["run"], overridden=True)
    metrics = build_metrics(run, merged["cells"], merged["redacted"], merged["corpus"], merged["ident"], mode,
                            runtime_doc(merged["outcome"], merged["wall"]))
    return _finish(metrics, mode, args, gate_cfg)


def main(argv: Sequence[str] | None = None, *, adapter: Any = None, scorer: Any = None, oracle: Any = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    aggregating = bool(argv) and argv[0] == "aggregate"
    if argv and argv[0] in ("aggregate", "run"):
        argv = argv[1:]
    _STATE["heldout"] = False
    try:
        if aggregating:
            return aggregate_main(argv)
        return run_main(argv, adapter=adapter, scorer=scorer, oracle=oracle)
    except hygiene.HygieneError as exc:
        print(f"hygiene violation: {exc}", file=sys.stderr)
        return gate.EXIT_HYGIENE
    except gate.GateConfigError as exc:
        print(f"harness error: {exc}", file=sys.stderr)
        return gate.EXIT_ERROR
    except HarnessError as exc:
        print(f"harness error: {exc}", file=sys.stderr)
        return gate.EXIT_ERROR
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, _config_error_type()):
            print(f"harness error: {exc}", file=sys.stderr)
        elif _STATE["heldout"]:  # never print a message that might carry heldout content
            print(f"harness error: {type(exc).__name__} (details withheld)", file=sys.stderr)
        else:
            print(f"harness error: {exc!r}", file=sys.stderr)
        return gate.EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
