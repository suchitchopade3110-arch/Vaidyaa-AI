"""T10 corpus-level checks (P1 plan section 11: GN-01, GN-05, GN-06, GN-11, GN-14) and the T05 decoy regression."""

from __future__ import annotations

import ast
import hashlib
import pathlib
import random
from unittest import mock

import fitz
import pytest

from tools.eval import config as cfg
from tools.eval import generate as gen
from tools.eval import render
from tools.eval import scenarios as sc
from tools.eval.gt_schema import FLAGS, validate_gt
from tools.eval.layouts import FAMILIES, base

ROOT = pathlib.Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "fixtures" / "eval_golden"

# ---------------------------------------------------------------- T05 regression: LDL "Not calculated" decoy

LDL_FAMILIES = ["IN-A", "IN-B", "IN-C", "IN-D", "IN-E", "US-A", "UK-A"]


@pytest.mark.parametrize("fam", LDL_FAMILIES)
def test_ldl_not_calculated_decoy_is_printed_and_registered(fam, tmp_path):
    """The note was keyed "LIPID" but looked up by the printed title ("LIPID PROFILE"), so it never printed."""
    pts = [p for p in sc.build_patients(40, random.Random(1)) if p.age_true >= 18 and p.input_sex]
    with mock.patch.object(sc, "ldl_friedewald", lambda *a, **k: None):  # force the TG >= 400 branch
        for i, pt in enumerate(pts[:12]):
            rep = FAMILIES[fam].builder(fam, split="dev", index=i, patient=pt, scenario_class="mild", target_pages=3)
            if any(r.key == "triglycerides" for r in rep.rows):
                break
        else:
            pytest.fail("no lipid-bearing report found")
    assert not any(r.key == "ldl" for r in rep.rows)
    pdf = tmp_path / "r.pdf"
    render.native_pdf(rep, str(pdf))
    with fitz.open(pdf) as doc:
        text = "\n".join(p.get_text() for p in doc)
    assert "LDL: Not calculated (TG >" in text
    decoys = [d for d in rep.decoys if d.kind == "not_calculated"]
    assert len(decoys) == 1 and decoys[0].text.startswith("LDL: Not calculated")
    gt = base.build_gt(rep).to_dict()
    assert not any("Not calculated" in r["line_text"] for r in gt["rows"])
    assert any(d["kind"] == "not_calculated" for d in gt["decoys"])


# ---------------------------------------------------------------- GN-01 corpus plan

ONLY = sorted(f for f, e in FAMILIES.items() if e.scope == "heldout_only")


def test_gn01_corpus_plan():
    plans = {s: gen.plan_split(s) for s in ("dev", "test", "heldout")}
    assert [len(plans[s]) for s in ("dev", "test", "heldout")] == [240, 80, 80]
    assert cfg.HELDOUT_SEED != cfg.DEVTEST_SEED and cfg.DEVTEST_SEED == 20261119 and cfg.HELDOUT_SEED == 61700529
    assert plans["dev"][0].report_id == "D001" and plans["heldout"][-1].report_id == "H080"
    dev_test = {p.family for s in ("dev", "test") for p in plans[s]}
    assert set(cfg.TEMPLATE_FAMILIES) <= dev_test
    assert len(dev_test & set(cfg.GENERIC_FAMILIES)) >= 10
    assert len(ONLY) == 3 and not dev_test & set(ONLY)
    for f in ONLY:
        assert sum(1 for p in plans["heldout"] if p.family == f) == 8
    for f in cfg.SHARED_FAMILIES:  # every shared family is in test and in heldout
        assert sum(1 for p in plans["test"] if p.family == f) >= 4, f
        assert sum(1 for p in plans["heldout"] if p.family == f) in (3, 4), f
    assert gen.verify_plan(["dev", "test", "heldout"]) == []


def test_gn01_plan_is_deterministic_and_stratified():
    again = {}
    for s in ("dev", "test", "heldout"):
        gen._PLAN_CACHE.pop(s, None)
        again[s] = gen.plan_split(s)
    assert again["dev"] == gen.plan_split("dev")
    for s, n in (("dev", 240), ("test", 80), ("heldout", 80)):
        ps = gen.plan_split(s)
        for cls, share in cfg.SCENARIO_SHARES.items():
            assert abs(sum(1 for p in ps if p.scenario_class == cls) - share * n) <= 1
        pages = [p.target_pages for p in ps]
        assert abs(pages.count(1) - 0.45 * n) <= 1.5 and abs(pages.count(3) - 0.15 * n) <= 1.5
        assert all(p.target_pages == 1 for p in ps if p.is_csv)
        assert sum(1 for p in ps if p.rot90) == int(0.05 * sum(1 for q in ps if not q.is_csv) + 0.5)
    assert {s: len({p.report_id for p in gen.plan_split(s)}) for s in ("dev", "test", "heldout")} == \
        {"dev": 240, "test": 80, "heldout": 80}


