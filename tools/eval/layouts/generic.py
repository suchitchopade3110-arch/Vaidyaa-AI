"""Generic layout families G01 to G10 (P1 plan section 3.2, task T07).

Ten structurally different layouts built on the T05 kit (``base``) and the T05 value engine (``templates``):

* G01 plain 4-column table; G02 colon lines (one string per row, inline range and word flag);
* G03 flag-first table (optional Method column); G04 form-style single tests with interpretive tables;
* G05 two tests per visual line (half-width tables, portrait or landscape); G06 units in headers or names,
  15% of count rows unit-less (AMB_UNITLESS_COUNT); G07 dated cumulative columns, newest on the left,
  5% unlabelled multi-value rows (AMB_UNLABELLED_MULTIVALUE);
* G08 mixed specimen (chemistry, urine routine, spot electrolytes, serology; 30% urine-only with a phone footer);
* G09 CSF with paired blood glucose; G10 serology and immunology (S/Co index lines, Widal titres with and
  without a printed cut-off, Dengue, VDRL, blood group).

Every name, address and phone is fictional (section 3.9, via ``base.make_identity``). GT-side module: imports
nothing from ``app``. Pure and deterministic: every random draw comes from ``random.Random`` instances seeded
by ``config.seed_for``. Private helpers of ``templates`` (``_Gen``, ``Lay``, ``_make_range`` ...) are reused so
that value sampling, range syntax, SI units and invariants stay identical to the template families.
"""

from __future__ import annotations

import datetime
import random
from dataclasses import dataclass, field, replace
from decimal import Decimal

from tools.eval import config as cfg
from tools.eval import scenarios as sc
from tools.eval import units
from tools.eval.catalogue import CATALOGUE, COUNT_KEYS
from tools.eval.clinical import PrintedRange
from tools.eval.layouts import base, templates as tpl
from tools.eval.layouts.base import (
    A4, FONT_BOLD, FONT_ITALIC, FONT_MONO, FONT_REGULAR, FONT_SERIF, FONT_SERIF_BOLD, Cell, Column, DocBuilder,
    PrintedReport, PrintedRow, simple_cell,
)
from tools.eval.scenarios import Patient

MAX_ATTEMPTS = 80
A4_L = (A4[1], A4[0])
FONT_MONO_BOLD = "Courier-Bold"

# ---------------------------------------------------------------- panels
_CBC, _KFT, _LFT, _LIPID, _DIAB = tpl.CBC, tpl.KFT, tpl.LFT, tpl.LIPID, tpl.DIAB
_BASE = (
    ("Haematology", _CBC), ("Glycaemic Control", _DIAB), ("Lipid Profile", _LIPID), ("Liver Function", _LFT),
    ("Renal Function and Electrolytes", _KFT), ("Thyroid", tpl.THY), ("Iron Studies", tpl.IRON),
    ("Vitamins", tpl.VIT), ("Coagulation", tpl.COAG), ("Cardiac Markers", tpl.CARD), ("Special Chemistry", tpl.MISC),
)
_G04 = (
    ("Biochemistry", _DIAB + ("hdl", "ldl", "cholesterol", "triglycerides") + ("urea", "creatinine", "uric_acid")),
    ("Hormones and Vitamins", tpl.THY + tpl.VIT + ("ferritin",)),
    ("Haematology", ("hemoglobin", "wbc", "platelets", "esr")),
    ("Liver", ("bilirubin_total", "ast", "alt", "alp", "albumin")),
)
_G06 = (("Complete Blood Count", _CBC), ("Kidney Profile", _KFT[:8]), ("Liver Profile", _LFT), ("Lipid Panel", _LIPID))
_URINE = ("urine_colour", "urine_appearance", "specific_gravity", "urine_ph", "urine_protein", "urine_glucose",
          "urine_ketones", "urine_blood", "urine_bilirubin", "urine_urobilinogen", "urine_nitrite",
          "urine_leukocyte_esterase", "urine_pus_cells", "urine_rbc", "urine_epithelial", "urine_casts",
          "urine_crystals", "urine_bacteria")
_SPOT = ("urine_sodium", "urine_potassium", "urine_creatinine", "microalbumin", "microalbumin_creatinine")
_G08_BLOOD = ("BLOOD CHEMISTRY", ("glucose_fasting", "urea", "creatinine", "uric_acid", "egfr", "sodium", "potassium",
                                  "chloride", "calcium", "bilirubin_total", "ast", "alt", "alp", "total_protein",
                                  "albumin"))
_G08_URINE = ("URINE ROUTINE EXAMINATION", _URINE)
_G08_SPOT = ("URINE SPOT ELECTROLYTES", _SPOT)
_G08_SERO = ("SEROLOGY", ("hbsag", "hiv_screen", "vdrl", "anti_hcv"))
_G09 = (
    ("CSF ANALYSIS", ("csf_appearance", "csf_glucose", "csf_protein", "csf_chloride", "csf_wbc", "csf_neutrophils_pct",
                      "csf_lymphocytes_pct")),
    ("PAIRED BLOOD GLUCOSE", ("glucose_random",)),
    ("BLOOD CHEMISTRY", ("urea", "creatinine", "sodium", "potassium", "calcium", "bilirubin_total")),
    ("HAEMATOLOGY", ("hemoglobin", "wbc", "platelets", "esr", "neutrophils_pct", "lymphocytes_pct")),
)
_G10 = (
    ("SEROLOGY AND IMMUNOLOGY", ("hiv_screen", "hbsag", "anti_hcv", "vdrl", "dengue_ns1", "dengue_igm", "ra_factor",
                                 "aso")),
    ("WIDAL TEST", ("widal_o", "widal_h", "widal_ah", "widal_bh")),
    ("BLOOD GROUP", ("blood_group_abo", "blood_group_rh")),
    ("THYROID AND VITAMINS", ("tsh", "t3", "t4", "free_t4", "vitamin_b12", "vitamin_d", "ferritin")),
    ("HAEMATOLOGY", ("hemoglobin", "wbc", "platelets", "esr")),
    ("INFLAMMATORY MARKERS", ("crp", "procalcitonin")),
)
_URINE_SUB = {
    "urine_colour": "Physical Examination", "urine_appearance": "Physical Examination",
    "specific_gravity": "Physical Examination", "urine_ph": "Physical Examination",
    "urine_pus_cells": "Microscopic Examination", "urine_rbc": "Microscopic Examination",
    "urine_epithelial": "Microscopic Examination", "urine_casts": "Microscopic Examination",
    "urine_crystals": "Microscopic Examination", "urine_bacteria": "Microscopic Examination",
}
_CSF_SUB = {"csf_appearance": "Physical", "csf_glucose": "Chemical", "csf_protein": "Chemical",
            "csf_chloride": "Chemical", "csf_wbc": "Cytology", "csf_neutrophils_pct": "Cytology (differential)",
            "csf_lymphocytes_pct": "Cytology (differential)"}


