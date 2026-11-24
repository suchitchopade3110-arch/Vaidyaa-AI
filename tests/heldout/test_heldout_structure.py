"""DO NOT READ when working on extraction, the engine or templates (FR-5.4).

Reading this file to write extraction, engine or template rules invalidates the heldout metric (plan 3.2.2,
P1 review LOW-6). Structure checks of the heldout-only families: the declared descriptors differ from every shared
family and match the drawn text layer. Only synthetic scratch reports are built (never the heldout corpus).
"""

from __future__ import annotations

import random

import pytest

from tools.eval import scenarios as sc
from tools.eval.layouts import FAMILIES, base
from tools.eval.layouts.heldout import families as hx

HX_IDS = tuple(hx.SPECS)
CASES = [(0, "normal", 1), (1, "severe", 2), (2, "critical", 3), (3, "mild", 2)]


@pytest.fixture(scope="module")
def reports():
    pts = sc.build_patients(12, random.Random(11))
    out = {}
    for fam in HX_IDS:
        for i, cls, pages in CASES:
            out[(fam, i)] = FAMILIES[fam].builder(fam, split="heldout", index=i, patient=pts[i], scenario_class=cls,
                                                  target_pages=pages)
    return out


@pytest.mark.parametrize("fam", HX_IDS)
def test_heldout_value_engine_accepts_the_base_name_width_signature(fam):
    """T16 (zoro): ``templates._Gen._name_width(unit_txt="")`` is called with a unit text by the shared row builder;
    the heldout override must accept it (T10 crash), and the width stays the family's fixed name-column width."""
    gen = object.__new__(hx._HXGen)  # only ``spec`` is read by the override
    gen.spec = hx.SPECS[fam]
    assert gen._name_width("mmol/L") == gen._name_width() == hx._NAME_WIDTH[fam]


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


def test_hx2_unit_is_glued_and_hx1_flag_is_trailing(reports):
    for (fam, i), rep in reports.items():
        for r in rep.rows:
            if fam == "HX-2" and r.unit:
                assert r.unit_placement == "inline"
                assert f"{r.value_text} {r.unit}" in r.line_text
            if fam == "HX-1" and r.printed_flag:
                assert r.line_text.endswith(r.printed_flag)