def test_fingerprints_differ_per_split_and_heldout_modules_only_in_heldout(monkeypatch):
    fps = {s: gen.fingerprint(s) for s in ("dev", "test", "heldout")}
    assert len(set(fps.values())) == 3
    assert not any("heldout" in m for m in gen._COMMON_MODULES)
    assert gen.fingerprint("dev") == fps["dev"]  # stable


# ---------------------------------------------------------------- GN-05 seed sensitivity

def test_gn05_different_seed_changes_gt(monkeypatch):
    plans = gen.plan_split("dev")[:10]
    first = [gen.canonical_json(base.build_gt(gen.build_printed(p)).to_dict()) for p in plans]
    monkeypatch.setattr(cfg, "DEVTEST_SEED", cfg.DEVTEST_SEED + 1)
    second = [gen.canonical_json(base.build_gt(gen.build_printed(p)).to_dict()) for p in plans]
    assert sum(a != b for a, b in zip(first, second, strict=True)) >= 9


# ---------------------------------------------------------------- GN-06 golden

GOLDEN_IDS = ("D043", "D071", "D203")  # IN-A 2 pages + critical; G08 blood+urine+spot electrolytes; US-A flags+qualifiers (+ mEq/L)


def _golden_manifest() -> dict[str, str]:
    out = {}
    for line in (GOLDEN / "manifest.sha256").read_text().splitlines():
        sha, name = line.split("  ", 1)
        out[name] = sha
    return out


def test_gn06_golden_files_match_manifest_sha256():
    man = _golden_manifest()
    assert set(man) == {f"{i}.{k}" for i in GOLDEN_IDS for k in ("gt.json", "native.pdf")}
    for name, sha in man.items():
        assert hashlib.sha256((GOLDEN / name).read_bytes()).hexdigest() == sha, name
    assert sum(p.stat().st_size for p in GOLDEN.iterdir()) < 200_000


def test_gn06_golden_samples_have_the_planned_properties():
    plans = {p.report_id: p for p in gen.plan_split("dev")}
    assert [plans[i].family for i in GOLDEN_IDS] == ["IN-A", "G08", "US-A"]
    for fam, rid in zip(("IN-A", "G08", "US-A"), GOLDEN_IDS, strict=True):  # the first dev report with the property
        first = [p for p in gen.plan_split("dev") if p.family == fam]
        assert rid in {p.report_id for p in first}
    gt = {i: __import__("json").loads((GOLDEN / f"{i}.gt.json").read_text()) for i in GOLDEN_IDS}
    assert len(gt["D043"]["pages"]) == 2 and gt["D043"]["expected"]["critical_rows"]
    assert any("method_column" in r["features"] for r in gt["D043"]["rows"])
    titles = [s["title"] for s in gt["D071"]["sections"]]
    assert "URINE ROUTINE EXAMINATION" in titles and "URINE SPOT ELECTROLYTES" in titles
    assert any(r["features"] and "flag_column" in r["features"] for r in gt["D203"]["rows"])
    assert any(r["qualifier"] for r in gt["D203"]["rows"])


def test_gn06_regenerated_bytes_match_golden(tmp_path):
    gen.materialize("dev", "native", root=tmp_path, ids=list(GOLDEN_IDS))
    man = _golden_manifest()
    for i in GOLDEN_IDS:
        assert hashlib.sha256((tmp_path / "dev/gt" / f"{i}.json").read_bytes()).hexdigest() == man[f"{i}.gt.json"]
        assert hashlib.sha256((tmp_path / "dev/native" / f"{i}.pdf").read_bytes()).hexdigest() == man[f"{i}.native.pdf"]


# ---------------------------------------------------------------- GN-11 independence

GT_SIDE = ["config", "catalogue", "critical_table", "units", "gt_schema", "clinical", "scenarios", "render",
           "generate"]
JUDGE_SIDE = ["match", "metrics", "review_sim", "targets", "gate", "hygiene", "report", "units", "clinical"]
FORBIDDEN = ("app", "clinical_reference", "tools.eval.adapters")


def _imports(path: pathlib.Path) -> list[str]:
    mods: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            mods += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            mods.append(node.module or "")
            mods += [f"{node.module}.{a.name}" for a in node.names]
    return mods


