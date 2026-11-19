"""The three heldout-only layout families, canary tokens and DO-NOT-READ notice (P1 plan 3.2.2, 3.9, 6.11).

DO NOT READ when working on extraction, the engine or templates (FR-5.4). Reading this folder or
``eval/corpus/heldout/`` to write rules invalidates the heldout metric.

GT-side module: imports nothing from ``app``. Pure and deterministic. All names, addresses, phones and logos are
fictional (section 3.9). The families reuse the shared value engine and drawing kit (``templates._Gen`` and
``base``) so only the page STRUCTURE differs; they refuse every split except ``heldout``.
"""

from __future__ import annotations

import dataclasses
import random
from dataclasses import dataclass

from tools.eval import config as cfg
from tools.eval import scenarios as sc
from tools.eval.catalogue import CATALOGUE
from tools.eval.layouts import base, templates
from tools.eval.layouts.base import (
    A4, FONT_BOLD, FONT_ITALIC, FONT_MONO, FONT_REGULAR, FONT_SERIF, FONT_SERIF_BOLD, LETTER, Cell, Column, DocBuilder,
    PrintedReport, PrintedRow, simple_cell,
)
from tools.eval.layouts.templates import PANELS_IN, PANELS_US, FamilySpec, Lay, _Gen
from tools.eval.scenarios import Patient

FONT_MONO_BOLD = "Courier-Bold"

# Canary tokens (plan 3.2.2 item 4): every token is printed on EVERY report of its family, and none may ever
# appear in ``app/`` (GN-12). Invented words only; no real organisation, brand or accreditation body.
CANARY_BY_FAMILY: dict[str, tuple[str, ...]] = {
    "HX-1": ("Kestrelmoor Pathology Annex", "Zyleth Ledger Sheet", "Zyleth Span", "Obtainium Reading",
             "Tallyview Edition 9"),
    "HX-2": ("Vorrelin Assay House", "Vorrelin Assay Summary", "Parameter Dossier", "Quillfig Value",
             "Orbitrex Seal Copy"),
    "HX-3": ("Tamblor Wickham Diagnostics", "Tamblor Result Register", "Sundrel Reading", "Brindle Norm",
             "Pellucid Register 4"),
}
CANARY_TOKENS: tuple[str, ...] = tuple(t for fam in sorted(CANARY_BY_FAMILY) for t in CANARY_BY_FAMILY[fam])

_LAB = {"HX-1": "Kestrelmoor Pathology Annex", "HX-2": "Vorrelin Assay House", "HX-3": "Tamblor Wickham Diagnostics"}
_TITLE = {"HX-1": "Zyleth Ledger Sheet", "HX-2": "Vorrelin Assay Summary", "HX-3": "Tamblor Result Register"}
_TAG = {"HX-1": "Tallyview Edition 9", "HX-2": "Orbitrex Seal Copy", "HX-3": "Pellucid Register 4"}
_SLUG = {"HX-1": "kestrelmoor", "HX-2": "vorrelin", "HX-3": "tamblor"}

# Column geometry. Result columns are right-aligned ('r'); the name width is the room left of the next column.
SPECS: dict[str, FamilySpec] = {
    "HX-1": FamilySpec(
        "HX-1", "IN", A4, FONT_MONO, FONT_MONO_BOLD, 8.0, 1, "hx1", "in", "indian",
        (Column("name", "Assay Line", 40), Column("range", "Zyleth Span", 236),
         Column("result", "Obtainium Reading", 400, "r"), Column("unit", "Measure", 408), Column("flag", "Alert", 512)),
        PANELS_IN, ("to", "bracket"), ("word",), True, 0.05, si_p=0.2, cumm_p=0.3, flag_mode="column",
        abs_counts=True, template_id=None,
    ),
    "HX-2": FamilySpec(
        "HX-2", "IN", A4, FONT_REGULAR, FONT_BOLD, 8.5, 0, "hx2", "in", "indian",
        (Column("name", "Parameter Dossier", 40), Column("result", "Quillfig Value", 408, "r"),
         Column("flag", "Quillfig Mark", 440)),
        PANELS_IN, ("endash", "tight"), ("sym",), True, 0.0, si_p=0.3, flag_mode="column",
        sex_word=("Male", "Female"), template_id=None,
    ),
    "HX-3": FamilySpec(
        "HX-3", "US", LETTER, FONT_SERIF, FONT_SERIF_BOLD, 9.5, 3, "hx3", "us", "western",
        (Column("result", "Sundrel Reading", 126, "r"), Column("unit", "Unit", 134), Column("name", "Parameter", 196),
         Column("range", "Brindle Norm", 408)),
        PANELS_US, ("paren", "to"), ("sym", "word"), True, 0.0, si_p=0.3, flag_mode="column", abs_counts=True,
        template_id=None,
    ),
}
_NAME_WIDTH = {"HX-1": 190.0, "HX-2": 250.0, "HX-3": 198.0}