# ---------------------------------------------------------------- family configuration

@dataclass(frozen=True)
class Conf:
    page: tuple[float, float]
    font: str
    bold: str
    size: float
    header: str  # A..E header style
    band: str  # fill | rule | centre | plain
    margin: float
    variant: int
    syntaxes: tuple[str, ...]
    one_sided: tuple[str, ...]
    sex_labelled: bool
    age_label_p: float
    si_p: float
    absent_p: float
    flag_mode: str | None
    shuffle: bool
    name_w: float
    kind: str  # table | lines | blocks | pairs
    abs_counts: bool = False


_DASH = ("dash", "dash", "endash", "to")
CONFS: dict[str, Conf] = {
    "G01": Conf(A4, FONT_REGULAR, FONT_BOLD, 9.0, "A", "fill", 40, 0, _DASH, ("sym", "sym", "word"), True, 0.08, 0.30,
                0.05, None, True, 240, "table"),
    "G02": Conf(A4, FONT_MONO, FONT_MONO_BOLD, 8.5, "B", "rule", 40, 1, ("tight", "dash", "to"), ("sym", "word"),
                False, 0.0, 0.30, 0.05, None, True, 150, "lines"),
    "G03": Conf(A4, FONT_REGULAR, FONT_BOLD, 8.5, "C", "fill", 40, 2, ("dash", "bracket", "paren"), ("sym",), False,
                0.0, 0.30, 0.0, "column", True, 205, "table"),
    "G04": Conf(A4, FONT_REGULAR, FONT_BOLD, 9.0, "D", "centre", 46, 0, ("dash", "tight", "to"), ("sym", "word"), True,
                0.10, 0.30, 0.0, None, False, 300, "blocks"),
    "G05": Conf(A4, FONT_REGULAR, FONT_BOLD, 8.0, "E", "fill", 36, 1, ("tight", "dash"), ("sym",), False, 0.0, 0.30,
                0.0, "glued", True, 108, "pairs"),
    "G06": Conf(A4, FONT_SERIF, FONT_SERIF_BOLD, 9.5, "A", "rule", 40, 1, ("dash", "paren"), ("sym",), False, 0.0, 0.0,
                0.0, None, False, 255, "table", True),
    "G07": Conf(A4_L, FONT_REGULAR, FONT_BOLD, 8.5, "E", "fill", 40, 1, ("dash", "tight"), ("sym",), False, 0.0, 0.30,
                0.0, None, True, 250, "table"),
    "G08": Conf(A4, FONT_REGULAR, FONT_BOLD, 9.0, "C", "rule", 40, 0, ("dash", "to"), ("sym", "word"), True, 0.0, 0.0,
                0.0, None, False, 240, "table"),
    "G09": Conf(A4, FONT_SERIF, FONT_SERIF_BOLD, 9.5, "D", "fill", 40, 1, ("dash", "tight"), ("sym",), False, 0.0, 0.0,
                0.0, "column", False, 235, "table"),
    "G10": Conf(A4, FONT_REGULAR, FONT_BOLD, 9.0, "E", "centre", 40, 2, ("dash", "paren"), ("sym", "word"), False, 0.0,
                0.0, 0.0, None, False, 195, "table"),
}


def _spec(fid: str, panels, **over) -> tpl.FamilySpec:
    c = CONFS[fid]
    return tpl.FamilySpec(
        fid, "generic", c.page, c.font, c.bold, c.size, c.variant, "g", "in", "western",
        (Column("name", "Test", c.margin),), tuple(panels), c.syntaxes, c.one_sided, c.sex_labelled, c.age_label_p,
        si_p=c.si_p, flag_mode=c.flag_mode, abs_counts=c.abs_counts, template_id=None,
    ) if not over else replace(_spec(fid, panels), **over)


SPECS: dict[str, tpl.FamilySpec] = {fid: _spec(fid, _BASE) for fid in CONFS}


def _panels_for(fid: str, extra: dict) -> tuple:
    if fid in ("G01", "G02", "G03", "G05", "G07"):
        return _BASE
    return {"G04": _G04, "G06": _G06, "G09": _G09, "G10": _G10,
            "G08": (_G08_URINE, _G08_SPOT) if extra.get("urine_only") else (_G08_BLOOD, _G08_URINE, _G08_SPOT,
                                                                           _G08_SERO)}[fid]


# ---------------------------------------------------------------- per-report context

@dataclass
class Ctx:
    fid: str
    conf: Conf
    spec: tpl.FamilySpec
    lay: tpl.Lay
    pt: Patient
    page: tuple[float, float]
    extra: dict
    sub: dict[int, str] = field(default_factory=dict)
    idx: dict[int, str] = field(default_factory=dict)  # id(row) -> index line (G10)


def _val(r: PrintedRow) -> str:
    return f"{r.qualifier} {r.value_text}" if r.qualifier else r.value_text


# ---------------------------------------------------------------- the value engine (templates._Gen plus non-numeric rows)

