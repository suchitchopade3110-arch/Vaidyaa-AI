"""Matching and unit comparators (P1 plan sections 6.6, 6.7, 11).

GT rows are built with the GT rules (``clinical.derive_row``); extracted rows are the adapter dataclass
``ExtractedRow``, which the judge-side matcher reads by duck typing. Every expectation is derived by hand from the
plan's cost and comparator rules (worked out in the comments), never from the matcher's own output.
"""

from __future__ import annotations

import random

import pytest

from tools.eval import clinical as cl
from tools.eval import match as m
from tools.eval.adapters.base import ExtractedRow
from tools.eval.catalogue import CATALOGUE
from tools.eval.units import units_equivalent

# ======================================================================== builders (shared with test_eval_metrics)


def _fmt(v: float) -> str:
    return f"{v:g}"


def gt_row(key, value=None, unit=None, lo=None, hi=None, *, name=None, text=None, value_text=None, qualifier=None,
           page=1, printed=None, unit_true=None, sex="male", age=40, true_sex=None, true_age=None, features=(),
           row_id=None, section="Section", printed_flag=""):
    """One GT row dict with the GT JSON fields; flags, criticality and ranges come from the GT rules."""
    a = CATALOGUE[key]
    unit = a.unit if unit is None else unit
    if printed is None:
        if lo is not None and hi is not None:
            printed = cl.PrintedRange("two_sided", lo, hi)
        elif hi is not None:
            printed = cl.PrintedRange("upper", None, hi)
        elif lo is not None:
            printed = cl.PrintedRange("lower", lo, None)
        else:
            printed = cl.PrintedRange()
    t = cl.derive_row(key, value=value, qualifier=qualifier, unit=unit, unit_true=unit_true, text_value=text,
                      printed_range=printed, printed_flag=printed_flag, input_sex=sex, input_age=age,
                      true_sex=true_sex, true_age=true_age)
    if value_text is None:
        value_text = text if value is None else _fmt(value)
    return {
        "row_id": row_id or f"R-{key}", "page": page, "bbox": [0, 0, 1, 1], "line_text": "", "name": name or a.display,
        "analyte_key": key, "key_optional": a.key_optional, "specimen": a.specimen, "section": section,
        "value_type": a.value_type, "value": value, "value_text": value_text, "qualifier": qualifier,
        "text_value": text, "unit": unit, "unit_placement": "cell" if unit else "none",
        "unit_true": unit if unit_true is None else unit_true,
        "range": {"kind": printed.kind, "low": printed.low, "high": printed.high, "text": "", "options": []},
        "effective_range_page": t.effective_range_page, "effective_range_true": t.effective_range_true,
        "printed_flag": printed_flag, "flag": t.flag, "flag_true": t.flag_true, "is_critical": t.is_critical,
        "critical_rule": t.critical_rule, "canonical": t.canonical or {"value": None, "unit": ""},
        "expected_needs_review": t.expected_needs_review, "ambiguity": list(t.ambiguity),
        "features": list(features), "previous": [],
    }


def ext_row(g, index=0, **over):
    """An ExtractedRow that reads ``g`` exactly as printed (the perfect extraction), with overrides."""
    rng = g["effective_range_page"] or {}
    fields = dict(
        row_index=index, kind="row", name_raw=g["name"], analyte_key=g["analyte_key"],
        value_raw=(g["qualifier"] or "") + (g["value_text"] or ""), value_num=g["value"], qualifier=g["qualifier"],
        text_value=g["text_value"], unit_raw=g["unit"], ref_low=rng.get("low"), ref_high=rng.get("high"),
        specimen=g["specimen"], flag=g["flag"], needs_review=False, origin="text", source_page=g["page"],
        source_text=g["line_text"],
    )
    fields.update(over)
    return ExtractedRow(**fields)


def perfect(gt_rows):
    return [ext_row(g, i) for i, g in enumerate(gt_rows)]


def unread(text, index=0, needs_review=True):
    return ExtractedRow(row_index=index, kind="unread_line", source_text=text, needs_review=needs_review,
                        origin="unread_line", flag="UNKNOWN")


