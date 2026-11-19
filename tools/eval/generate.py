"""Corpus plan, splits, manifest with split fingerprints, lazy materialisation and ``--verify`` (P1 plan 3.1, 4.3, 5, 8).

GT-side module: imports nothing from ``app``, ``clinical_reference`` or ``tools.eval.adapters``.

* ``plan_split`` is a pure function of the seeds in ``config.py``: 400 reports = dev 240 / test 80 / heldout 80.
  dev+test (320) are drawn from ``DEVTEST_SEED`` and heldout (80) from ``HELDOUT_SEED``.
* ``materialize`` writes git-ignored files under ``eval/corpus/<split>/`` (gt, native, csv, scan, photo, rot90,
  render_meta) and ``manifest.json``; files whose recorded SHA-256 and split fingerprint match are skipped.
* Heldout hygiene: nothing per-report is ever printed for heldout (aggregate counts only) and heldout outputs are
  written only below ``<root>/heldout/``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata as md
import json
import os
import pathlib
import random
import sys
import time
from dataclasses import dataclass
from typing import Any

from tools.eval import config as cfg
from tools.eval import gt_schema, render
from tools.eval import scenarios as sc
from tools.eval.layouts import FAMILIES, base, csv_export
from tools.eval.layouts.base import PrintedReport

MANIFEST_SCHEMA = "vaidya-eval-manifest/1"
RENDERINGS = ("native", "csv", "scan", "photo", "rot90")
PAGE_ORDER = {1: (1, 2, 3), 2: (2, 1, 3), 3: (3, 2, 1)}  # planned target first, then the nearest feasible ones

# modules whose source feeds the split fingerprint (relative to the repo root)
_COMMON_MODULES = (
    "tools/eval/config.py", "tools/eval/catalogue.py", "tools/eval/critical_table.py", "tools/eval/units.py",
    "tools/eval/gt_schema.py", "tools/eval/clinical.py", "tools/eval/scenarios.py", "tools/eval/render.py",
    "tools/eval/generate.py", "tools/eval/layouts/__init__.py", "tools/eval/layouts/base.py",
    "tools/eval/layouts/templates.py", "tools/eval/layouts/generic.py", "tools/eval/layouts/csv_export.py",
)
_HELDOUT_MODULES = ("tools/eval/layouts/heldout/__init__.py", "tools/eval/layouts/heldout/families.py")


# ---------------------------------------------------------------- plan

@dataclass(frozen=True)
class ReportPlan:
    report_id: str
    split: str
    index: int  # 0-based position within the split; seeds derive from (master, split, index)
    family: str
    scenario_class: str
    target_pages: int
    patient: sc.Patient
    rot90: int  # 0, +90 or -90

    @property
    def is_csv(self) -> bool:
        return self.family == "GEN-CSV"


def _plan_seed(split: str) -> int:
    return cfg.seed_for(cfg.master_seed(split), "heldout" if split == "heldout" else "devtest", 0, "plan")


def _deck(n: int, shares: dict[Any, float], rng: random.Random) -> list[Any]:
    """Exact stratified deck: blocks of 20 hold the shares exactly (each block shuffled)."""
    out: list[Any] = []
    remaining = n
    while remaining > 0:
        m = min(20, remaining)
        counts = sc.allocate_counts(m, shares)
        block = [k for k, c in counts.items() for _ in range(c)]
        rng.shuffle(block)
        out.extend(block)
        remaining -= m
    return out


def _family_slots() -> dict[str, list[str]]:
    """Family of every report per split, before ordering: dev/test (320) and heldout (80)."""
    rng = random.Random(_plan_seed("dev"))
    fams = list(cfg.SHARED_FAMILIES)
    rng.shuffle(fams)
    base_q, extra = divmod(cfg.DEVTEST_TOTAL, len(fams))
    quota = {f: base_q + (1 if i < extra else 0) for i, f in enumerate(fams)}
    n_test = cfg.SPLIT_SIZES["test"]
    t_base, t_extra = divmod(n_test, len(fams))
    order18 = [f for f in fams if quota[f] == base_q + 1] + [f for f in fams if quota[f] == base_q]
    test_n = {f: t_base + (1 if i < t_extra else 0) for i, f in enumerate(order18)}
    dev: list[str] = []
    test: list[str] = []
    for f in fams:
        test += [f] * test_n[f]
        dev += [f] * (quota[f] - test_n[f])
    hrng = random.Random(_plan_seed("heldout"))
    shared = list(cfg.SHARED_FAMILIES)
    hrng.shuffle(shared)
    only = sorted(f for f, e in FAMILIES.items() if e.scope == "heldout_only")
    h_shared_n = cfg.SPLIT_SIZES["heldout"] - cfg.HELDOUT_REPORTS_PER_ONLY_FAMILY * len(only)
    hb, hx = divmod(h_shared_n, len(shared))
    held = [f for f in only for _ in range(cfg.HELDOUT_REPORTS_PER_ONLY_FAMILY)]
    for i, f in enumerate(shared):
        held += [f] * (hb + (1 if i < hx else 0))
    return {"dev": dev, "test": test, "heldout": held}


_PLAN_CACHE: dict[str, list[ReportPlan]] = {}


def plan_split(split: str) -> list[ReportPlan]:
    """The deterministic plan of one split. Scenario classes and page targets are dealt from exact stratified decks
    over the family-sorted slots (so every family gets a proportional mix), then slots are shuffled into ids."""
    if split in _PLAN_CACHE:
        return _PLAN_CACHE[split]
    if split not in cfg.SPLIT_SIZES:
        raise ValueError(f"unknown split {split!r}")
    master = cfg.master_seed(split)
    n = cfg.SPLIT_SIZES[split]
    fams = sorted(_family_slots()[split])
    assert len(fams) == n
    sseed = lambda purpose: cfg.seed_for(master, split, 0, purpose)  # noqa: E731
    classes = _deck(n, cfg.SCENARIO_SHARES, random.Random(sseed("scenario")))
    # page targets: GEN-CSV is one page; the rest of the deck is dealt to the other slots
    pcounts = sc.allocate_counts(n, {str(k): v for k, v in cfg.PAGE_SHARES.items()})
    n_csv = fams.count("GEN-CSV")
    pcounts["1"] = max(0, pcounts["1"] - n_csv)
    rest = n - n_csv
    short = sum(pcounts.values()) - rest
    for k in ("2", "3"):  # keep totals exact if the 1-page share was already exhausted
        while short > 0 and pcounts[k] > 0:
            pcounts[k] -= 1
            short -= 1
    prng = random.Random(sseed("plan") + 1)
    pages_deck = [int(k) for k, c in pcounts.items() for _ in range(c)]
    prng.shuffle(pages_deck)
    patients = sc.build_patients(n, random.Random(sseed("patient")))
    slots = []
    pd = iter(pages_deck)
    for fam, cls in zip(fams, classes, strict=True):
        slots.append((fam, cls, 1 if fam == "GEN-CSV" else next(pd)))
    random.Random(sseed("plan") + 2).shuffle(slots)
    ids = [f"{split[0].upper()}{i + 1:03d}" for i in range(n)]
    rot = render.choose_rot90([(rid, s[0]) for rid, s in zip(ids, slots, strict=True) if s[0] != "GEN-CSV"],
                              sseed("rot90"))
    out = [
        ReportPlan(rid, split, i, fam, cls, pg, patients[i], rot.get(rid, 0))
        for i, (rid, (fam, cls, pg)) in enumerate(zip(ids, slots, strict=True))
    ]
    _PLAN_CACHE[split] = out
    return out


# ---------------------------------------------------------------- building one report

def build_printed(p: ReportPlan) -> PrintedReport:
    """Build the printed report of a plan item; falls back to the nearest other page target if the family cannot
    produce the planned one (the actual page count is what GT and the manifest record)."""
    entry = FAMILIES[p.family]
    err: Exception | None = None
    for tgt in PAGE_ORDER[p.target_pages]:
        try:
            return entry.builder(p.family, split=p.split, index=p.index, patient=p.patient,
                                 scenario_class=p.scenario_class, target_pages=tgt, report_id=p.report_id)
        except RuntimeError as exc:  # pagination or class not reachable for this target
            err = exc
    raise RuntimeError("no page target feasible") from err


# ---------------------------------------------------------------- fingerprint and files

def _sha_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha_file(path: pathlib.Path | str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_json(obj: Any) -> bytes:
    return (json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")


def _lib_versions() -> dict[str, str]:
    return {name: md.version(name) for name in ("reportlab", "PyMuPDF", "Pillow", "numpy")}


def fingerprint(split: str) -> str:
    """SHA-256 over the sorted (module, source hash) pairs the split uses, seeds, N, library and schema versions."""
    mods = list(_COMMON_MODULES) + (list(_HELDOUT_MODULES) if split == "heldout" else [])
    parts = {
        "modules": sorted((m, sha_file(cfg.REPO_ROOT / m)) for m in mods),
        "seed": cfg.master_seed(split), "split": split, "n": cfg.SPLIT_SIZES[split],
        "versions": _lib_versions(),
        "schemas": [gt_schema.GT_SCHEMA, MANIFEST_SCHEMA, cfg.GENERATOR_VERSION],
    }
    return _sha_bytes(canonical_json(parts))


def _write_atomic(path: pathlib.Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _norm_renderings(renderings: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(renderings, str):
        renderings = [x for x in renderings.split(",") if x]
    out: list[str] = []
    for r in renderings:
        if r == "all":
            out += list(RENDERINGS)
        elif r in RENDERINGS:
            out.append(r)
        else:
            raise ValueError(f"unknown rendering {r!r}")
    return [r for r in RENDERINGS if r in out]


def load_manifest(split: str, root: pathlib.Path) -> dict[str, Any] | None:
    path = root / split / "manifest.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _entry_ok(root: pathlib.Path, split: str, f: dict[str, Any] | None) -> bool:
    """True when the manifest entry's file(s) exist and hash to the recorded SHA-256."""
    if not f:
        return False
    items = f["pages"] if "pages" in f else [f]
    try:
        return bool(items) and all(sha_file(root / split / it["path"]) == it["sha256"] for it in items)
    except OSError:
        return False