class _GGen(tpl._Gen):
    def __init__(self, ctx: Ctx, rng: random.Random, plan: sc.ScenarioSpec) -> None:
        super().__init__(ctx.spec, ctx.lay, ctx.pt, ctx.extra["cls"], rng, plan)
        self.ctx = ctx
        self.csf_n: int | None = None

    def _name_width(self, *_unused: str) -> float:
        return self.ctx.extra.get("name_w", self.ctx.conf.name_w)

    def eligible(self, key: str) -> bool:
        a = CATALOGUE[key]
        pt = self.pt
        if a.sex_only and a.sex_only != pt.sex_code:
            return False
        if pt.is_paediatric and not set(a.panels) & (set(cfg.PAEDIATRIC_PANELS) | {"CSF", "URINE_SPOT"}):
            return False
        return not (key == "egfr" and (pt.input_age is None or pt.input_sex is None))

    # -- numeric rows, with optional range-absent printing (G01, G02: 5% of rows, adults with known age)
    def primary(self, key: str, title: str) -> PrintedRow | None:
        a = CATALOGUE[key]
        spec, lay, pt, rng = self.spec, self.lay, self.pt, self.rng
        unit = self._unit(key)
        tgt = self.targets.get(key)
        state, direction = (tgt.state, tgt.direction) if tgt else ("normal", None)
        dec = units.decimals_for(key, unit)
        absent = (self.ctx.conf.absent_p > 0 and pt.input_age is not None and pt.input_age >= 18 and bool(a.refs)
                  and key not in COUNT_KEYS and key != "egfr" and rng.random() < self.ctx.conf.absent_p)
        rb = s = None
        if absent:
            s = sc.sample_value(key, state, rng, printed=None, unit=unit, direction=direction, sex_code=pt.sex_code,
                                range_absent=True)
            if s is not None:
                rb = tpl.RangeBundle(None, PrintedRange("none"), [], [], ["range_absent"])
        if s is None:
            rb = tpl._make_range(key, a, spec, lay, pt, unit, rng, False)
            if rb is None:
                return None
            for _ in range(8):
                s = sc.sample_value(key, state, rng, printed=rb.own, unit=unit, direction=direction,
                                    sex_code=pt.sex_code, range_absent=False)
                if s is None or not tpl._near_bound(float(s.value), rb.bounds, dec):
                    break
                s = None
            if s is None:
                return None
        qualifier = None
        dp = s.value
        if a.can_qualify and state in ("mild", "severe") and s.direction and rng.random() < 0.5:
            qualifier = ">" if s.direction == "high" else "<"
        if key == "troponin_i" and state == "normal" and rng.random() < 0.4:
            qualifier, dp = "<", Decimal("0.01")
        self.vals[key] = s.canonical
        return self._finish_row(key, title, unit, dp, rb, state, qualifier)

    # -- qualitative, titre, range_result and info rows (never scenario targets)
    def nonnum(self, key: str, title: str) -> PrintedRow:
        a = CATALOGUE[key]
        rng = self.rng
        vt = a.value_type
        abn = self.cls != "normal" and rng.random() < 0.2
        unit = ""
        value: float | None = None
        pr = PrintedRange()
        lines: list[str] = []
        feats = ["qualitative_result"] if vt == "qualitative" else []
        cutoff: int | None = None
        if vt == "qualitative":
            if key == "urine_urobilinogen":
                tv = "Normal"
                lines = ["Normal"]
            elif key.startswith("urine_"):
                absent_word = key in ("urine_casts", "urine_bacteria")
                lines = ["Absent" if absent_word else self.ctx.extra["neg_word"]]
                if not abn:
                    tv = lines[0]
                elif absent_word:
                    tv = "Present"
                elif key in ("urine_nitrite", "urine_leukocyte_esterase"):
                    tv = "Positive"
                else:
                    tv = rng.choice(("Trace", "1+", "2+", "3+"))
            else:
                lines = [a.normal_texts[0]]
                tv = lines[0] if not abn else ("Reactive" if lines[0] == "Non-Reactive" else "Positive")
            pr = PrintedRange("none", text=lines[0])
            vtxt = tv
        elif vt == "titre":
            if "widal_cutoff" not in self.ctx.extra:
                self.ctx.extra["widal_cutoff"] = None
            cutoff = self.ctx.extra["widal_cutoff"]
            if cutoff is None:
                n = rng.choice((320, 640)) if abn else rng.choice((20, 40))
            else:
                n = rng.choice((160, 320)) if abn else rng.choice((20, 40))
                lines = [f"Significant titre: >= 1:{cutoff}"]
                pr = PrintedRange("cutoff", low=float(cutoff), text=lines[0])
            vtxt = tv = f"1:{n}"
            feats.append("titre")
        elif vt == "range_result":
            unit = a.unit
            r = a.consensus.unisex
            lo, hi = int(r.low), int(r.high)
            lines = [f"{lo} - {hi}"]
            pr = PrintedRange("two_sided", float(lo), float(hi), text=lines[0])
            if key == "urine_rbc" and not abn and rng.random() < 0.4:
                tv = "Nil"
            elif abn:
                a0 = hi + rng.randint(1, 6)
                tv = f"{a0}-{a0 + rng.randint(1, 3)}"
            else:
                a0 = rng.randint(0, max(0, hi - 2))
                tv = f"{a0}-{min(hi, a0 + rng.randint(1, 2))}"
            vtxt = tv
        else:  # info
            tv = self._info_text(key)
            if key in ("csf_neutrophils_pct", "csf_lymphocytes_pct"):
                unit = "%"
                value = float(tv)
            vtxt = tv
        row = PrintedRow(
            key=key, name=tpl._row_name(self.spec, self.lay, a, key, self._name_width()), section=title,
            value_type=vt, value=value, value_text=vtxt, text_value=tv, unit=tpl.pretty_unit(unit),
            unit_placement="cell" if unit else "none", unit_true=tpl.pretty_unit(unit), printed_range=pr,
            range_lines=list(lines), state="mild" if abn else "normal", features=feats,
        )
        row.titre_cutoff = cutoff if vt == "titre" else None
        return row

    def _info_text(self, key: str) -> str:
        rng = self.rng
        if key == "urine_colour":
            return rng.choice(("Yellow", "Straw", "Amber"))
        if key == "urine_appearance":
            return rng.choice(("Clear", "Clear", "Hazy"))
        if key == "urine_crystals":
            return "Absent"
        if key == "csf_appearance":
            return rng.choice(("Clear", "Clear", "Hazy"))
        if key == "blood_group_abo":
            return rng.choice(("A", "B", "AB", "O"))
        if key == "blood_group_rh":
            return rng.choice(("Positive", "Positive", "Positive", "Negative"))
        wbc = self.vals.get("csf_wbc", 0.0)
        if key == "csf_neutrophils_pct":
            self.csf_n = rng.randint(60, 85) if wbc > 5 else rng.randint(2, 15)
            return str(self.csf_n)
        return str(100 - (self.csf_n if self.csf_n is not None else 10))

    def run(self) -> list[PrintedRow]:
        rows: list[PrintedRow] = []
        for title, keys in self.spec.panels:
            for key in keys:
                if not self.eligible(key):
                    continue
                if key in ("neutrophils_abs", "lymphocytes_abs") and not self.spec.abs_counts:
                    continue
                a = CATALOGUE[key]
                if a.value_type != "numeric":
                    r = self.nonnum(key, title)
                elif key in tpl.DERIVED:
                    r = self.derived(key, title)
                else:
                    r = self.primary(key, title)
                if r is None:
                    continue
                if key == "pt" and self.vals.get("inr", 0) > 2.4:
                    continue
                rows.append(r)
        return rows


# ---------------------------------------------------------------- post-processing per family

_GLUC = ("glucose_fasting", "glucose_pp", "glucose_random", "glucose_unspecified")


def _specimen_text(key: str) -> str:
    a = CATALOGUE[key]
    if "CBC" in a.panels or key == "hba1c":
        return "Whole Blood (EDTA)"
    if key in _GLUC:
        return "Plasma (Fluoride)"
    if "COAG" in a.panels:
        return "Plasma (Citrate)"
    return "Serum"