# 20 normal adult-male rows with printed two-sided ranges (each flag NORMAL by value vs printed range)
TWENTY = (
    ("hemoglobin", 14.2, "g/dL", 13.0, 17.0), ("wbc", 7.2, "x10³/µL", 4.0, 11.0),
    ("platelets", 250, "10³/µL", 150, 450), ("rbc", 4.9, "million/µL", 4.5, 5.5), ("hematocrit", 44, "%", 40, 50),
    ("mcv", 88, "fL", 80, 100), ("mch", 29, "pg", 27, 32), ("mchc", 33, "g/dL", 32, 36),
    ("sodium", 140, "mmol/L", 135, 145), ("potassium", 4.2, "mmol/L", 3.5, 5.1),
    ("chloride", 102, "mmol/L", 98, 107), ("urea", 30, "mg/dL", 15, 40), ("creatinine", 1.0, "mg/dL", 0.7, 1.3),
    ("glucose_fasting", 90, "mg/dL", 70, 100), ("calcium", 9.5, "mg/dL", 8.5, 10.5), ("alt", 30, "U/L", 7, 56),
    ("ast", 25, "U/L", 10, 40), ("albumin", 4.2, "g/dL", 3.5, 5.0), ("tsh", 2.1, "µIU/mL", 0.4, 4.0),
    ("bilirubin_total", 0.8, "mg/dL", 0.3, 1.2),
)


def twenty(replace=None, **kw):
    """The 20-row normal report; ``replace`` maps an analyte key to the GT row that takes its place."""
    rows = []
    for i, (k, v, u, lo, hi) in enumerate(TWENTY, 1):
        g = (replace or {}).get(k) or gt_row(k, v, u, lo, hi, **kw)
        g = dict(g, row_id=f"X001-r{i:03d}")
        rows.append(g)
    return rows


# ======================================================================== MA-01


def test_ma01_unit_tokens():
    eq = [
        ("x10^3/uL", "10³/µL"), ("10³/µL", "10^9/L"), ("K/µL", "x10^3/uL"), ("thou/mm3", "K/µL"),
        ("/cumm", "/µL"), ("/µL", "cells/mm3"), ("µIU/mL", "mIU/L"), ("µg/L", "ng/mL"),
        ("gm%", "g/dL"), ("", None), ("G/L", "10^9/L"), ("IU/L", "U/L"), ("mg%", "mg/dL"),
        ("lakh/cumm", "lakhs/cumm"), ("pg/mL", "ng/L"), ("mm/hr", "mm/1st hr"), ("s", "sec"),
    ]
    for a, b in eq:
        assert units_equivalent(a, b), (a, b)
    ne = [("mEq/L", "mmol/L"), ("mIU/mL", "mIU/L"), ("g/L", "g/dL"), ("g/L", "10^9/L"), ("/µL", "10^3/µL")]
    for a, b in ne:
        assert not units_equivalent(a, b), (a, b)


# ======================================================================== MA-02 values


def test_ma02_digit_grouping_and_trailing_zeros():
    assert m.parse_number("2,50,000") == 250000.0  # Indian grouping
    assert m.parse_number("250,000") == 250000.0 and m.parse_number("1,135") == 1135.0
    assert m.parse_number("7.20") == 7.2
    assert m.parse_number("4,5") is None  # a decimal comma is ambiguous, never guessed
    assert m.parse_value_text("< 0.01") == ("<", 0.01)
    assert m.parse_value_text("2,50,000") == (None, 250000.0)
    # GT printed "2,50,000 /cumm"; an extractor that hands over the printed string still compares equal
    g = gt_row("platelets", 250000.0, "/cumm", 150000, 450000, value_text="2,50,000")
    assert m.value_ok(g, ext_row(g, value_num="2,50,000"))
    assert m.value_ok(g, ext_row(g, value_num=250000))
    k = gt_row("potassium", 7.2, "mmol/L", 3.5, 5.1)
    assert m.value_ok(k, ext_row(k, value_num=7.20))
    assert not m.value_ok(k, ext_row(k, value_num=7.21))  # 0.01 > 1e-9 + 1e-6 * 7.2
    assert not m.value_ok(k, ext_row(k, value_num=None))


def test_ma02_qualifiers_must_match():
    tsh = gt_row("tsh", 0.01, "µIU/mL", 0.4, 4.0, qualifier="<")  # CL-25 "TSH < 0.01"
    assert m.value_ok(tsh, ext_row(tsh))
    q, v = m.parse_value_text("0.01")
    assert not m.value_ok(tsh, ext_row(tsh, qualifier=q, value_num=v))  # None != "<"
    assert not m.value_ok(tsh, ext_row(tsh, qualifier="<="))  # "<" != "<="
    assert m.norm_qualifier("≤") == "<=" and m.norm_qualifier(" < ") == "<" and m.norm_qualifier("") is None
    plain = gt_row("tsh", 0.01, "µIU/mL", 0.4, 4.0)
    q, v = m.parse_value_text("< 0.01")
    assert not m.value_ok(plain, ext_row(plain, qualifier=q, value_num=v))  # "< 0.01" != 0.01