def test_gn11_independence_ast_scan():
    files = [ROOT / "tools/eval" / f"{m}.py" for m in GT_SIDE + JUDGE_SIDE]
    files += sorted((ROOT / "tools/eval/layouts").rglob("*.py"))
    scanned = 0
    for f in files:
        if not f.exists():
            continue
        scanned += 1
        for m in _imports(f):
            assert not any(m == bad or m.startswith(bad + ".") for bad in FORBIDDEN), f"{f.name} imports {m}"
    assert scanned >= 15


def test_gn11_scanner_catches_a_violation(tmp_path):
    bad = tmp_path / "x.py"
    bad.write_text("from app.services import ocr_service\nimport clinical_reference\nfrom tools.eval import adapters\n")
    assert len([m for m in _imports(bad) if any(m == b or m.startswith(b + ".") for b in FORBIDDEN)]) >= 3


# ---------------------------------------------------------------- GN-14 schema (whole test split)

@pytest.fixture(scope="module")
def test_split(tmp_path_factory):
    root = tmp_path_factory.mktemp("corpus")
    stats = gen.Stats()
    man = gen.materialize("test", "native", root=root, stats=stats)
    return root, man, stats


def test_gn14_every_gt_validates(test_split):
    import json

    root, man, _ = test_split
    assert len(man["reports"]) == 80
    n_rows = 0
    for ent in man["reports"]:
        gt = json.loads((root / "test" / ent["files"]["gt"]["path"]).read_text(encoding="utf-8"))
        assert validate_gt(gt) == [], ent["id"]
        for r in gt["rows"]:
            n_rows += 1
            assert r["flag"] in FLAGS
            assert r["flag_true"] in FLAGS and r["flag_true"] != "UNKNOWN"
            if r["is_critical"]:
                assert r["specimen"] == "serum"
    assert n_rows > 1500


def test_oracle_reproduces_every_gt_headline(test_split):
    """Plan 6.8 self-consistency check (T16, zoro): ``ClinicalRulesScorer`` on the GT true rows of every job scope
    (whole report, and each page for photo jobs) must reproduce the GT headline. ``generate --verify`` does not run
    it yet (adding it there moves every split fingerprint), so it is enforced here on the whole test split."""
    import json

    from tools.eval import clinical
    from tools.eval import metrics as me
    from tools.eval import review_sim as rs

    root, man, _ = test_split
    oracle = clinical.ClinicalRulesScorer()
    bad, scopes = [], 0
    for ent in man["reports"]:
        gt = json.loads((root / "test" / ent["files"]["gt"]["path"]).read_text(encoding="utf-8"))
        sex, age = gt["patient"]["input_sex"], gt["patient"]["input_age"]
        for pages in [()] + [(n,) for n in sorted({r["page"] for r in gt["rows"]})]:
            rows = me.scope_rows(gt, pages)
            want = me.gt_headline(gt, rows, pages)
            got = oracle.score(rs.corrected_rows(rows), sex, age).headline
            scopes += 1
            if got != want:
                bad.append((ent["id"], pages, want, got))
    assert scopes > 80 and bad == []


def test_manifest_shape_and_lazy_skip(test_split):
    root, man, stats = test_split
    assert stats.built == 80 and stats.skipped == 0
    assert man["schema"] == gen.MANIFEST_SCHEMA and man["fingerprint"] == gen.fingerprint("test")
    assert man["seed"] == cfg.DEVTEST_SEED and man["n"] == 80
    ent = man["reports"][0]
    assert {"id", "family", "template_id", "scenario_class", "n_pages", "rot90", "is_csv", "files"} <= set(ent)
    assert all(len(f["sha256"]) == 64 for f in ent["files"].values() if "sha256" in f)
    s2 = gen.Stats()
    man2 = gen.materialize("test", "native", root=root, stats=s2)
    assert s2.built == 0 and s2.skipped == 80 and man2 == man


def test_changed_fingerprint_or_file_triggers_rebuild(tmp_path, monkeypatch):
    rid = gen.plan_split("test")[0].report_id
    gen.materialize("test", "native", root=tmp_path, ids=[rid])
    gt = tmp_path / "test/gt" / f"{rid}.json"
    gt.write_bytes(b"{}\n")  # corrupt: the recorded SHA-256 no longer matches
    st = gen.Stats()
    gen.materialize("test", "native", root=tmp_path, ids=[rid], stats=st)
    assert st.built == 1 and validate_gt(__import__("json").loads(gt.read_text())) == []
    monkeypatch.setattr(gen, "fingerprint", lambda split: "0" * 64)
    st = gen.Stats()
    gen.materialize("test", "native", root=tmp_path, ids=[rid], stats=st)
    assert st.built == 1