def _post(ctx: Ctx, rows: list[PrintedRow], rng: random.Random) -> list[PrintedRow]:
    fid, pt, ex = ctx.fid, ctx.pt, ctx.extra
    tag = {"G01": "plain_table", "G02": "colon_line", "G03": "flag_first", "G04": "form_style", "G05": "two_per_line",
           "G07": "cumulative", "G08": "mixed_specimen", "G09": "csf_report", "G10": "serology_report"}.get(fid)
    for r in rows:
        if tag:
            r.features.append(tag)
        sp = CATALOGUE[r.key].specimen
        if sp == "urine":
            r.features.append("urine_section")
            if ex.get("urine_only"):
                r.features.append("urine_only")
        elif sp == "csf":
            r.features.append("csf")
        if r.value_type == "titre" or (CATALOGUE[r.key].panels and CATALOGUE[r.key].panels[0] == "SEROLOGY"):
            r.features.append("serology")
    if fid == "G02":
        for r in rows:  # word flags straight from the truth (never contradict the value)
            ft = base.row_truth(r, pt).flag_true
            r.printed_flag = {"HIGH": "HIGH", "LOW": "LOW", "CRITICAL_HIGH": "CRITICAL HIGH",
                              "CRITICAL_LOW": "CRITICAL LOW"}.get(ft, "")
            if r.unit:
                r.unit_placement = "inline"
    elif fid == "G03":
        if ex["star"]:
            for r in rows:
                if r.printed_flag in ("H", "L") and rng.random() < 0.5:
                    r.printed_flag = "*"
    elif fid == "G04":
        for r in rows:
            if r.unit:
                r.unit_placement = "inline"
            if r.value_type == "numeric":
                r.method = tpl.method_for(r.key, 0)
                r.features.append("method_line")
            if ex["interp"] and r.key in tpl.INTERPRETIVE:
                r.features.append("interpretive_table")
    elif fid == "G06":
        rows = _post_g06(ctx, rows, rng)
    elif fid == "G07":
        rows = _post_g07(ctx, rows, rng)
    elif fid == "G08":
        for r in rows:
            if r.key in _URINE_SUB:
                ctx.sub[id(r)] = _URINE_SUB[r.key]
            elif CATALOGUE[r.key].specimen == "urine" and r.section == "URINE ROUTINE EXAMINATION":
                ctx.sub[id(r)] = "Chemical Examination"
    elif fid == "G09":
        for r in rows:
            if r.key in _CSF_SUB:
                ctx.sub[id(r)] = _CSF_SUB[r.key]
    elif fid == "G10" and ex["index"]:
        for r in rows:
            if r.key in ("hiv_screen", "hbsag", "anti_hcv"):
                pos = r.state == "mild"
                v = rng.uniform(5.0, 80.0) if pos else rng.uniform(0.05, 0.95)
                ctx.idx[id(r)] = f"S/Co {v:.2f}"
                r.features.append("index_line")
    return rows


def _post_g06(ctx: Ctx, rows: list[PrintedRow], rng: random.Random) -> list[PrintedRow]:
    """Units in column headers or inside names; 15% of count rows have no unit, no range and no header unit."""
    for r in rows:
        if r.key in COUNT_KEYS and r.unit and r.value_type == "numeric" and rng.random() < 0.15:
            r.unit = ""
            r.unit_placement = "none"
            r.printed_range = PrintedRange()
            r.range_lines = []
            r.features = [f for f in r.features if f not in ("sex_labelled_range", "age_labelled_range")]
            r.features.append("unitless_count")
            ctx.sub[id(r)] = "N|"
    for r in rows:
        if id(r) in ctx.sub:
            continue
        if r.unit.startswith("10^"):
            r.unit, r.unit_true = "x" + r.unit, "x" + r.unit_true
    tally: dict[str, int] = {}
    for r in rows:
        if id(r) not in ctx.sub and r.unit:
            tally[r.unit] = tally.get(r.unit, 0) + 1
    for r in rows:
        if id(r) in ctx.sub:
            continue
        if r.unit and tally[r.unit] >= 2:
            r.unit_placement = "header"
            ctx.sub[id(r)] = f"H|{r.unit}"
            r.features.append("header_unit")
        elif r.unit:
            r.unit_placement = "inline"
            r.name = f"{r.name} ({r.unit})"
            ctx.sub[id(r)] = "I|"
            r.features.append("inline_unit")
        else:
            ctx.sub[id(r)] = "I|"
    sec_order: list[str] = []
    for r in rows:
        if r.section not in sec_order:
            sec_order.append(r.section)
    first_seen: dict[str, int] = {}
    for r in rows:
        first_seen.setdefault(ctx.sub[id(r)], len(first_seen))

    def rank(r: PrintedRow) -> tuple[int, int, int]:
        s = ctx.sub[id(r)]
        return sec_order.index(r.section), 0 if s[0] == "H" else 1 if s == "I|" else 2, first_seen[s]

    return sorted(rows, key=rank)


def _post_g07(ctx: Ctx, rows: list[PrintedRow], rng: random.Random) -> list[PrintedRow]:
    dates = ctx.extra["dates"]
    for r in rows:
        r.previous = []
        for d in dates:
            f = 1.0 + rng.uniform(-0.12, 0.12)
            dec = units.decimals_for(r.key, r.unit)
            dp = sc.round_half_up(max(r.value * f, 10.0 ** -dec), dec)
            txt = tpl.group_digits(format(dp, "f"), "western")
            r.previous.append({"date": d, "value_text": txt, "value": float(txt.replace(",", ""))})
        if rng.random() < 0.05:
            r.unlabelled_multivalue = True
            r.section = "ADDITIONAL RESULTS"
            r.features.append("unlabelled_multivalue")
    order: list[str] = []
    for r in rows:
        if r.section not in order and r.section != "ADDITIONAL RESULTS":
            order.append(r.section)
    order.append("ADDITIONAL RESULTS")
    return sorted(rows, key=lambda r: order.index(r.section))


# ---------------------------------------------------------------- drawing: header and footer

def _hdr_height(ctx: Ctx, first: bool) -> float:
    return 56.0 if not first else {"A": 124.0, "B": 144.0, "C": 112.0, "D": 142.0, "E": 112.0}[ctx.conf.header]


def _pt_lines(doc: DocBuilder, ctx: Ctx, pl: dict, x: float, y: float, keys: tuple[str, ...], size: float = 8.5,
              dy: float = 12.5, label_sep: bool = False) -> float:
    for k in keys:
        if k in pl:
            text, kind = pl[k]
            if label_sep:
                text = text.replace(": ", " : ", 1)
            doc.put(x, y, text, ctx.conf.font, size, decoy=kind)
            y += dy
    return y