def test_ma02_canonical_pair_equivalence_and_unit_ok():
    # (2.5, lakhs/cumm) == (250000, /cumm): 2.5 x 100 = 250 x10^3/uL = 250000 x 0.001
    g = gt_row("platelets", 2.5, "lakhs/cumm", 1.5, 4.5)
    e = ext_row(g, value_num=250000.0, unit_raw="/cumm", ref_low=150000.0, ref_high=450000.0)
    chk = m.compare(g, e)
    assert (chk.value, chk.unit, chk.range) == (True, True, True) and chk.correct
    # (7.2, "") versus (7.2, x10^3/uL): same printed number, but the unit is missing -> unit_ok False
    w = gt_row("wbc", 7.2, "x10³/µL", 4.0, 11.0)
    chk = m.compare(w, ext_row(w, unit_raw=""))
    assert chk.value and not chk.unit and chk.failed == ("unit",)
    # and the reverse: the page has no unit, the extraction invents one (never an assumed unit, S6)
    amb = gt_row("wbc", 7.2, "", unit_true="x10³/µL")
    assert amb["ambiguity"] == ["AMB_UNITLESS_COUNT"]
    assert not m.unit_ok(amb, ext_row(amb, unit_raw="x10³/µL"))
    assert m.unit_ok(amb, ext_row(amb, unit_raw=""))  # both empty
    # mEq/L and mmol/L stay distinct (plan 6.7)
    k = gt_row("potassium", 4.2, "mmol/L", 3.5, 5.1)
    assert not m.unit_ok(k, ext_row(k, unit_raw="mEq/L"))
    # a wrong-by-1000 unit: 7.2 x10^3/uL is not 7.2 /uL (7.2 vs 0.0072 canonical)
    assert not m.unit_ok(w, ext_row(w, unit_raw="/µL"))


def test_ma02_range_comparator():
    k = gt_row("potassium", 4.2, "mmol/L", 3.5, 5.1)
    assert m.range_ok(k, ext_row(k))
    assert not m.range_ok(k, ext_row(k, ref_low=3.5, ref_high=5.3))
    assert not m.range_ok(k, ext_row(k, ref_low=None))  # the same sides must be present
    assert not m.range_ok(k, ext_row(k, ref_low=None, ref_high=None))
    up = gt_row("hba1c", 5.2, "%", None, 5.7)  # "< 5.7"
    assert up["effective_range_page"] == {"kind": "upper", "low": None, "high": 5.7}
    assert m.range_ok(up, ext_row(up)) and not m.range_ok(up, ext_row(up, ref_low=0.0))
    sexed = gt_row("hemoglobin", 13.0, "g/dL", sex=None, true_sex="female",
                   printed=cl.PrintedRange("sex", options=(cl.OptionRange("M", 13.5, 17.5, sex="M"),
                                                           cl.OptionRange("F", 12.0, 15.5, sex="F"))))
    assert sexed["effective_range_page"] is None  # sex unknown: no range is determinable on the page
    assert m.range_ok(sexed, ext_row(sexed, ref_low=None, ref_high=None))
    assert not m.range_ok(sexed, ext_row(sexed, ref_low=13.5, ref_high=17.5))  # a guessed sex range is wrong


# ======================================================================== MA-03 qualitative


def test_ma03_qualitative_equivalence():
    for a, b in [("Non Reactive", "Non-Reactive"), ("Non-Reactive", "NON REACTIVE"), ("++", "2+"), ("+++", "3+"),
                 ("Nil", "Negative"), ("Absent", "Nil"), ("trace", "Trace")]:
        assert m.qualitative_equal(a, b), (a, b)
    for a, b in [("Trace", "1+"), ("Nil", "Trace"), ("1+", "2+"), ("Reactive", "Non-Reactive"), ("1.020", "Nil"),
                 ("", "Nil")]:
        assert not m.qualitative_equal(a, b), (a, b)
    hiv = gt_row("hiv_screen", text="Non-Reactive")
    assert (hiv["flag"], hiv["key_optional"]) == ("NEGATIVE", True)
    # v0 gives qualitative rows no key (plan 1 fact 5): key-optional name match, unit and range not applicable
    e = ext_row(hiv, analyte_key=None, text_value="NON REACTIVE", value_raw="NON REACTIVE", unit_raw="", ref_low=None)
    assert m.compare(hiv, e).correct
    assert not m.compare(hiv, ext_row(hiv, analyte_key="hbsag")).name  # a different key is a wrong name
    assert m.compare(hiv, ext_row(hiv, text_value="Reactive", flag="POSITIVE")).failed == ("value", "flag")
    # key-optional with no key needs name Jaccard >= 0.5: {hiv,i,ii,antibody} vs {hbsag} = 0
    assert not m.name_ok(hiv, ext_row(hiv, analyte_key=None, name_raw="HBsAg"))