# Structure descriptors (used by the GN-01 distinctness check). Dimensions: text-layer order, unit placement,
# flag placement, column order, font family.
DESCRIPTORS: dict[str, dict[str, object]] = {
    "HX-1": {"order": "column_major", "unit": "own_column", "flag": "trailing_column",
             "columns": ("name", "range", "result", "unit", "flag"), "font": "Courier"},
    "HX-2": {"order": "stack_bottom_up", "unit": "glued_to_result", "flag": "parenthesised_after_value",
             "columns": ("name+range", "result+unit", "flag"), "font": "Helvetica"},
    "HX-3": {"order": "cells_right_to_left", "unit": "leading_column", "flag": "prefix_in_result",
             "columns": ("result", "unit", "name", "range"), "font": "Times"},
}


class _HXGen(_Gen):
    """Shared value engine; only the name-column width differs."""

    def _name_width(self) -> float:
        return _NAME_WIDTH[self.spec.fid]


@dataclass
class _Rec:
    page: int
    start: int
    sizes: list[int]

    @property
    def n(self) -> int:
        return sum(self.sizes)


# ---------------------------------------------------------------- header, column header, rows

def _header_height(spec: FamilySpec, first: bool) -> float:
    if not first:
        return 56.0
    return {"hx1": 142.0, "hx2": 132.0, "hx3": 112.0}[spec.header_style]


def _draw_header(doc: DocBuilder, spec: FamilySpec, lay: Lay, pt: Patient, page_no: int) -> None:
    ident = lay.ident
    W = doc.size[0]
    mx = doc.margin_x
    rx = W - mx
    y = doc.top
    f, fb = spec.font, spec.bold
    pl = base.patient_lines(pt, ident, spec.date_style)
    if page_no > 1:
        doc.put(mx, y + 2, ident.lab_name, fb, 10)
        doc.put(rx, y + 2, pl["pid"][0], f, 8, align="r", decoy="header_id")
        doc.put(mx, y + 16, pl["name"][0], f, 8)
        doc.put(rx, y + 16, pl["labno"][0], f, 8, align="r", decoy="header_id")
        doc.line(mx, y + 34, rx, y + 34, 0.6)
        doc.y = doc.top + _header_height(spec, False)
        return
    style = spec.header_style
    if style == "hx1":
        doc.rect(mx, y, rx - mx, 26, fill=0.88)
        base.draw_logo(doc, mx + 3, y + 2, ident, 11)
        doc.put(mx + 34, y + 7, ident.lab_name, fb, 12)
        doc.put(rx - 4, y + 8, _TITLE["HX-1"], fb, 9, align="r")
        doc.put(mx, y + 32, ident.address, f, 8, decoy="address")
        doc.put(mx, y + 43, f"Tel: {ident.phone}   Toll Free: {ident.tollfree}", f, 8, decoy="phone")
        yy = y + 60
        for k in ("name", "agesex", "pid", "labno", "sample", "ref"):
            if k in pl:
                doc.put(mx, yy, pl[k][0], f, 8, decoy=pl[k][1])
                yy += 11
        doc.put(W / 2 + 40, y + 60, f"Collected: {lay.collected}", f, 8, decoy="date")
        doc.put(W / 2 + 40, y + 71, f"Reported: {lay.reported}", f, 8, decoy="date")
    elif style == "hx2":
        doc.put(mx, y, ident.lab_name, fb, 16)
        doc.put(mx, y + 21, ident.address, f, 8, decoy="address")
        doc.put(mx, y + 32, f"Tel: {ident.phone}  |  {ident.email}", f, 8, decoy="phone")
        base.draw_logo(doc, rx - 34, y, ident, 17)
        doc.put(rx, y + 40, _TITLE["HX-2"], fb, 9.5, align="r")
        doc.line(mx, y + 54, rx, y + 54, 1.4)
        yy = y + 62
        for k in ("name", "agesex", "ref"):
            if k in pl:
                doc.put(mx + 4, yy, pl[k][0], f, 8.5, decoy=pl[k][1])
                yy += 12.5
        yy = y + 62
        for k in ("pid", "labno", "sample"):
            doc.put(W / 2 + 20, yy, pl[k][0], f, 8.5, decoy=pl[k][1])
            yy += 12.5
        doc.put(mx + 4, y + 100, f"Collected: {lay.collected}", f, 8.5, decoy="date")
        doc.put(mx + 160, y + 100, f"Reported: {lay.reported}", f, 8.5, decoy="date")
        doc.line(mx, y + 116, rx, y + 116, 0.5)
    else:  # hx3
        doc.put(W / 2, y, ident.lab_name, fb, 15, align="c")
        doc.put(W / 2, y + 19, f"{ident.address}  |  Phone: {ident.phone}", f, 8, align="c", decoy="address")
        doc.put(W / 2, y + 31, _TITLE["HX-3"], fb, 10, align="c")
        doc.line(mx, y + 46, rx, y + 46, 1.2)
        doc.line(mx, y + 49, rx, y + 49, 0.4)
        cols = (("name", "agesex"), ("pid", "labno"), ("sample", "ref"))
        for ci, ks in enumerate(cols):
            yy = y + 56
            for k in ks:
                if k in pl:
                    doc.put(mx + ci * 175, yy, pl[k][0], f, 8.5, decoy=pl[k][1])
                    yy += 12
        doc.put(mx, y + 84, f"Collected: {lay.collected}", f, 8.5, decoy="date")
        doc.put(mx + 175, y + 84, f"Reported: {lay.reported}", f, 8.5, decoy="date")
    doc.y = doc.top + _header_height(spec, True)