def _draw_header(doc: DocBuilder, ctx: Ctx, page_no: int) -> None:
    c, lay = ctx.conf, ctx.lay
    ident = lay.ident
    W = doc.size[0]
    mx = doc.margin_x
    rx = W - mx
    y = doc.top
    f, fb = c.font, c.bold
    pl = base.patient_lines(ctx.pt, ident, "in")
    if page_no > 1:
        base.draw_logo(doc, mx, y, ident, 10)
        doc.put(mx + 28, y + 2, ident.lab_name, fb, 10)
        doc.put(rx, y + 2, pl["pid"][0], f, 8, align="r", decoy="header_id")
        doc.put(mx + 28, y + 16, pl["name"][0], f, 8)
        doc.put(rx, y + 16, pl["labno"][0], f, 8, align="r", decoy="header_id")
        doc.line(mx, y + 36, rx, y + 36, 0.6)
        doc.y = doc.top + _hdr_height(ctx, False)
        return
    st = c.header
    dates = (f"Collected: {lay.collected}", f"Reported: {lay.reported}")
    if st == "A":
        base.draw_logo(doc, mx, y, ident, 17)
        doc.put(mx + 44, y + 2, ident.lab_name, fb, 15)
        doc.put(mx + 44, y + 21, ident.address, f, 8, decoy="address")
        doc.put(rx, y + 2, f"Tel: {ident.phone}", f, 8, align="r", decoy="phone")
        doc.put(rx, y + 13, f"Toll Free: {ident.tollfree}", f, 8, align="r", decoy="tollfree")
        doc.line(mx, y + 42, rx, y + 42, 1.0)
        _pt_lines(doc, ctx, pl, mx + 4, y + 50, ("name", "agesex", "ref"))
        yy = _pt_lines(doc, ctx, pl, W / 2 + 10, y + 50, ("pid", "labno", "sample"))
        doc.put(W / 2 + 10, yy, dates[0], f, 8.5, decoy="date")
        doc.put(W / 2 + 150, yy, dates[1], f, 8.5, decoy="date")
    elif st == "B":  # plain monospace text block
        doc.put(mx, y, ident.lab_name.upper(), fb, 12)
        doc.put(mx, y + 15, ident.address, f, 8, decoy="address")
        doc.put(mx, y + 26, f"Phone: {ident.phone}", f, 8, decoy="phone")
        doc.put(mx, y + 37, "-" * 78, f, 8)
        yy = _pt_lines(doc, ctx, pl, mx, y + 50, ("name", "agesex", "pid", "labno", "ref"), 8.5, 11, True)
        doc.put(mx, yy, f"Collected: {lay.collected}   Reported: {lay.reported}", f, 8.5, decoy="date")
        doc.put(mx, yy + 11, "-" * 78, f, 8)
    elif st == "C":  # filled band, three patient columns
        doc.rect(mx, y, rx - mx, 26, fill=0.82)
        doc.put(mx + 8, y + 5, ident.lab_name, fb, 14)
        doc.put(rx - 6, y + 4, f"Tel: {ident.phone}", f, 7.5, align="r", decoy="phone")
        doc.put(rx - 6, y + 14, ident.address_city, f, 7.5, align="r", decoy="address")
        cw = (rx - mx) / 3
        _pt_lines(doc, ctx, pl, mx + 4, y + 36, ("name", "agesex"))
        _pt_lines(doc, ctx, pl, mx + cw + 4, y + 36, ("pid", "labno"))
        _pt_lines(doc, ctx, pl, mx + 2 * cw + 4, y + 36, ("sample", "ref"))
        doc.put(mx + 4, y + 66, dates[0], f, 8.5, decoy="date")
        doc.put(mx + cw + 4, y + 66, dates[1], f, 8.5, decoy="date")
        doc.line(mx, y + 82, rx, y + 82, 0.8)
    elif st == "D":  # centred title, boxed patient block
        base.draw_logo(doc, (W - 34) / 2, y, ident, 17)
        doc.put(W / 2, y + 38, ident.lab_name.upper(), fb, 13, align="c")
        doc.put(W / 2, y + 54, ident.address, f, 7.5, align="c", decoy="address")
        doc.put(W / 2, y + 64, f"Phone: {ident.phone}  |  {ident.email}", f, 7.5, align="c", decoy="phone")
        doc.rect(mx, y + 78, rx - mx, 58, width=0.7)
        _pt_lines(doc, ctx, pl, mx + 6, y + 82, ("name", "agesex", "ref"), 8.5, 12, True)
        yy = _pt_lines(doc, ctx, pl, W / 2 + 6, y + 82, ("pid", "labno"), 8.5, 12, True)
        doc.put(W / 2 + 6, yy, dates[0], f, 8.5, decoy="date")
        doc.put(W / 2 + 6, yy + 12, dates[1], f, 8.5, decoy="date")
    else:  # E: lab block on the left, patient box on the right
        base.draw_logo(doc, mx, y, ident, 15)
        doc.put(mx + 38, y + 2, ident.lab_name, fb, 13)
        doc.put(mx + 38, y + 19, ident.address, f, 7.5, decoy="address")
        doc.put(mx + 38, y + 29, f"Tel: {ident.phone}  |  Toll Free: {ident.tollfree}", f, 7.5, decoy="phone")
        bx = W - mx - 250
        doc.rect(bx, y, 250, 84, width=0.7)
        _pt_lines(doc, ctx, pl, bx + 6, y + 4, ("name", "agesex", "pid", "labno", "sample"), 8, 11)
        doc.put(bx + 6, y + 60, dates[0], f, 8, decoy="date")
        doc.put(bx + 6, y + 71, dates[1], f, 8, decoy="date")
        doc.line(mx, y + 94, rx, y + 94, 0.8)
    doc.y = doc.top + _hdr_height(ctx, True)


def _footer(doc: DocBuilder, pg, total: int, ctx: Ctx) -> None:
    base.draw_standard_footer(doc, pg, total, ctx.lay.ident, font=ctx.conf.font, size=7.0,
                              right_edge=doc.size[0] - doc.margin_x)
    if ctx.extra.get("urine_only"):  # trailing phone line (a bare phone number must not become a result)
        doc.put(doc.margin_x, doc.size[1] - 24, f"For queries Ph: {ctx.lay.ident.phone}", ctx.conf.font, 7.0,
                decoy="phone", page=pg)


# ---------------------------------------------------------------- drawing: sections, headings, rows