def test_ma03_titre_range_result_and_info():
    widal = gt_row("widal_o", text="1:160", printed=cl.PrintedRange("cutoff", 160))
    assert m.value_ok(widal, ext_row(widal, text_value="1 : 160"))
    assert not m.value_ok(widal, ext_row(widal, text_value="1:80"))
    pus = gt_row("urine_pus_cells", text="2-3", unit="/hpf", lo=0, hi=5)
    assert m.value_ok(pus, ext_row(pus, text_value="2 - 3"))
    assert not m.value_ok(pus, ext_row(pus, text_value=None, value_raw="", value_num=3.0))  # the range was lost
    nil = gt_row("urine_rbc", text="Nil", unit="/hpf", lo=0, hi=2)
    assert m.value_ok(nil, ext_row(nil, text_value="0-0")) and not m.value_ok(nil, ext_row(nil, text_value="1-2"))
    colour = gt_row("urine_colour", text="Pale Yellow")
    assert m.value_ok(colour, ext_row(colour, text_value="pale  yellow"))
    assert not m.value_ok(colour, ext_row(colour, text_value="Yellow"))


# ======================================================================== MA-04 alignment


def test_ma04_perfect_twenty_rows_invariant_to_shuffling():
    gt = twenty()
    ext = perfect(gt)
    a = m.align(gt, ext)
    assert len(a.pairs) == 20 and not a.unmatched_gt and not a.unmatched_ext
    assert all(p.rule == "K" and ext[p.e].row_index == p.g for p in a.pairs)
    assert all(m.compare(gt[p.g], ext[p.e]).correct for p in a.pairs)
    for seed in (1, 2, 3):
        shuffled = list(ext)
        random.Random(seed).shuffle(shuffled)
        b = m.align(gt, shuffled)
        assert {(p.g, shuffled[p.e].row_index) for p in b.pairs} == {(i, i) for i in range(20)}


def test_ma04_current_value_wins_and_previous_is_invented():
    k = gt_row("potassium", 6.7, "mmol/L", 3.5, 5.1)
    assert k["flag"] == "CRITICAL_HIGH" and k["is_critical"]  # Table C: K > 6.5
    prev = ext_row(k, 0, value_num=4.2, flag="NORMAL")
    cur = ext_row(k, 1)
    # both are rule K (cost 0); value cost 0.6 for 4.2 (|2.5| > 10% of 6.7) and 0 for 6.7
    a = m.align([k], [prev, cur])
    assert [(p.g, p.e, p.rule) for p in a.pairs] == [(0, 1, "K")]
    assert a.unmatched_ext == [0] and a.unmatched_gt == []


def test_ma04_glued_name_matches_by_rule_t_with_wrong_value():
    k = gt_row("potassium", 4.7, "mmol/L", 3.5, 5.1)
    glued = ext_row(k, analyte_key=None, name_raw="Potassium 4.2 5.1", value_num=5.1, value_raw="5.1")
    # tokens {potassium, 4.2, 5.1}: J = 1/3 < 0.5 (not N); {potassium} is inside them and J >= 0.25 -> T, cost 0.8
    assert m.name_rule(k, glued) == ("T", 0.8)
    # |5.1 - 4.7| = 0.4 <= 0.47 (10%) -> value cost 0.3; total 1.1 <= 1.2: eligible
    a = m.align([k], [glued])
    assert [(p.rule, round(p.cost, 6)) for p in a.pairs] == [("T", 1.1)]
    chk = m.compare(k, glued)
    assert not chk.value and not chk.name  # key None on a key-required analyte
    # with the value far off, rule T costs 0.8 + 0.6 = 1.4 > 1.2: no pair
    far = ext_row(k, analyte_key=None, name_raw="Potassium 4.2 5.1", value_num=4.2)
    a = m.align([k], [far])
    assert a.pairs == [] and a.unmatched_gt == [0] and a.unmatched_ext == [0]