def test_all_renderings_are_deterministic_and_rot90_only_where_planned(tmp_path):
    ps = gen.plan_split("test")
    csv = next(p for p in ps if p.is_csv)
    rot = next(p for p in ps if p.rot90)
    plain = next(p for p in ps if not p.is_csv and not p.rot90)
    ids = [csv.report_id, rot.report_id, plain.report_id]
    m1 = gen.materialize("test", "all", root=tmp_path / "a", ids=ids)
    m2 = gen.materialize("test", "all", root=tmp_path / "b", ids=ids)
    assert m1 == m2
    by = {e["id"]: e for e in m1["reports"]}
    assert set(by[csv.report_id]["files"]) == {"gt", "csv"}
    assert set(by[rot.report_id]["files"]) == {"gt", "native", "scan", "photo", "rot90"}
    assert set(by[plain.report_id]["files"]) == {"gt", "native", "scan", "photo"}
    for rid in ids:
        for ent in by[rid]["files"].values():
            for it in ent.get("pages", [ent]):
                assert (tmp_path / "a/test" / it["path"]).read_bytes() == (tmp_path / "b/test" / it["path"]).read_bytes()


# ---------------------------------------------------------------- heldout hygiene

def test_heldout_verify_prints_aggregates_only_and_writes_only_under_heldout(tmp_path):
    import io
    import re

    from tools.eval.layouts.heldout import families as hx

    buf = io.StringIO()
    rc = gen.verify(["heldout"], root=tmp_path, out=buf)
    text = buf.getvalue()
    assert rc == 0, text
    assert "heldout: 80 reports" in text
    assert not re.search(r"\bH\d{3}\b", text) and not re.search(r"\bHX-\d\b", text)
    assert not any(tok.lower() in text.lower() for tok in hx.CANARY_TOKENS)
    assert [p.name for p in tmp_path.iterdir()] == ["heldout"]


# ---------------------------------------------------------------- P1 review MED-4: Table C coverage of the corpus

# Table C by hand (plan 3.5, Kost 1990 means with guard bands), blood only: critical below LOW or above HIGH; a value
# inside a guard is never emitted. hs-TnT guards from its printed URL (14 ng/L) to 100.
HAND_TABLE_C = {  # key: (critical below, critical above, guards)
    "blood_ph": (7.15, 7.65, ((7.15, 7.25), (7.55, 7.65))),
    "pco2": (15, 70, ((15, 25), (60, 70))),
    "po2": (40, None, ((40, 50),)),
    "ionized_calcium": (0.75, 1.65, ((0.75, 0.90), (1.50, 1.65))),
    "hs_troponin_t": (None, 100, ((14, 100),)),
}
MED4 = tuple(HAND_TABLE_C)


@pytest.fixture(scope="module")
def devtest_rows():
    """Every dev and test GT row, built in memory (no files)."""
    from tools.eval.layouts import base as lbase

    out = {"dev": [], "test": []}
    for split in out:
        for p in gen.plan_split(split):
            gt = lbase.build_gt(gen.build_printed(p)).to_dict()
            out[split] += [dict(r, family=p.family) for r in gt["rows"]]
    return out


def _crit(rows, key):
    return [r for r in rows if r["is_critical"] and r["analyte_key"] == key]


def test_med4_every_table_c_analyte_is_critical_in_dev_and_test(devtest_rows):
    """Floor: every Table C analyte the catalogue prints is critical at least twice in dev + test (blood glucose as one
    group); glucose_capillary is a Table C alias with no catalogue analyte."""
    from tools.eval import critical_table as ct
    from tools.eval.catalogue import CATALOGUE

    assert "glucose_capillary" not in CATALOGUE
    rows = devtest_rows["dev"] + devtest_rows["test"]
    groups: dict[str, int] = {}
    for key in ct.CRITICAL:
        if key in CATALOGUE:
            g = "any blood glucose" if key.startswith("glucose_") else key
            groups[g] = groups.get(g, 0) + len(_crit(rows, key))
    low = {g: n for g, n in groups.items() if n < 2}
    assert low == {}, low
    assert len(groups) == 19  # 23 Table C keys - glucose_capillary, with the 4 printed glucose keys as one group