def _band(doc: DocBuilder, ctx: Ctx, title: str, cont: bool) -> None:
    c = ctx.conf
    mx = doc.margin_x
    rx = doc.size[0] - mx
    t = f"{title} (contd.)" if cont else title
    if c.band == "fill":
        doc.rect(mx, doc.y, rx - mx, 15, fill=0.9)
        doc.put(mx + 4, doc.y + 3, t, c.bold, c.size + 0.5)
        doc.y += 18
    elif c.band == "rule":
        doc.put(mx, doc.y + 1, t.upper() if c.kind != "lines" else f"== {t.upper()} ==", c.bold, c.size + 1)
        doc.line(mx, doc.y + 15, rx, doc.y + 15, 0.8)
        doc.y += 19
    elif c.band == "centre":
        doc.put(doc.size[0] / 2, doc.y + 1, f"- {t} -", c.bold, c.size + 1, align="c")
        doc.y += 17
    else:
        doc.put(mx, doc.y, t, c.bold, c.size + 1)
        doc.y += 15


def _head_line(doc: DocBuilder, ctx: Ctx, cols: list[tuple[float, str, str]]) -> None:
    c = ctx.conf
    for x, text, align in cols:
        doc.put(x, doc.y, text, c.bold, c.size - 0.5, align=align)
    doc.y += 12
    doc.line(doc.margin_x, doc.y - 1, doc.size[0] - doc.margin_x, doc.y - 1, 0.6)
    doc.y += 3


def _g07_cols(ctx: Ctx) -> dict[str, float]:
    n = len(ctx.extra["dates"])
    return {"name": 40.0, "cur": 360.0, "d1": 445.0, "d2": 530.0, "unit": 440.0 + 85.0 * n + 10,
            "ref": 440.0 + 85.0 * n + 85}


def _half(ctx: Ctx) -> tuple[float, float]:
    W = ctx.page[0]
    mx = ctx.conf.margin
    w2 = (W - 2 * mx - 14) / 2
    return mx, w2


def _heads(doc: DocBuilder, ctx: Ctx, title: str) -> None:
    fid = ctx.fid
    if fid == "G01":
        _head_line(doc, ctx, [(40, "Test", "l"), (300, "Result", "l"), (372, "Unit", "l"), (445, "Reference", "l")])
    elif fid == "G03":
        cols = [(40, "Flag", "l"), (70, "Test", "l"), (320, "Result", "r"), (330, "Unit", "l"), (395, "Range", "l")]
        if ctx.extra["method_col"]:
            cols.append((500, "Method", "l"))
        _head_line(doc, ctx, cols)
    elif fid == "G05":
        mx, w2 = _half(ctx)
        cols = []
        for off in (mx, mx + w2 + 14):
            cols += [(off, "Test", "l"), (off + 0.58 * w2, "Result", "r"), (off + 0.61 * w2, "Unit", "l"),
                     (off + 0.78 * w2, "Reference", "l")]
        _head_line(doc, ctx, cols)
    elif fid == "G07":
        k = _g07_cols(ctx)
        if title == "ADDITIONAL RESULTS":
            _head_line(doc, ctx, [(k["name"], "Test", "l"), (k["unit"], "Unit", "l"), (k["ref"], "Reference", "l")])
            return
        cols = [(k["name"], "Test", "l"), (k["cur"], "Current", "r")]
        for i, d in enumerate(ctx.extra["dates"]):
            x = k["d1"] if i == 0 else k["d2"]
            cols.append((x, d, "r"))
        cols += [(k["unit"], "Unit", "l"), (k["ref"], "Reference", "l")]
        for x, text, align in cols:
            doc.put(x, doc.y, text, ctx.conf.bold, ctx.conf.size - 0.5, align=align,
                    decoy="date" if text[:1].isdigit() else None)
        doc.y += 12
        doc.line(doc.margin_x, doc.y - 1, doc.size[0] - doc.margin_x, doc.y - 1, 0.6)
        doc.y += 3
    elif fid == "G08":
        _head_line(doc, ctx, [(40, "Investigation", "l"), (300, "Observed Value", "l"), (380, "Units", "l"),
                              (458, "Reference Interval", "l")])
    elif fid == "G09":
        _head_line(doc, ctx, [(40, "Test", "l"), (275, "Result", "l"), (340, "Unit", "l"), (410, "Reference Interval", "l"),
                              (525, "Flag", "l")])
    elif fid == "G10":
        _head_line(doc, ctx, [(40, "Test", "l"), (250, "Result", "l"), (325, "Unit", "l"),
                              (395, "Reference / Cut-off", "l")])


def _sub_h(ctx: Ctx, text: str) -> float:
    return 14.0


def _draw_sub(doc: DocBuilder, ctx: Ctx, text: str) -> None:
    c = ctx.conf
    if ctx.fid == "G06":
        kind, _, unit = text.partition("|")
        res = f"Result ({unit})" if kind == "H" else "Result"
        cols = [(40, "Test", "l"), (330, res, "r")]
        if kind != "N":
            cols.append((400, "Reference", "l"))
        for x, t, al in cols:
            doc.put(x, doc.y, t, c.bold, c.size - 0.5, align=al)
        doc.line(doc.margin_x, doc.y + 11, doc.size[0] - doc.margin_x, doc.y + 11, 0.5)
        doc.y += 14
        return
    doc.put(doc.margin_x + 6, doc.y, text, FONT_ITALIC if c.font != FONT_MONO else c.font, c.size - 0.5)
    doc.y += 14


def _interp_h(ctx: Ctx, r: PrintedRow) -> float:
    if ctx.fid == "G04" and "interpretive_table" in r.features:
        return 3 + 13 + 11 * len(tpl.INTERPRETIVE[r.key][1]) + 6
    return 0.0


def _draw_interp(doc: DocBuilder, ctx: Ctx, key: str) -> None:
    title, cats = tpl.INTERPRETIVE[key]
    h = 13 + 11 * len(cats) + 6
    mx = doc.margin_x
    doc.y += 3
    doc.rect(mx + 20, doc.y, 300, h - 4, width=0.4, gray=0.4)
    doc.put(mx + 26, doc.y + 2, f"Interpretation: {title}", FONT_ITALIC, 7.5, decoy="interpretive")
    yy = doc.y + 14
    for cat, rngt in cats:
        doc.put(mx + 30, yy, cat, ctx.conf.font, 7.5)
        doc.put(mx + 160, yy, rngt, ctx.conf.font, 7.5, decoy="interpretive")
        yy += 11
    doc.y += h


def _line_text(r: PrintedRow) -> str:
    unit = f" {r.unit}" if r.unit else ""
    rng_ = " ".join(r.range_lines)
    ref = f" (Ref: {rng_})" if rng_ else ""
    flag = f" {r.printed_flag}" if r.printed_flag else ""
    return f"{r.name}: {_val(r)}{unit}{ref}{flag}"