# ======================================================================== MA-05 duplicate names


def test_ma05_two_glucose_rows_paired_by_value():
    fasting = gt_row("glucose_fasting", 92, "mg/dL", 70, 100, name="Glucose")
    pp = gt_row("glucose_pp", 210, "mg/dL", 70, 140, name="Glucose")
    gt = [fasting, pp]
    e210 = ext_row(pp, 0, analyte_key=None)
    e92 = ext_row(fasting, 1, analyte_key=None)
    # rule N (J = 1, cost 0); value cost 0 for the equal value, 0.6 for the other; order cost 0.005 either way
    a = m.align(gt, [e210, e92])
    assert [(p.g, p.e, p.rule) for p in a.pairs] == [(0, 1, "N"), (1, 0, "N")]
    b = m.align(gt, [e92, e210])
    assert [(p.g, p.e) for p in b.pairs] == [(0, 0), (1, 1)]


def test_ma05_exact_tie_breaks_on_gt_index_deterministically():
    fasting = gt_row("glucose_fasting", 92, "mg/dL", 70, 100, name="Glucose")
    urea = gt_row("urea", 30, "mg/dL", 15, 40)
    pp = gt_row("glucose_pp", 210, "mg/dL", 70, 140, name="Glucose")
    gt = [fasting, urea, pp]
    ext = [ext_row(urea, 0), ext_row(fasting, 1, analyte_key=None, value_num=150.0)]
    # glucose 150 vs 92 (63%) and vs 210 (28.6%): value cost 0.6 both; order |0-1|/3 = |2-1|/3: an exact tie,
    # broken by the GT index -> fasting takes it, PP stays unmatched
    runs = [m.align(gt, ext) for _ in range(3)]
    for a in runs:
        assert [(p.g, p.e) for p in a.pairs] == [(0, 1), (1, 0)]
        assert a.unmatched_gt == [2]
    assert runs[0] == runs[1] == runs[2]


# ======================================================================== MA-06 unread lines


def test_ma06_unread_line_coverage():
    k = gt_row("potassium", 6.7, "mmol/L", 3.5, 5.1)
    na = gt_row("sodium", 140, "mmol/L", 135, 145)
    lines = [unread("Potassium   6.7   mmol/L   3.5-5.1", 0), unread("Toll free 1800-000-0123", 1)]
    a = m.align([k, na], [], lines)
    assert a.covered == {0: [0]} and a.unread_used == [0]  # the toll-free line covers nothing
    assert not m.covers(lines[0], na)
    # two results on one line: one line may cover several rows
    both = m.align([k, na], [], [unread("Sodium 140 mmol/L Potassium 6.7 mmol/L")])
    assert both.covered == {0: [0], 1: [0]}
    # the name alone is not enough (value 4.2 is not 6.7), and a matched row is never "covered"
    assert m.align([k], [], [unread("Potassium 4.2 mmol/L")]).covered == {}
    assert m.align([k], [ext_row(k)], lines).covered == {}


def test_names_normalise_spelling_and_noise():
    assert m.name_tokens("Serum Haemoglobin") == m.name_tokens("Hemoglobin") == {"hemoglobin"}
    assert m.name_tokens("Total Leucocyte Count (TLC)") == {"total", "leukocyte", "tlc"}
    assert m.name_tokens("Blood") == {"blood"}  # a name made only of noise words keeps its tokens
    assert m.tokens("Platelets 2,50,000 /cumm") == ["platelets", "2,50,000", "cumm"]


def test_one_unread_line_cannot_cover_a_whole_page():
    rows = twenty()
    page = "  ".join(f"{g['name']} {g['value_text']} {g['unit']}" for g in rows)
    a = m.align(rows, [], [unread(page)])
    assert len(a.covered) <= 2  # one printed line holds at most two results (plan 6.6.7: "two per line")


def test_page_long_unread_line_leaves_nearly_every_omission_silent():
    """Hand-derived: 20 GT rows, zero extracted, one page-length unread line. A line covers at most two rows, so at
    least 18 omissions stay uncovered (M1 numerator >= 18/20) and the line cannot satisfy M2/M5 for the rest."""
    rows = twenty()
    page = "  ".join(f"{g['name']} {g['value_text']} {g['unit']}" for g in rows)
    a = m.align(rows, [], [unread(page)])
    assert len(a.unmatched_gt) == 20
    assert len(a.unmatched_gt) - len(a.covered) >= 18
    assert a.unread_used == [0]