@dataclass
class Stats:
    built: int = 0
    skipped: int = 0
    files: int = 0


def materialize(split: str, renderings: str | list[str] = "native", *, root: pathlib.Path | str | None = None,
                ids: list[str] | None = None, stats: Stats | None = None) -> dict[str, Any]:
    """Write GT, native/csv and the requested renderings of ``split`` under ``<root>/<split>/``; return the manifest.

    GT and native (csv for GEN-CSV) are always produced; ``scan``, ``photo`` and ``rot90`` only when requested.
    Existing files with a matching SHA-256 in a manifest carrying the same fingerprint are skipped.
    """
    root = pathlib.Path(root) if root is not None else cfg.CORPUS_DIR
    want = _norm_renderings(renderings)
    stats = stats if stats is not None else Stats()
    fp = fingerprint(split)
    old = load_manifest(split, root)
    prev: dict[str, dict[str, Any]] = {}
    if old and old.get("fingerprint") == fp and old.get("schema") == MANIFEST_SCHEMA:
        prev = {r["id"]: r for r in old["reports"]}
    sdir = root / split
    plans = [p for p in plan_split(split) if ids is None or p.report_id in ids]
    master = cfg.master_seed(split)
    entries: dict[str, dict[str, Any]] = {rid: r for rid, r in prev.items() if ids is not None}
    for p in plans:
        ent = prev.get(p.report_id, {})
        files: dict[str, Any] = dict(ent.get("files", {}))
        main = "csv" if p.is_csv else "native"
        need = ["gt", main] + [r for r in want if r in ("scan", "photo") and not p.is_csv]
        if p.rot90:
            need += ["rot90"] if "rot90" in want else []
        have_all = ent and all(_entry_ok(root, split, files.get(k)) for k in need)
        if have_all:
            stats.skipped += 1
            entries[p.report_id] = ent
            continue
        rep = build_printed(p)
        gtd = base.build_gt(rep).to_dict()
        gtb = canonical_json(gtd)
        rel = f"gt/{p.report_id}.json"
        _write_atomic(sdir / rel, gtb)
        files = {"gt": {"path": rel, "sha256": _sha_bytes(gtb)}}
        if p.is_csv:
            rel = f"csv/{p.report_id}.csv"
            _write_atomic(sdir / rel, csv_export.csv_text(rep).encode("utf-8"))
            files["csv"] = {"path": rel, "sha256": sha_file(sdir / rel)}
        else:
            rel = f"native/{p.report_id}.pdf"
            (sdir / "native").mkdir(parents=True, exist_ok=True)
            render.native_pdf(rep, str(sdir / rel))
            files["native"] = {"path": rel, "sha256": sha_file(sdir / rel)}
            native = str(sdir / rel)
            if "scan" in want:
                rel = f"scan/{p.report_id}.pdf"
                (sdir / "scan").mkdir(parents=True, exist_ok=True)
                meta = render.render_scan(native, str(sdir / rel), cfg.seed_for(master, split, p.index, "scan"))
                files["scan"] = {"path": rel, "sha256": sha_file(sdir / rel)}
                _write_atomic(sdir / f"render_meta/{p.report_id}.scan.json", canonical_json(meta))
            if "photo" in want:
                (sdir / "photo").mkdir(parents=True, exist_ok=True)
                meta = render.render_photo(native, str(sdir / "photo" / p.report_id),
                                           cfg.seed_for(master, split, p.index, "photo"))
                pages = []
                for fpath in meta["files"]:
                    pages.append({"path": f"photo/{pathlib.Path(fpath).name}", "sha256": sha_file(fpath)})
                meta["files"] = [x["path"] for x in pages]
                files["photo"] = {"pages": pages}
                _write_atomic(sdir / f"render_meta/{p.report_id}.photo.json", canonical_json(meta))
            if p.rot90 and "rot90" in want:
                rel = f"rot90/{p.report_id}.pdf"
                (sdir / "rot90").mkdir(parents=True, exist_ok=True)
                meta = render.render_scan(native, str(sdir / rel), cfg.seed_for(master, split, p.index, "rot90"),
                                          rot90=p.rot90)
                files["rot90"] = {"path": rel, "sha256": sha_file(sdir / rel)}
                _write_atomic(sdir / f"render_meta/{p.report_id}.rot90.json", canonical_json(meta))
        stats.built += 1
        stats.files += len(files)
        entries[p.report_id] = {
            "id": p.report_id, "family": p.family, "template_id": rep.template_id, "scenario_class": p.scenario_class,
            "n_pages": rep.n_pages, "rot90": p.rot90, "is_csv": p.is_csv, "files": files,
        }
    manifest = {
        "schema": MANIFEST_SCHEMA, "split": split, "fingerprint": fp, "seed": cfg.master_seed(split),
        "n": cfg.SPLIT_SIZES[split], "generator_version": cfg.GENERATOR_VERSION,
        "reports": [entries[k] for k in sorted(entries)],
    }
    _write_atomic(sdir / "manifest.json", canonical_json(manifest))
    return manifest


