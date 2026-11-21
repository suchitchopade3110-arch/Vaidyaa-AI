"""T08 checks for the heldout-only families (P1 plan 3.2.2, GN-01 family checks, GN-12 canary).

Only synthetic scratch reports are built here (never the heldout corpus), and no per-report output is printed. This
file holds only aggregate-safe assertions (canaries, registration, determinism, generic invariants); anything that
describes how a heldout family is laid out lives in ``tests/heldout/`` behind the DO-NOT-READ notice (P1 review
LOW-6, plan 3.2.2).
"""

from __future__ import annotations

import pathlib
import random
import re

import pytest

from tools.eval import config as cfg
from tools.eval import scenarios as sc
from tools.eval.layouts import FAMILIES, base, templates
from tools.eval.layouts import base as lbase
from tools.eval.layouts.heldout import families as hx
from tools.eval.gt_schema import validate_gt

REPO = pathlib.Path(__file__).resolve().parents[1]
HX_IDS = tuple(hx.SPECS)

CASES = [(0, "normal", 1), (1, "severe", 2), (2, "critical", 3), (3, "mild", 2)]


# Engine-side text that P3 may write rules into: code, template registries (PRD FR-3.1 allows data files) and the
# clinical reference module at the repo root. Binary files (model weights, images, databases) are skipped.
TEXT_SUFFIXES = frozenset({".py", ".json", ".yaml", ".yml", ".toml", ".txt", ".md", ".csv", ".tsv", ".cfg", ".ini",
                           ".html", ".htm", ".js", ".jsx", ".ts", ".tsx", ".xml", ".j2", ".jinja", ".tmpl", ".sql"})
ENGINE_ROOTS = (REPO / "app", REPO / "clinical_reference.py", REPO / "data")
MAX_SCAN_BYTES = 5 << 20


def _text_files(root: pathlib.Path) -> list[pathlib.Path]:
    files = [root] if root.is_file() else sorted(root.rglob("*")) if root.exists() else []
    return [p for p in files if p.is_file() and p.suffix.lower() in TEXT_SUFFIXES and "__pycache__" not in p.parts
            and p.stat().st_size <= MAX_SCAN_BYTES]


def canary_hits(*roots: pathlib.Path) -> list[int]:
    """Indices of canary tokens found (case-insensitive) in any text file under ``roots``; never the text itself."""
    texts = [p.read_text(encoding="utf-8", errors="ignore").lower() for r in roots for p in _text_files(r)]
    return [i for i, tok in enumerate(hx.CANARY_TOKENS) if any(tok.lower() in t for t in texts)]


@pytest.fixture(scope="module")
def reports():
    pts = sc.build_patients(12, random.Random(11))
    out = {}
    for fam in HX_IDS:
        for i, cls, pages in CASES:
            out[(fam, i)] = FAMILIES[fam].builder(fam, split="heldout", index=i, patient=pts[i], scenario_class=cls,
                                                  target_pages=pages)
    return out


# ---------------------------------------------------------------- GN-12 canary

def test_gn12_no_canary_token_in_app():
    hits = canary_hits(*ENGINE_ROOTS)
    assert not hits, f"canary token index(es) found in app/, clinical_reference.py or data/: {hits}"  # indices only


def test_gn12_scan_reaches_the_engine_roots():
    """The scan must actually read the engine code (a vacuous scan would always pass)."""
    scanned = [p for r in ENGINE_ROOTS for p in _text_files(r)]
    assert REPO / "clinical_reference.py" in scanned
    assert any(p.suffix == ".py" and "app" in p.relative_to(REPO).parts[:1] for p in scanned)


def test_gn12_leak_detection_works_and_prints_only_the_index(tmp_path):
    (tmp_path / "engine.py").write_text("# ok\nx = 1\n")
    assert canary_hits(tmp_path) == []
    leaked = hx.CANARY_TOKENS[7]
    (tmp_path / "rules.py").write_text(f'LAB = "{leaked.upper()}"\n')  # case change must not hide a leak
    assert canary_hits(tmp_path) == [7]
    with pytest.raises(AssertionError) as exc:
        assert not canary_hits(tmp_path), f"canary token index(es) found in app/: {canary_hits(tmp_path)}"
    assert leaked.lower() not in str(exc.value).lower()


