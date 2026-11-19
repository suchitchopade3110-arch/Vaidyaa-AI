"""T08 checks for the heldout-only families (P1 plan 3.2.2, GN-01 family checks, GN-12 canary).

Only synthetic scratch reports are built here (never the heldout corpus), and no per-report output is printed.
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


def canary_hits(root: pathlib.Path) -> list[int]:
    """Indices of canary tokens found (case-insensitive) in any ``*.py`` under ``root``; never the text itself."""
    texts = [p.read_text(encoding="utf-8", errors="ignore").lower() for p in sorted(root.rglob("*.py"))]
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
    hits = canary_hits(REPO / "app")
    assert not hits, f"canary token index(es) found in app/: {hits}"  # indices only, never the text


def test_gn12_leak_detection_works_and_prints_only_the_index(tmp_path):
    (tmp_path / "engine.py").write_text("# ok\nx = 1\n")
    assert canary_hits(tmp_path) == []
    leaked = hx.CANARY_TOKENS[7]
    (tmp_path / "rules.py").write_text(f'LAB = "{leaked.upper()}"\n')  # case change must not hide a leak
    assert canary_hits(tmp_path) == [7]
    with pytest.raises(AssertionError) as exc:
        assert not canary_hits(tmp_path), f"canary token index(es) found in app/: {canary_hits(tmp_path)}"
    assert leaked.lower() not in str(exc.value).lower()


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


# Shared-family structure (plan 3.2 table): text-layer order, unit placement, flag placement, column order.
# Generic families whose columns/flags the plan leaves open are listed by what the plan states.
SHARED_STRUCTURE = {
    "IN-A": ("row_cell", "own_column", "none", ("name", "result", "unit", "range", "method")),
    "IN-B": ("row_cell", "own_column", "glued_after_result", ("name", "tech", "result", "unit", "range")),
    "IN-C": ("row_cell", "own_column", "none", ("name", "result", "unit", "range")),
    "IN-D": ("row_cell", "own_column", "none", ("name", "dated_results", "unit", "range")),
    "IN-E": ("left_panel_then_right", "panel_header", "none", ("name", "result", "range")),
    "US-A": ("row_cell", "own_column", "own_column", ("name", "result", "flag", "range", "unit", "lab")),
    "UK-A": ("row_cell", "inline_brackets", "none", ("name", "result+unit", "range")),
    "G01": ("row_cell", "own_column", "none", ("name", "result", "unit", "range")),
    "G02": ("row_string", "inline", "inline", ("name:result unit (Ref range) flag",)),
    "G03": ("row_cell", "own_column", "first_column", ("flag", "name", "result", "unit", "range")),
    "G04": ("row_string", "inline", "none", ("form lines",)),
    "G05": ("row_cell", "own_column", "none", ("name", "result", "unit", "range", "name", "result", "unit", "range")),
    "G06": ("row_cell", "header", "none", ("name", "result", "range")),
    "G07": ("row_cell", "own_column", "none", ("name", "dated_results", "unit", "range")),
    "G08": ("row_cell", "own_column", "none", ("name", "result", "unit", "range")),
    "G09": ("row_cell", "own_column", "none", ("name", "result", "unit", "range")),
    "G10": ("row_cell", "none", "none", ("name", "result", "range")),
}
DIMS = ("order", "unit", "flag", "columns")


def test_gn01_structure_differs_from_every_shared_family_in_two_dimensions():
    for fam, d in hx.DESCRIPTORS.items():
        mine = tuple(d[k] for k in DIMS)
        for other, theirs in SHARED_STRUCTURE.items():
            diff = sum(1 for a, b in zip(mine, theirs, strict=True) if a != b)
            assert diff >= 2, (fam, other)
    descs = [tuple(d[k] for k in (*DIMS, "font")) for d in hx.DESCRIPTORS.values()]
    assert len(set(descs)) == 3
    assert len({d["order"] for d in hx.DESCRIPTORS.values()}) == 3
    assert len({tuple(d["columns"]) for d in hx.DESCRIPTORS.values()}) == 3


def _row_ops(rep, row):
    pg = rep.pages[row.page - 1]
    x0, y0, x1, y1 = row.bbox
    return [(i, op) for i, op in enumerate(pg.ops)
            if isinstance(op, base.TextOp) and x0 - 1 <= op.x <= x1 + 1 and y0 - 2 <= op.y <= y1 + 2]


def test_descriptors_match_real_text_layer_order(reports):
    """The declared text-layer orders are what the drawn pages actually do (checked on the drawing ops)."""

    def first_idx(rep, r, pred):
        return min(idx for idx, op in _row_ops(rep, r) if pred(op))

    checked = {fam: 0 for fam in HX_IDS}
    for (fam, _i), rep in reports.items():
        runs: dict[tuple[int, str], list] = {}
        for r in rep.rows:
            runs.setdefault((r.page, r.section), []).append(r)
        for rs in runs.values():
            if fam == "HX-1" and len(rs) >= 3:  # column-major: all names of the run precede its first result
                names = [first_idx(rep, r, lambda o, r=r: o.text == base.sanitize(r.name)) for r in rs]
                vals = [first_idx(rep, r, lambda o, r=r: o.text.endswith(base.sanitize(r.value_text))
                                  and 300 < o.x < 405) for r in rs]
                assert max(names) < min(vals)
                checked[fam] += 1
            elif fam == "HX-2":  # bottom-up: the stacked range line precedes the name
                for r in rs:
                    nm = first_idx(rep, r, lambda o, r=r: o.text == base.sanitize(r.name))
                    rg = [idx for idx, op in _row_ops(rep, r) if op.text.startswith("Normal ")]
                    assert rg and max(rg) < nm
                    checked[fam] += 1
            elif fam == "HX-3":  # right to left: range, then name, then result
                for r in rs:
                    nm = first_idx(rep, r, lambda o, r=r: o.text == base.sanitize(r.name))
                    rg = [idx for idx, op in _row_ops(rep, r) if op.x >= 400]
                    vl = [idx for idx, op in _row_ops(rep, r) if op.x < 130]
                    assert rg and vl and max(rg) < nm < min(vl)
                    checked[fam] += 1
    assert all(n > 0 for n in checked.values())


# ---------------------------------------------------------------- generated-report invariants

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


def test_hx2_unit_is_glued_and_hx1_flag_is_trailing(reports):
    for (fam, i), rep in reports.items():
        for r in rep.rows:
            if fam == "HX-2" and r.unit:
                assert r.unit_placement == "inline"
                assert f"{r.value_text} {r.unit}" in r.line_text
            if fam == "HX-1" and r.printed_flag:
                assert r.line_text.endswith(r.printed_flag)