# ---------------------------------------------------------------- verify

PLAN_MIN_TEST_PER_FAMILY = 4


def verify_plan(splits: list[str]) -> list[str]:
    """Plan-level invariants (GN-01/02/03 style). Returns error strings that never name a heldout report."""
    errs: list[str] = []
    plans = {s: plan_split(s) for s in splits}
    for s, ps in plans.items():
        if len(ps) != cfg.SPLIT_SIZES[s]:
            errs.append(f"{s}: {len(ps)} reports, expected {cfg.SPLIT_SIZES[s]}")
        if len({p.report_id for p in ps}) != len(ps):
            errs.append(f"{s}: duplicate ids")
        counts = sc.allocate_counts(len(ps), cfg.SCENARIO_SHARES)
        for cls, c in counts.items():
            got = sum(1 for p in ps if p.scenario_class == cls)
            if abs(got - c) > 1:
                errs.append(f"{s}: scenario class {cls} has {got}, expected {c} +-1")
        n_rot = sum(1 for p in ps if p.rot90)
        n_non_csv = sum(1 for p in ps if not p.is_csv)
        if n_rot != int(n_non_csv * cfg.ROT90_FRACTION + 0.5):
            errs.append(f"{s}: rot90 count {n_rot} != round(5% of {n_non_csv})")
        if any(p.rot90 and p.is_csv for p in ps):
            errs.append(f"{s}: a csv report is rot90")
        fams = {p.family for p in ps}
        only = {f for f, e in FAMILIES.items() if e.scope == "heldout_only"}
        if s == "heldout":
            if not only <= fams:
                errs.append("heldout: heldout-only families missing")
            for f in only:
                if sum(1 for p in ps if p.family == f) != cfg.HELDOUT_REPORTS_PER_ONLY_FAMILY:
                    errs.append("heldout: a heldout-only family does not have 8 reports")
        else:
            if fams & only:
                errs.append(f"{s}: contains heldout-only families")
    if {"dev", "test"} <= set(plans):
        if cfg.master_seed("heldout") == cfg.master_seed("dev"):
            errs.append("heldout seed equals the dev/test seed")
        tf = [p.family for p in plans["test"]]
        for f in cfg.SHARED_FAMILIES:
            if tf.count(f) < PLAN_MIN_TEST_PER_FAMILY:
                errs.append(f"test: family {f} has fewer than {PLAN_MIN_TEST_PER_FAMILY} reports")
        dt = {p.family for p in plans["dev"]} | {p.family for p in plans["test"]}
        if not set(cfg.TEMPLATE_FAMILIES) <= dt or len(dt & set(cfg.GENERIC_FAMILIES)) < 10:
            errs.append("dev+test: family coverage below 8 templates + 10 generic")
    return errs