def _cells(ctx: Ctx, r: PrintedRow, x0: float | None = None, w2: float = 0.0) -> list[Cell]:
    fid, c = ctx.fid, ctx.conf
    f, fb, sz = c.font, c.bold, c.size
    val = _val(r)
    if fid == "G01":
        return [simple_cell(40, r.name, f, sz), simple_cell(300, val, f, sz), simple_cell(372, r.unit, f, sz),
                simple_cell(445, r.range_lines, f, sz - 0.5)]
    if fid == "G02":
        return [Cell(40, [(_line_text(r), fb if r.printed_flag else f, sz)])]
    if fid == "G03":
        cells = [simple_cell(40, r.printed_flag, fb, sz), simple_cell(70, r.name, f, sz),
                 simple_cell(320, val, fb if r.printed_flag else f, sz, "r"), simple_cell(330, r.unit, f, sz),
                 simple_cell(395, r.range_lines, f, sz - 0.5)]
        if ctx.extra["method_col"]:
            cells.append(simple_cell(500, r.method, f, 7))
        return cells
    if fid == "G04":
        unit = f" {r.unit}" if r.unit else ""
        mx = c.margin
        lines = [(f"Test Name : {r.name}", fb, sz), (f"Specimen : {_specimen_text(r.key)}", f, sz),
                 (f"Result : {val}{unit}", fb, sz),
                 (f"Biological Reference Interval : {' '.join(r.range_lines) or '-'}", f, sz)]
        if r.method:
            lines.append((f"Method : {r.method}", f, sz))
        return [Cell(mx, lines)]
    if fid == "G05":
        res = f"{val} {r.printed_flag}" if r.printed_flag else val
        usz = sz - 0.5
        while usz > 5.0 and base.text_width(r.unit, f, usz) > 0.17 * w2 - 2:
            usz -= 0.5
        return [simple_cell(x0, r.name, f, sz), simple_cell(x0 + 0.58 * w2, res, fb if r.printed_flag else f, sz, "r"),
                simple_cell(x0 + 0.61 * w2, r.unit, f, usz), simple_cell(x0 + 0.78 * w2, r.range_lines, f, sz - 0.5)]
    if fid == "G06":
        cells = [simple_cell(40, r.name, f, sz), simple_cell(330, val, f, sz, "r")]
        if r.range_lines:
            cells.append(simple_cell(400, r.range_lines, f, sz - 0.5))
        return cells
    if fid == "G07":
        k = _g07_cols(ctx)
        cells = [simple_cell(k["name"], r.name, f, sz), simple_cell(k["cur"], val, fb, sz, "r")]
        for i, p in enumerate(r.previous):
            cells.append(simple_cell(k["d1"] if i == 0 else k["d2"], p["value_text"], f, sz, "r"))
        cells += [simple_cell(k["unit"], r.unit, f, sz), simple_cell(k["ref"], r.range_lines, f, sz - 0.5)]
        return cells
    if fid == "G08":
        return [simple_cell(40, r.name, f, sz), simple_cell(300, val, f, sz), simple_cell(380, r.unit, f, sz),
                simple_cell(458, r.range_lines, f, sz - 0.5)]
    if fid == "G09":
        return [simple_cell(40, r.name, f, sz), simple_cell(275, val, f, sz), simple_cell(340, r.unit, f, sz),
                simple_cell(410, r.range_lines, f, sz - 0.5), simple_cell(525, r.printed_flag, fb, sz)]
    return [simple_cell(40, r.name, f, sz), simple_cell(250, val, fb, sz), simple_cell(325, r.unit, f, sz),
            simple_cell(395, r.range_lines, f, sz - 0.5)]  # G10


def _items(ctx: Ctx, srows: list[PrintedRow]) -> list[tuple]:
    items: list[tuple] = []
    last_sub = None
    pair: list[PrintedRow] = []
    for r in srows:
        s = ctx.sub.get(id(r))
        if s != last_sub:
            if pair:
                items.append(("pair", pair[0], pair[1] if len(pair) > 1 else None))
                pair = []
            if s is not None:
                items.append(("sub", s))
            last_sub = s
        if ctx.fid == "G05":
            pair.append(r)
            if len(pair) == 2:
                items.append(("pair", pair[0], pair[1]))
                pair = []
        else:
            items.append(("row", r))
    if pair:
        items.append(("pair", pair[0], None))
    return items


def _draw_section(doc: DocBuilder, ctx: Ctx, title: str, srows: list[PrintedRow], pad: float) -> None:
    items = _items(ctx, srows)
    first = next(i for i in items if i[0] != "sub")
    probe = _item_h(doc, ctx, first, pad)
    doc.ensure(18 + 15 + 14 + probe)
    _band(doc, ctx, title, False)
    _heads(doc, ctx, title)
    cur_sub = None
    pending = None
    for it in items:
        if it[0] == "sub":
            pending = it[1]
            continue
        need = _item_h(doc, ctx, it, pad) + (_sub_h(ctx, pending) if pending else 0)
        if doc.y + need > doc.limit:
            doc.new_page()
            _band(doc, ctx, title, True)
            _heads(doc, ctx, title)
            if pending is None and cur_sub is not None:
                pending = cur_sub
        if pending is not None:
            _draw_sub(doc, ctx, pending)
            cur_sub, pending = pending, None
        _draw_item(doc, ctx, it, pad)


def _item_h(doc: DocBuilder, ctx: Ctx, it: tuple, pad: float) -> float:
    if it[0] == "pair":
        mx, w2 = _half(ctx)
        hs = [doc.row_height(_cells(ctx, r, mx, w2), pad) for r in it[1:] if r is not None]
        return max(hs)
    r = it[1]
    extra = _interp_h(ctx, r) if ctx.extra.get("interp") else 0.0
    return doc.row_height(_cells(ctx, r), pad) + extra + (11.0 if id(r) in ctx.idx else 0.0)


def _draw_item(doc: DocBuilder, ctx: Ctx, it: tuple, pad: float) -> None:
    if it[0] == "pair":
        mx, w2 = _half(ctx)
        top = doc.y
        h = _item_h(doc, ctx, it, pad)
        for r, x0 in ((it[1], mx), (it[2], mx + w2 + 14)):
            if r is None:
                continue
            doc.y = top
            doc.row(r, _cells(ctx, r, x0, w2), pad)
        doc.y = top + h
        return
    r = it[1]
    doc.row(r, _cells(ctx, r), pad)
    if id(r) in ctx.idx:
        doc.put(doc.margin_x + 300, doc.y - 1, ctx.idx[id(r)], FONT_ITALIC, 7, decoy="index_line")
        doc.y += 11
    if ctx.fid == "G04" and ctx.extra.get("interp") and "interpretive_table" in r.features:
        _draw_interp(doc, ctx, r.key)