def test_gn12_leak_detection_covers_template_data_files(tmp_path):
    """T16 (zoro, MED): the P3 template registry (PRD FR-3.1, app/services/lab_engine/templates/) may hold JSON or
    YAML detection signals; a heldout header word copied there must be caught like one in a .py file."""
    tdir = tmp_path / "services" / "lab_engine" / "templates"
    tdir.mkdir(parents=True)
    (tdir / "in_a.json").write_text('{"header_tokens": ["Bio. Ref. Interval"]}\n')
    assert canary_hits(tmp_path) == []
    (tdir / "lab_x.yaml").write_text(f"header_tokens:\n  - {hx.CANARY_TOKENS[3]}\n")
    (tdir / "lab_y.json").write_text(f'{{"footer": "{hx.CANARY_TOKENS[5].lower()}"}}\n')
    assert canary_hits(tmp_path) == [3, 5]


def test_canary_token_properties(reports):
    assert len(set(hx.CANARY_TOKENS)) == len(hx.CANARY_TOKENS)
    assert set(hx.CANARY_BY_FAMILY) == set(HX_IDS)
    for fam, toks in hx.CANARY_BY_FAMILY.items():
        assert 5 <= len(toks) <= 10, fam
        for i, _, _ in CASES:
            txt = reports[(fam, i)].all_text()
            assert all(t in txt for t in toks), (fam, i)  # every token is printed on every report
            for other, otoks in hx.CANARY_BY_FAMILY.items():
                if other != fam:
                    assert not any(t in txt for t in otoks), (fam, other)
    for t in hx.CANARY_TOKENS:
        assert not base.deny_hits(t)


def test_canary_not_in_shared_family_output():
    pts = sc.build_patients(5, random.Random(11))
    rep = templates.build_report("IN-A", split="dev", index=0, patient=pts[0], scenario_class="normal", target_pages=1)
    assert not any(t in rep.all_text() for t in hx.CANARY_TOKENS)


def test_readme_notice_is_first_line():
    first = (REPO / "tools/eval/layouts/heldout/README.md").read_text().lstrip().splitlines()[0]
    assert first.startswith("> DO NOT READ when working on extraction, the engine or templates (FR-5.4).")
    mod_doc = (REPO / "tools/eval/layouts/heldout/families.py").read_text().split('"""')[1]
    assert "DO NOT READ" in mod_doc


# ---------------------------------------------------------------- GN-01 heldout family checks

def test_gn01_heldout_families_excluded_from_devtest_and_registered():
    assert tuple(cfg.HELDOUT_ONLY_FAMILIES) == HX_IDS
    assert not set(HX_IDS) & set(cfg.SHARED_FAMILIES)
    assert cfg.HELDOUT_REPORTS_PER_ONLY_FAMILY == 8
    for fam in HX_IDS:
        assert FAMILIES[fam].scope == "heldout_only" and FAMILIES[fam].kind == "generic"
    for fam in cfg.SHARED_FAMILIES:
        assert fam not in FAMILIES or FAMILIES[fam].scope == "shared"
    assert cfg.HELDOUT_SEED != cfg.DEVTEST_SEED


@pytest.mark.parametrize("split", ["dev", "test"])
def test_gn01_heldout_only_families_refuse_dev_and_test(split):
    pt = sc.build_patients(5, random.Random(11))[0]
    for fam in HX_IDS:
        with pytest.raises(ValueError):
            hx.build_report(fam, split=split, index=0, patient=pt, scenario_class="normal", target_pages=1)