_WORD_TOL = 2.0


def _words_in_bbox(page: Any, bbox: list[float]) -> str:
    import fitz

    r = fitz.Rect(bbox[0] - _WORD_TOL, bbox[1] - _WORD_TOL, bbox[2] + _WORD_TOL, bbox[3] + _WORD_TOL)
    return " ".join(w[4] for w in page.get_text("words") if fitz.Rect(w[:4]).intersects(r))


def verify_report(split: str, root: pathlib.Path, ent: dict[str, Any]) -> list[str]:
    """Per-report checks. Messages carry no report id (the caller decides what may be shown)."""
    errs: list[str] = []
    sdir = root / split
    for k, f in ent["files"].items():
        if not _entry_ok(root, split, f):
            errs.append(f"manifest sha mismatch or missing file ({k})")
    gt = json.loads((sdir / ent["files"]["gt"]["path"]).read_text(encoding="utf-8"))
    errs += [f"gt: {e}" for e in gt_schema.validate_gt(gt)]
    for r in gt["rows"]:
        if r["flag"] not in gt_schema.FLAGS or r["flag_true"] in ("UNKNOWN", None):
            errs.append("flag/flag_true out of range")
        if r["is_critical"] and r["specimen"] != "serum":
            errs.append("is_critical on a non-serum row")
        if r["value_text"] not in r["line_text"]:
            errs.append("value_text not in line_text")
    text_blobs = [json.dumps(gt, ensure_ascii=False)]
    if ent["is_csv"]:
        text_blobs.append((sdir / ent["files"]["csv"]["path"]).read_text(encoding="utf-8"))
    else:
        import fitz

        with fitz.open(sdir / ent["files"]["native"]["path"]) as doc:
            if not 1 <= len(doc) <= 3 or len(doc) != len(gt["pages"]) or len(doc) != ent["n_pages"]:
                errs.append("native page count differs from GT")
            else:
                for r in gt["rows"]:
                    txt = _words_in_bbox(doc[r["page"] - 1], r["bbox"])
                    if not all(t in txt for t in r["value_text"].split()):
                        errs.append("row value_text outside its bbox")
                        break
                for d in gt["decoys"]:
                    if d["page"] > len(doc):
                        errs.append("decoy page out of range")
                        break
            text_blobs.append("\n".join(p.get_text() for p in doc))
    if base.deny_hits("\n".join(text_blobs)):
        errs.append("deny-list token present")
    return errs