def _col_header(doc: DocBuilder, spec: FamilySpec, title: str, cont: bool) -> None:
    mx = doc.margin_x
    rx = doc.size[0] - mx
    t = f"{title} (contd.)" if cont else title
    doc.rect(mx, doc.y, rx - mx, 15, fill=0.9)
    doc.put(mx + 4, doc.y + 3, t, spec.bold, spec.size + 0.5)
    doc.y += 18
    for c in spec.columns:
        doc.put(c.x, doc.y, c.header, spec.bold, spec.size - 0.5, align=c.align)
    doc.y += 12
    doc.line(mx, doc.y - 1, rx, doc.y - 1, 0.6)
    doc.y += 3


def _cells(spec: FamilySpec, r: PrintedRow) -> list[Cell]:
    f, fb, sz = spec.font, spec.bold, spec.size
    val = f"{r.qualifier} {r.value_text}" if r.qualifier else r.value_text
    bold = fb if r.printed_flag else f
    if spec.fid == "HX-1":  # name | range | result | unit | flag
        return [simple_cell(40, r.name, f, sz), simple_cell(236, r.range_lines, f, sz),
                simple_cell(400, val, bold, sz, "r"), simple_cell(408, r.unit, f, sz),
                simple_cell(512, r.printed_flag, fb, sz)]
    if spec.fid == "HX-2":  # name with the range stacked underneath | result glued to unit | (flag)
        under = [(f"Normal {ln}", FONT_ITALIC, 7.0) for ln in r.range_lines]
        res = f"{val} {r.unit}" if r.unit else val
        return [Cell(40, [(r.name, f, sz)] + under), simple_cell(408, res, bold, sz, "r"),
                simple_cell(440, f"({r.printed_flag})" if r.printed_flag else "", fb, sz)]
    res = f"{r.printed_flag} {val}" if r.printed_flag else val  # HX-3: result | unit | name | range
    return [simple_cell(126, res, bold, sz, "r"), simple_cell(134, r.unit, f, sz), simple_cell(196, r.name, f, sz),
            simple_cell(408, r.range_lines, f, sz - 0.5)]