def test_gn01_seed_usage_and_determinism(reports):
    pts = sc.build_patients(12, random.Random(11))
    for (fam, i), rep in reports.items():
        assert rep.split == "heldout" and rep.report_id == f"H{i + 1:03d}"
        assert rep.seed == cfg.seed_for(cfg.HELDOUT_SEED, "heldout", i, "plan")
        assert rep.seed != cfg.seed_for(cfg.DEVTEST_SEED, "heldout", i, "plan")
    fam = HX_IDS[0]
    cls, pages = CASES[1][1:]
    again = hx.build_report(fam, split="heldout", index=1, patient=pts[1], scenario_class=cls, target_pages=pages)
    first = reports[(fam, 1)]
    assert again.all_text() == first.all_text() and [r.value_text for r in again.rows] == [r.value_text for r in first.rows]
    other = hx.build_report(fam, split="heldout", index=5, patient=pts[1], scenario_class=cls, target_pages=pages)
    assert other.all_text() != first.all_text()


# ---------------------------------------------------------------- generated-report invariants

def _row_ops(rep, row):
    pg = rep.pages[row.page - 1]
    x0, y0, x1, y1 = row.bbox
    return [(i, op) for i, op in enumerate(pg.ops)
            if isinstance(op, base.TextOp) and x0 - 1 <= op.x <= x1 + 1 and y0 - 2 <= op.y <= y1 + 2]



def test_geometry_gt_and_identity(reports):
    for (fam, i), rep in reports.items():
        W, H = rep.page_size
        assert 1 <= rep.n_pages <= 3
        for pg in rep.pages:
            texts = [op for op in pg.ops if isinstance(op, base.TextOp)]
            for op in texts:
                assert 0 <= op.x and op.x + base.text_width(op.text, op.font, op.size) <= W - 20, (fam, i)
                assert op.y <= H, (fam, i)
        for r in rep.rows:  # no two text runs of one row overlap (columns do not collide)
            ops = _row_ops(rep, r)
            boxes = [(op.x, op.y - op.size, op.x + base.text_width(op.text, op.font, op.size), op.y) for _, op in ops]
            for a in range(len(boxes)):
                for b in range(a + 1, len(boxes)):
                    A, B = boxes[a], boxes[b]
                    overlap = min(A[2], B[2]) - max(A[0], B[0]) > 0.5 and min(A[3], B[3]) - max(A[1], B[1]) > 1.0
                    assert not overlap, (fam, i, r.key)
        gt = lbase.build_gt(rep).to_dict()
        assert validate_gt(gt) == [], (fam, i)
        assert len({(r.key, r.section) for r in rep.rows}) == len(rep.rows)
        # fictional identity (3.9)
        text = rep.all_text()
        assert not base.deny_hits(text)
        assert any(p.search(text) for p in base.PHONE_PATTERNS)
        for host in re.findall(r"[\w.-]+@([\w.-]+)|www\.([\w.-]+)", text):
            assert any(h.endswith(("example.org", "example.com")) for h in host if h)
        assert re.search(r"FX-\d{5}", text)
        for r in rep.rows:
            assert r.value_text in r.line_text


def test_low6_heldout_structure_tests_live_behind_the_notice():
    """P1 review LOW-6: P3 engine builders read tests/, so this file must not describe how a heldout family is laid
    out. Those checks live in tests/heldout/, whose README and every module open with the DO-NOT-READ notice."""
    here = pathlib.Path(__file__).read_text(encoding="utf-8")
    forbidden = ["DESCRIP" + "TORS", "_NAME" + "_WIDTH"] + [f'"HX-{i}"' for i in (1, 2, 3)]
    assert [t for t in forbidden if t in here] == []
    notice = "DO NOT READ when working on extraction, the engine or templates (FR-5.4)."
    folder = REPO / "tests" / "heldout"
    assert (folder / "README.md").read_text(encoding="utf-8").lstrip("> ").startswith(notice)
    mods = sorted(folder.glob("*.py"))
    assert mods and all(p.read_text(encoding="utf-8").startswith(f'"""{notice}') for p in mods)