@pytest.mark.parametrize("split", ["dev", "test"])
def test_med4_blood_gas_ica_hs_tnt_and_unitless_count_criticals_in_each_split(devtest_rows, split):
    rows = devtest_rows[split]
    missing = [k for k in MED4 if not _crit(rows, k)]
    assert missing == [], missing
    unitless = [r for r in rows if r["is_critical"] and "AMB_UNITLESS_COUNT" in r["ambiguity"]]
    assert unitless, "no critical count printed without a unit (S6, CL-27)"
    for r in unitless:  # the page cannot tell 1,303 x10^3/uL from 1,303 /uL: UNKNOWN, review required
        assert r["unit"] == "" and r["flag"] == "UNKNOWN" and r["flag_true"].startswith("CRITICAL_")
        assert r["analyte_key"] in ("wbc", "platelets") and r["expected_needs_review"]


def test_med4_blood_gas_ica_and_hs_tnt_values_follow_table_c_by_hand(devtest_rows):
    """Every emitted value of the five analytes sits outside the guards and is critical exactly when Table C says so;
    non-critical abnormal blood gases exist too (pH 7.30 acidaemia, pCO2 52 is HIGH), not only criticals."""
    rows = devtest_rows["dev"] + devtest_rows["test"]
    seen = 0
    for r in rows:
        if r["analyte_key"] not in HAND_TABLE_C:
            continue
        lo, hi, guards = HAND_TABLE_C[r["analyte_key"]]
        v = r["canonical"]["value"]
        assert not any(a <= v <= b for a, b in guards), (r["analyte_key"], v)
        assert r["is_critical"] == ((lo is not None and v < lo) or (hi is not None and v > hi)), (r["analyte_key"], v)
        seen += 1
    assert seen >= 150
    noncrit_abnormal = [r for r in rows if r["analyte_key"] in ("blood_ph", "pco2", "po2") and not r["is_critical"]
                        and r["flag_true"] in ("HIGH", "LOW")]
    assert noncrit_abnormal
    ph = [r["value"] for r in rows if r["analyte_key"] == "blood_ph" and r["flag_true"] == "LOW"]
    assert all(7.25 < v < 7.35 for v in ph)  # non-critical acidaemia: above the 7.25 guard edge, below 7.35


def test_med4_heldout_carries_the_strata_aggregate_only():
    """Heldout, aggregate only: the five analytes are each critical somewhere and a critical count is printed
    unit-less. Only counts are compared; nothing per report is printed or kept."""
    from tools.eval.layouts import base as lbase

    present: set[str] = set()
    unitless = 0
    for p in gen.plan_split("heldout"):
        for r in lbase.build_gt(gen.build_printed(p)).to_dict()["rows"]:
            if r["is_critical"] and r["analyte_key"] in HAND_TABLE_C:
                present.add(r["analyte_key"])
            unitless += int(r["is_critical"] and "AMB_UNITLESS_COUNT" in r["ambiguity"])
    n_present = len(present)
    assert n_present == len(HAND_TABLE_C) and unitless >= 1, f"{n_present}/5 analytes, {unitless} unit-less"


def test_med4_coverage_deal_is_balanced_and_never_touches_heldout_only_or_children():
    from tools.eval import scenarios as sc
    from tools.eval.layouts import FAMILIES

    for split in ("dev", "test", "heldout"):
        plans = gen.plan_split(split)
        dealt = [p for p in plans if p.coverage]
        assert all(p.scenario_class == "critical" and FAMILIES[p.family].scope == "shared" for p in dealt)
        for p in dealt:
            if p.patient.is_paediatric:  # blood gas and cardiac panels are adult-only
                assert p.coverage in ("neutrophils_abs", "count_unitless")
        per = {k: sum(p.coverage == k for p in dealt) for k in sc.COVERAGE_KEYS}
        assert max(per.values()) <= sc.COVERAGE_ROUNDS
        if split != "heldout":  # dev and test: every stratum is dealt at least once (supply allowing, plan 3.1)
            assert min(per.values()) >= 1, per


# ---------------------------------------------------------------- P1 review LOW-5: mEq/L is generated, not dropped

def test_low5_us_a_prints_meq_per_litre_for_the_monovalent_ions_only(devtest_rows):
    rows = devtest_rows["dev"] + devtest_rows["test"]
    meq = [r for r in rows if r["unit"] == "mEq/L"]
    assert meq and {r["family"] for r in meq} == {"US-A"}
    assert {r["analyte_key"] for r in meq} <= {"sodium", "potassium", "chloride", "bicarbonate"}
    for r in meq:  # 1 mEq = 1 mmol for a monovalent ion: the canonical value is the printed one
        assert r["canonical"]["value"] == pytest.approx(r["value"]) and r["canonical"]["unit"] == "mmol/L"