def _layout(spec: FamilySpec, lay: Lay, pt: Patient, rows: list[PrintedRow], notes: dict[str, str], pad: float,
            tg_text: str) -> DocBuilder:
    def on_page(doc: DocBuilder, n: int, is_break: bool) -> None:
        _draw_header(doc, spec, lay, pt, n)

    doc = DocBuilder(spec.page, margin_x=40.0, top=34.0, bottom=84.0, on_new_page=on_page)
    recs: list[_Rec] = []
    order: list[str] = []
    for r in rows:
        if r.section not in order:
            order.append(r.section)

    def emit(r: PrintedRow) -> None:
        cells = _cells(spec, r)
        if doc.y + doc.row_height(cells, pad) > doc.limit:
            doc.new_page()
            _col_header(doc, spec, r.section, True)
        doc.row(r, cells, pad)
        sizes = [len(c.lines) for c in cells]
        recs.append(_Rec(r.page, len(doc.pages[r.page - 1].ops) - sum(sizes), sizes))

    for title in order:
        srows = [r for r in rows if r.section == title]
        doc.ensure(18 + 15 + doc.row_height(_cells(spec, srows[0]), pad) * 2)
        _col_header(doc, spec, title, False)
        for r in srows:
            emit(r)
        if notes.get(title) == "tg" and any(r.key == "triglycerides" for r in srows) \
                and not any(r.key == "ldl" for r in srows):
            doc.ensure(16)
            doc.put(doc.margin_x + 4, doc.y + 2, f"LDL: Not calculated (TG > {tg_text})", FONT_ITALIC, 8,
                    decoy="not_calculated")
            doc.y += 15
        doc.y += 8

    def footer(d: DocBuilder, pg, total: int) -> None:
        base.draw_standard_footer(d, pg, total, lay.ident, font=spec.font, size=7.0,
                                  right_edge=d.size[0] - d.margin_x)
        d.put(d.margin_x, d.size[1] - 16, _TAG[spec.fid], spec.font, 6.5, decoy="comment", page=pg)

    doc.finish(footer)
    doc._hx_records = recs  # type: ignore[attr-defined]
    return doc


def _reorder(doc: DocBuilder, spec: FamilySpec) -> None:
    """Rewrite the text-layer (drawing) order of result rows without moving anything on the page."""
    by_page: dict[int, list[_Rec]] = {}
    for r in doc._hx_records:  # type: ignore[attr-defined]
        by_page.setdefault(r.page, []).append(r)
    for pn, rs in by_page.items():
        ops = doc.pages[pn - 1].ops
        rs.sort(key=lambda r: r.start)
        runs: list[list[_Rec]] = []
        for r in rs:
            if runs and r.start == runs[-1][-1].start + runs[-1][-1].n:
                runs[-1].append(r)
            else:
                runs.append([r])
        for run in runs:
            lo = run[0].start
            groups: list[list[list]] = []
            pos = lo
            for r in run:
                g = []
                for s in r.sizes:
                    g.append(ops[pos:pos + s])
                    pos += s
                groups.append(g)
            new: list = []
            if spec.fid == "HX-1":  # column-major: every name of the run, then every range, ...
                for ci in range(max(len(g) for g in groups)):
                    for g in groups:
                        if ci < len(g):
                            new.extend(g[ci])
            elif spec.fid == "HX-2":  # bottom-up: last text line of the row first
                for g in groups:
                    new.extend(reversed([op for cell in g for op in cell]))
            else:  # HX-3: cells right to left
                for g in groups:
                    for cell in reversed(g):
                        new.extend(cell)
            ops[lo:pos] = new


# ---------------------------------------------------------------- pagination fit and builder

def _fit(spec, lay, pt, rows, notes, target, rng, tg_text):
    n_all = len(rows)
    n_min = len({id(r) for r in rows if r.state in ("mild", "severe", "critical")})
    lo_pad = {1: 3.0, 2: 5.0, 3: 9.0}[target]
    pads = [p for p in (lay.start_pad + k * 2.0 for k in range(14)) if p >= lo_pad - 1.5]

    def pages(n: int, pad: float) -> int:
        return len(_layout(spec, lay, pt, templates._select(rows, n), notes, pad, tg_text).pages)

    for pad in pads:
        if n_all < 4 or pages(n_all, pad) < target or pages(max(n_min, 4), pad) > target:
            continue
        a, b = max(n_min, 4), n_all
        while a < b:
            m = (a + b) // 2
            if pages(m, pad) >= target:
                b = m
            else:
                a = m + 1
        n_lo = a
        a, b = n_lo, n_all
        while a < b:
            m = (a + b + 1) // 2
            if pages(m, pad) <= target:
                a = m
            else:
                b = m - 1
        sel = templates._select(rows, rng.randint(n_lo, a))
        doc = _layout(spec, lay, pt, sel, notes, pad, tg_text)
        if len(doc.pages) == target:
            return doc, sel
    return None