def verify(splits: list[str], *, root: pathlib.Path | str | None = None, renderings: str = "native",
           out: Any = None) -> int:
    """Run the plan and corpus invariants over ``splits``; materialise what is missing. Returns an exit code."""
    out = out or sys.stdout
    root = pathlib.Path(root) if root is not None else cfg.CORPUS_DIR
    failures = 0
    errs = verify_plan(splits)
    for e in errs:
        print(f"FAIL plan: {e}", file=out)
    failures += len(errs)
    amb_rows = total_rows = crit_rows = 0
    for s in splits:
        t0 = time.time()
        held = s == "heldout"
        stats = Stats()
        try:
            man = materialize(s, renderings, root=root, stats=stats)
        except Exception as exc:  # noqa: BLE001 - heldout must not leak ids or traces
            print(f"FAIL {s}: materialise failed ({'details withheld' if held else repr(exc)})", file=out)
            failures += 1
            continue
        bad = 0
        n_amb = n_rows = n_crit = 0
        for ent in man["reports"]:
            try:
                r_errs = verify_report(s, root, ent)
                gt = json.loads((root / s / ent["files"]["gt"]["path"]).read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                r_errs, gt = [f"verify crashed: {exc!r}"], {"rows": []}
            n_rows += len(gt["rows"])
            n_amb += sum(1 for r in gt["rows"] if r["expected_needs_review"])
            n_crit += sum(1 for r in gt["rows"] if r["is_critical"])
            if r_errs:
                bad += 1
                if not held:
                    for e in r_errs:
                        print(f"FAIL {s} {ent['id']}: {e}", file=out)
        if len(man["reports"]) != cfg.SPLIT_SIZES[s]:
            print(f"FAIL {s}: manifest has {len(man['reports'])} reports", file=out)
            failures += 1
        failures += bad
        amb_rows += n_amb
        total_rows += n_rows
        crit_rows += n_crit
        share = n_amb / n_rows if n_rows else 0.0
        print(f"{s}: {len(man['reports'])} reports, {n_rows} rows, AMB {share:.2%}, critical rows {n_crit}, "
              f"{bad} report(s) failing, built {stats.built} skipped {stats.skipped}, {time.time() - t0:.1f}s",
              file=out)
    if total_rows:
        share = amb_rows / total_rows
        print(f"corpus: {total_rows} rows, AMB share {share:.2%}, critical rows {crit_rows}", file=out)
        if len(splits) == len(cfg.SPLIT_SIZES):
            if not 0.02 <= share <= 0.05:
                print("FAIL corpus: AMB share outside 2-5%", file=out)
                failures += 1
            if crit_rows < 100:
                print("FAIL corpus: fewer than 100 critical rows", file=out)
                failures += 1
    print("verify: OK" if not failures else f"verify: {failures} failure(s)", file=out)
    return 0 if not failures else 1


# ---------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.eval.generate", description=__doc__.split("\n")[0])
    ap.add_argument("--splits", default="dev,test,heldout")
    ap.add_argument("--renderings", default="native", help="comma list of native,csv,scan,photo,rot90 or all")
    ap.add_argument("--verify", action="store_true", help="check plan and corpus invariants (materialises natives)")
    ap.add_argument("--out", default=None, help="corpus root (default eval/corpus)")
    args = ap.parse_args(argv)
    splits = [s for s in args.splits.split(",") if s]
    bad = [s for s in splits if s not in cfg.SPLIT_SIZES]
    if bad:
        print(f"unknown split(s): {','.join(bad)}", file=sys.stderr)
        return 2
    root = pathlib.Path(args.out) if args.out else None
    if args.verify:
        return verify(splits, root=root, renderings=args.renderings)
    for s in splits:
        t0 = time.time()
        stats = Stats()
        try:
            man = materialize(s, args.renderings, root=root, stats=stats)
        except Exception as exc:  # noqa: BLE001
            print(f"{s}: FAILED ({'details withheld' if s == 'heldout' else repr(exc)})", file=sys.stderr)
            return 1
        print(f"{s}: {len(man['reports'])} reports (built {stats.built}, skipped {stats.skipped}, "
              f"{time.time() - t0:.1f}s) fingerprint {man['fingerprint'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