def _layout(ctx: Ctx, rows: list[PrintedRow], pad: float) -> DocBuilder:
    def on_page(doc: DocBuilder, n: int, is_break: bool) -> None:
        _draw_header(doc, ctx, n)

    doc = DocBuilder(ctx.page, margin_x=ctx.conf.margin, top=34.0, bottom=84.0, on_new_page=on_page)
    order: list[str] = []
    for r in rows:
        if r.section not in order:
            order.append(r.section)
    for title in order:
        _draw_section(doc, ctx, title, [r for r in rows if r.section == title], pad)
        doc.y += 8
    doc.finish(lambda d, pg, total: _footer(d, pg, total, ctx))
    return doc


# ---------------------------------------------------------------- pagination fit (section 3.1) and build

def _fit(ctx: Ctx, rows: list[PrintedRow], target: int, rng: random.Random) -> tuple[DocBuilder, list[PrintedRow]] | None:
    n_all = len(rows)
    n_min = len({id(r) for r in rows if r.state in ("mild", "severe", "critical") and r.value_type == "numeric"})
    lo_pad = {1: 3.0, 2: 5.0, 3: 9.0}[target]
    pads = [ctx.lay.start_pad + k * 2.0 for k in range(0, 16)]
    pads = [p for p in pads if p >= lo_pad - 1.5] or pads

    def pages(n: int, pad: float) -> int:
        return len(_layout(ctx, tpl._select(rows, n), pad).pages)

    for pad in pads:
        if n_all < 4 or pages(n_all, pad) < target:
            continue
        if pages(max(n_min, 4), pad) > target:
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
        n = rng.randint(n_lo, a)
        sel = tpl._select(rows, n)
        doc = _layout(ctx, sel, pad)
        if len(doc.pages) == target:
            return doc, sel
    return None


def build_report(family: str, *, split: str, index: int, patient: Patient, scenario_class: str, target_pages: int,
                 report_id: str | None = None) -> PrintedReport:
    """Build the printed report (rows, decoys, pages) for one generic family.

    Fully determined by (family, split, index, patient, scenario_class, target_pages). The value draw is repeated
    (attempt counter added to the seed) until pagination matches ``target_pages`` and the GT headline matches the
    scenario class (same contract as ``templates.build_report``).
    """
    conf = CONFS[family]
    master = cfg.master_seed(split)
    seed = lambda purpose: cfg.seed_for(master, split, index, purpose)  # noqa: E731
    rid = report_id or f"{split[0].upper()}{index + 1:03d}"
    lrng = random.Random(seed("layout"))
    locale = "US" if lrng.random() < 0.25 else "IN"
    ident = base.make_identity(random.Random(seed("names")), locale)
    collected, reported = base.fixed_dates(lrng)
    dstyle = "us" if locale == "US" else "in"
    lay = tpl.Lay(
        ident=ident, collected=base.fmt_date(collected, dstyle), reported=base.fmt_date(reported, dstyle),
        syntax=lrng.choice(conf.syntaxes), one_sided=lrng.choice(conf.one_sided), si=lrng.random() < conf.si_p,
        cumm=False, size=conf.size, unit_spell={k: lrng.randint(0, 3) for k in CATALOGUE},
        name_pick={k: lrng.randint(0, 4) for k in CATALOGUE}, footnotes=(), lab_codes={},
        start_pad=lrng.uniform(3.0, 6.0) if target_pages == 1 else lrng.uniform(5.0, 8.0) if target_pages == 2
        else lrng.uniform(9.0, 12.0),
    )
    base_extra: dict = {"cls": scenario_class, "neg_word": lrng.choice(("Nil", "Negative"))}
    page = conf.page
    if family == "G03":
        base_extra.update(star=lrng.random() < 0.5, method_col=lrng.random() < 0.5)
    elif family == "G04":
        base_extra["interp"] = lrng.random() < 0.7
    elif family == "G05":
        landscape = lrng.random() < 0.4
        page = A4_L if landscape else A4
        w2 = (page[0] - 2 * conf.margin - 14) / 2
        base_extra["name_w"] = 0.58 * w2 - 38
    elif family == "G07":
        n_prev = lrng.choice((1, 2, 2))
        base_extra["dates"] = [
            base.fmt_date(collected - datetime.timedelta(days=lrng.randint(25, 60) * (i + 1) + 10 * i), dstyle)
            for i in range(n_prev)]
    elif family == "G08":
        base_extra["urine_only"] = (scenario_class != "critical" and target_pages <= 2 and lrng.random() < 0.47)
    elif family == "G10":
        base_extra["index"] = lrng.random() < 0.6
        base_extra["widal_cutoff"] = None if lrng.random() < 0.5 else lrng.choice((80, 160))
    panels = list(_panels_for(family, base_extra))
    if conf.shuffle:
        rest = panels[1:]
        lrng.shuffle(rest)
        panels = [panels[0]] + rest
    spec = replace(SPECS[family], page=page, panels=tuple(panels),
                   method_mode="column" if base_extra.get("method_col") else None)
    for attempt in range(MAX_ATTEMPTS):
        srng = random.Random(seed("scenario") + attempt)
        vrng = random.Random(seed("values") + attempt)
        ctx = Ctx(family, conf, spec, lay, patient, page, dict(base_extra))
        cand = _GGen(ctx, vrng, sc.ScenarioSpec(scenario_class)).candidates()
        plan = sc.plan_scenario(scenario_class, srng, patient, cand)
        gen = _GGen(ctx, vrng, plan)
        rows = _post(ctx, gen.run(), vrng)
        fit = _fit(ctx, rows, target_pages, vrng)
        if fit is None:
            continue
        doc, sel = fit
        if not tpl._headline_ok(scenario_class, sel, patient):
            continue
        feats = sorted({f for r in sel for f in r.features})
        return PrintedReport(
            report_id=rid, split=split, family=family, seed=seed("plan"), page_size=page,
            lab={"name": ident.lab_name, "address": ident.address, "phone": ident.phone, "fictional": True},
            patient=patient, scenario={"class": scenario_class, "clusters": list(plan.clusters)},
            unit_system="SI" if any("si" in r.features for r in sel) else "conventional", rows=sel,
            decoys=doc.decoys, pages=doc.pages, features=feats, template_id=None,
        )
    raise RuntimeError(f"{family}: could not build {rid} (class {scenario_class}, {target_pages} pages)")


def native_pdf(report: PrintedReport, path: str) -> None:
    base.write_pdf(report.pages, report.page_size, path, title=f"{report.lab['name']} - Laboratory Report")


__all__ = ["CONFS", "SPECS", "build_report", "native_pdf"]