def build_report(family: str, *, split: str, index: int, patient: Patient, scenario_class: str, target_pages: int,
                 report_id: str | None = None) -> PrintedReport:
    """Build one heldout-only report. Seeds always come from ``HELDOUT_SEED``; other splits are refused."""
    if split != "heldout":
        raise ValueError(f"{family} is heldout-only (got split {split!r})")
    spec = SPECS[family]
    master = cfg.master_seed(split)
    seed = lambda purpose: cfg.seed_for(master, split, index, purpose)  # noqa: E731
    rid = report_id or f"H{index + 1:03d}"
    lrng = random.Random(seed("layout"))
    ident0 = base.make_identity(random.Random(seed("names")), spec.locale)
    name = _LAB[family]
    ident = dataclasses.replace(
        ident0, lab_name=name, initials="".join(w[0] for w in name.split()[:2]).upper(),
        email=f"info@{_SLUG[family]}.example.org", web=f"www.{_SLUG[family]}.example.com")
    collected, reported = base.fixed_dates(lrng)
    foots = ("Results relate only to the specimen received.",)
    lay = Lay(
        ident=ident, collected=base.fmt_date(collected, spec.date_style),
        reported=base.fmt_date(reported, spec.date_style), syntax=lrng.choice(spec.two_sided),
        one_sided=lrng.choice(spec.one_sided), si=lrng.random() < spec.si_p, cumm=lrng.random() < spec.cumm_p,
        size=spec.size, unit_spell={k: lrng.randint(0, 3) for k in CATALOGUE},
        name_pick={k: lrng.randint(0, 4) for k in CATALOGUE}, footnotes=foots, lab_codes={},
        start_pad=lrng.uniform(3.0, 6.0) if target_pages == 1 else lrng.uniform(5.0, 8.0) if target_pages == 2
        else lrng.uniform(9.0, 12.0),
    )
    panels = list(spec.panels)
    rest = panels[1:]
    lrng.shuffle(rest)
    spec2 = FamilySpec(**{**spec.__dict__, "panels": tuple([panels[0]] + rest)})
    tg_text = "4.5 mmol/L" if lay.si else "400 mg/dL"
    for attempt in range(templates.MAX_ATTEMPTS):
        srng = random.Random(seed("scenario") + attempt)
        vrng = random.Random(seed("values") + attempt)
        gen0 = _HXGen(spec2, lay, patient, scenario_class, vrng, sc.ScenarioSpec(scenario_class))
        plan = sc.plan_scenario(scenario_class, srng, patient, gen0.candidates())
        gen = _HXGen(spec2, lay, patient, scenario_class, vrng, plan)
        rows = gen.run()
        fit = _fit(spec2, lay, patient, rows, gen.notes, target_pages, vrng, tg_text)
        if fit is None:
            continue
        doc, sel = fit
        if not templates._headline_ok(scenario_class, sel, patient):
            continue
        if family == "HX-2":
            for r in sel:
                r.unit_placement = "inline" if r.unit else "none"
        _reorder(doc, spec2)
        return PrintedReport(
            report_id=rid, split=split, family=family, seed=seed("plan"), page_size=spec.page,
            lab={"name": ident.lab_name, "address": ident.address, "phone": ident.phone, "fictional": True},
            patient=patient, scenario={"class": scenario_class, "clusters": list(plan.clusters)},
            unit_system="SI" if any("si" in r.features for r in sel) else "conventional", rows=sel,
            decoys=doc.decoys, pages=doc.pages, features=sorted({f for r in sel for f in r.features}),
            template_id=None,
        )
    raise RuntimeError(f"{family}: could not build {rid} (class {scenario_class}, {target_pages} pages)")


__all__ = ["CANARY_BY_FAMILY", "CANARY_TOKENS", "DESCRIPTORS", "SPECS", "build_report"]
