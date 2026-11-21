"""Metric definitions M1-M7, the simulated clinician and Wilson intervals (P1 plan 6.5, 6.8, 11: ME-01..ME-13).

Hand-built jobs scored with ``ClinicalRulesScorer`` (the GT rules) and no pipeline, so every number is
hand-countable. Each expected count is worked out from the plan's definitions in the comment beside it; none is
taken from the metrics code's own output. The base report is the 20-row normal adult report of
``test_eval_matching.TWENTY`` (every value inside its printed range).
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from tests.test_eval_matching import TWENTY, ext_row, gt_row, perfect, twenty, unread
from tools.eval import clinical as cl
from tools.eval import match as m
from tools.eval import metrics as me
from tools.eval import review_sim as rs
from tools.eval.adapters.base import ConfirmedRow, EvalJob, ExtractionResult, Reason

ORACLE = cl.ClinicalRulesScorer()


def report(rows, report_id="X001", family="G01", decoys=()):
    """A GT report dict; ``expected`` follows the GT headline rule (plan 4.2.5), as the generator writes it."""
    by_page: dict[int, list] = {}
    for g in rows:
        by_page.setdefault(g["page"], []).append((g["is_critical"], g["flag_true"]))
    return {
        "report_id": report_id, "family": family, "rows": rows, "decoys": list(decoys),
        "expected": {
            "headline": cl.headline([(g["is_critical"], g["flag_true"]) for g in rows]),
            "per_page_headline": {str(p): cl.headline(v) for p, v in sorted(by_page.items())},
            "needs_review_min_set": [g["row_id"] for g in rows if g["expected_needs_review"]],
            "critical_rows": [g["row_id"] for g in rows if g["is_critical"]],
        },
    }


def job(report_id="X001", rendering="native", pages=(1,), sex="male", age=40, split="test"):
    suffix = f":p{pages[0]}" if rendering == "photo" else ""
    return EvalJob(f"{report_id}:{rendering}{suffix}", report_id, split, rendering, ("x.pdf",), pages, sex, age)


def extraction(rows, status="ok", pre="normal", items=(), review_all=False, raw_text=""):
    return ExtractionResult(job_id="j", status=status, rows=list(rows), report_review_items=list(items),
                            review_all=review_all, pre_review_headline=pre, raw_text=raw_text)


def record(gt, ext, j=None, scorer=ORACLE, **kw):
    return me.job_record(j or job(gt["report_id"]), gt, ext, scorer, **kw)


def nd(res, metric):
    return res[metric]["num"], res[metric]["den"]


def flag(e, code="NR0-CRITICAL"):
    """Mark an extracted row needs_review (the product shows a review reason)."""
    e.needs_review = True
    e.review_reasons = [Reason(code, "check")]
    return e


K_CRIT = gt_row("potassium", 6.7, "mmol/L", 3.5, 5.1)


def test_base_report_is_twenty_normal_rows():
    gt = twenty()
    assert len(gt) == 20 and {g["flag"] for g in gt} == {"NORMAL"} and not any(g["is_critical"] for g in gt)
    assert K_CRIT["flag"] == "CRITICAL_HIGH" and K_CRIT["is_critical"]  # Table C: K > 6.5


# ======================================================================== ME-01


def test_me01_perfect_extraction_normal_clean_report():
    gt = report(twenty())
    rec = record(gt, extraction(perfect(gt["rows"])))
    res = me.cell_results(rec.counts)
    assert nd(res, "M1") == (0, 20) and res["M1"]["value"] == 0.0
    assert nd(res, "M3") == (20, 20) and res["M3"]["value"] == 1.0
    assert nd(res, "M4") == (0, 20)
    assert nd(res, "M5") == (1, 1) and rec.gt_headline == rec.confirmed_headline == "normal"
    assert nd(res, "M6") == (1, 1)
    assert nd(res, "M7") == (0, 20)
    assert nd(res, "M2") == (0, 0) and res["M2"]["value"] is None  # n/a: no GT critical
    diag = me.cell_diagnostics(rec.counts)
    assert nd(diag, "M5_oracle") == (1, 1) and nd(diag, "M5_pre") == (1, 1)
    assert diag["review_actions"] == {"accepted": 20}


# ======================================================================== ME-02 / ME-03


def _k_report():
    return report(twenty(replace={"potassium": K_CRIT}))


def test_me02_critical_read_as_previous_value_not_flagged():
    gt = _k_report()
    ext = perfect(gt["rows"])
    ext[9] = ext_row(gt["rows"][9], 9, value_num=4.2, value_raw="4.2", flag="NORMAL")  # previous column
    rec = record(gt, extraction(ext))
    res = me.cell_results(rec.counts)
    assert nd(res, "M1") == (1, 20) and res["components"]["M1"] == {"wrong": 1, "invented": 0, "omitted": 0}
    assert nd(res, "M2") == (0, 1)  # matched, NORMAL, not needs_review, not covered
    assert nd(res, "M3") == (19, 20)
    assert nd(res, "M5") == (0, 1) and (rec.gt_headline, rec.confirmed_headline) == ("critical", "normal")
    assert nd(res, "M4") == (0, 20) and nd(res, "M6") == (0, 0)
    assert nd(res, "M2_post") == (0, 1)  # the confirmed (accepted) K 4.2 is not CRITICAL


def test_me03_critical_read_wrong_but_flagged():
    gt = _k_report()
    ext = perfect(gt["rows"])
    ext[9] = flag(ext_row(gt["rows"][9], 9, value_num=4.2, value_raw="4.2", flag="NORMAL"), "NR0-WARNING")
    rec = record(gt, extraction(ext))
    res = me.cell_results(rec.counts)
    assert nd(res, "M1") == (0, 20)  # wrong but flagged: not silent
    assert nd(res, "M2") == (1, 1)  # matched and needs_review
    assert nd(res, "M3") == (19, 20)  # correctness is judged before review
    assert nd(res, "M5") == (1, 1) and rec.confirmed_headline == "critical"  # edited to K 6.7 -> CRITICAL_HIGH
    assert nd(res, "M7") == (1, 20)
    assert nd(res, "M2_post") == (1, 1)  # edited to K 6.7; Table C > 6.5 -> CRITICAL_HIGH after confirmation
    diag = me.cell_diagnostics(rec.counts)
    assert diag["review_actions"] == {"accepted": 19, "edited": 1}


# ======================================================================== ME-04


def test_me04_invented_footer_row_flagged():
    gt = report(twenty(), decoys=[{"kind": "tollfree", "text": "Toll Free: 1800-000-0607", "page": 1}])
    footer = flag(ext_row(gt["rows"][0], 20, name_raw="Toll free", analyte_key=None, value_num=15.0,
                          value_raw="15.0", unit_raw="", ref_low=None, ref_high=None, flag="UNKNOWN",
                          source_text="Toll free = 15.0"), "NR0-UNKNOWN")
    rec = record(gt, extraction(perfect(gt["rows"]) + [footer]))
    res = me.cell_results(rec.counts)
    assert nd(res, "M1") == (0, 20)  # invented but flagged
    assert nd(res, "M3") == (20, 21)  # 20 correct / (20 GT + 1 unmatched extracted)
    assert nd(res, "M6") == (0, 1)  # a flagged row: not ready to confirm
    assert nd(res, "M7") == (1, 21)
    assert nd(res, "M5") == (1, 1)  # deleted by the clinician
    assert nd(res, "M4") == (0, 20)
    diag = me.cell_diagnostics(rec.counts)
    # {toll, free, 15.0} vs {toll, free, 1800, 000, 0607}: overlap 2/3 >= 0.5
    assert diag["invented_by_decoy"] == {"tollfree": 1}
    assert diag["review_actions"] == {"accepted": 20, "deleted": 1}


def test_me04_unflagged_invention_is_silent_and_persists():
    gt = report(twenty())
    footer = ext_row(gt["rows"][0], 20, name_raw="Toll free", analyte_key=None, value_num=15.0, unit_raw="",
                     ref_low=None, ref_high=None, flag="UNKNOWN", source_text="Toll free = 15.0")
    rec = record(gt, extraction(perfect(gt["rows"]) + [footer]))
    res = me.cell_results(rec.counts)
    assert nd(res, "M1") == (1, 20) and res["components"]["M1"]["invented"] == 1
    # the clinician keeps it; the GT rules cannot score a row without a test key -> unresolved -> unknown
    assert nd(res, "M5") == (0, 1) and rec.confirmed_headline == "unknown"
    assert me.cell_diagnostics(rec.counts)["invented_by_decoy"] == {"none": 1}  # no decoy on this report


# ======================================================================== ME-05


def test_me05_omission_then_covered_by_unread_line():
    bili = gt_row("bilirubin_total", 0.8, "mg/dL", 0.3, 1.2, features=("two_per_line",))
    gt = report(twenty(replace={"bilirubin_total": bili}))
    ext = perfect(gt["rows"])[:19]  # the bilirubin row (20th) is missed
    rec = record(gt, extraction(ext))
    res = me.cell_results(rec.counts)
    assert nd(res, "M1") == (1, 20) and res["components"]["M1"]["omitted"] == 1
    assert nd(res, "M3") == (19, 20)
    assert me.cell_diagnostics(rec.counts)["omitted_by_feature"] == {"two_per_line": 1}
    line = unread("Bilirubin, Total 0.8 mg/dL 0.3 - 1.2", 19)
    rec = record(gt, extraction(ext + [line]))
    res = me.cell_results(rec.counts)
    assert nd(res, "M1") == (0, 20)  # covered: not silent
    assert nd(res, "M3") == (19, 20)  # still a miss before review
    assert nd(res, "M7") == (1, 20)  # 19 rows + 1 unread line, the line flagged
    assert nd(res, "M6") == (0, 1)  # an unread line: not ready
    diag = me.cell_diagnostics(rec.counts)
    assert diag["review_actions"] == {"accepted": 19, "added": 1} and diag["omitted_by_feature"] == {}
    assert nd(res, "M5") == (1, 1)


# ======================================================================== ME-06 / ME-07


def test_me06_count_read_in_the_wrong_unit_as_critical_but_flagged():
    gt = report(twenty())
    ext = perfect(gt["rows"])
    assert gt["rows"][1]["analyte_key"] == "wbc" and gt["rows"][1]["unit"] == "x10³/µL"
    ext[1] = flag(ext_row(gt["rows"][1], 1, unit_raw="/µL", flag="CRITICAL_LOW"))  # 7.2 /uL would be critical
    rec = record(gt, extraction(ext))
    res = me.cell_results(rec.counts)
    assert nd(res, "M4") == (1, 20)  # CRITICAL_LOW on a non-critical GT row (measured before review)
    assert nd(res, "M1") == (0, 20)
    assert nd(res, "M3") == (19, 20)
    assert me.cell_diagnostics(rec.counts)["top_error_signatures"] == [["unit+flag", 1]]
    assert nd(res, "M5") == (1, 1)


def test_me07_urine_sodium_read_as_serum_sodium_critical_flagged():
    una = gt_row("urine_sodium", 38, "mmol/L", 40, 220, name="Sodium", section="Urine Spot Electrolytes")
    assert (una["flag"], una["is_critical"], una["specimen"]) == ("LOW", False, "urine")  # S5: no serum critical
    gt = report(twenty(replace={"sodium": una}))
    assert gt["expected"]["headline"] == "abnormal"
    ext = perfect(gt["rows"])
    ext[8] = flag(ext_row(gt["rows"][8], 8, analyte_key="sodium", specimen="serum", flag="CRITICAL_LOW"))
    rec = record(gt, extraction(ext))
    res = me.cell_results(rec.counts)
    assert rec.detail["pairs"][8]["signature"] == "name+flag"  # sodium != urine_sodium; CRITICAL_LOW != LOW
    assert nd(res, "M4") == (1, 20)
    assert nd(res, "M1") == (0, 20)
    assert nd(res, "M3") == (19, 20)
    assert nd(res, "M5") == (1, 1) and rec.confirmed_headline == "abnormal"  # corrected: urine Na 38 LOW


# ======================================================================== ME-08


def test_me08_review_all_job_covers_omissions_for_m1_but_not_criticals():
    gt = _k_report()
    rec = record(gt, extraction([], status="failed", pre="unknown", review_all=True))
    res = me.cell_results(rec.counts)
    assert nd(res, "M1") == (0, 20) and res["components"]["M1"]["omitted"] == 0
    assert nd(res, "M2") == (0, 1)
    assert nd(res, "M5") == (0, 1) and rec.confirmed_headline == "unknown"
    assert nd(res, "M3") == (0, 20) and nd(res, "M7") == (0, 0) and res["M7"]["value"] is None
    # the same empty result without the "review it manually" message: every row is a silent omission
    rec = record(gt, extraction([], status="empty", pre="unknown", review_all=False))
    assert nd(me.cell_results(rec.counts), "M1") == (20, 20)


def test_crash_counts_as_silent_total_omission():
    gt = _k_report()
    rec = me.crash_record(job(), gt, "RuntimeError", ORACLE)
    res = me.cell_results(rec.counts)
    assert res["crashes"] == {"RuntimeError": 1} and res["jobs"] == 1
    assert nd(res, "M1") == (20, 20) and nd(res, "M2") == (0, 1) and nd(res, "M5") == (0, 1)
    assert me.cell_diagnostics(rec.counts)["status"] == {"crash": 1}


# ======================================================================== ME-09


def test_me09_m6_denominator_and_not_ready_conditions():
    hb = gt_row("hemoglobin", 13.0, "g/dL", sex=None, true_sex="female",
                printed=cl.PrintedRange("sex", options=(cl.OptionRange("M", 13.5, 17.5, sex="M"),
                                                        cl.OptionRange("F", 12.0, 15.5, sex="F"))))
    assert hb["ambiguity"] == ["AMB_SEX_RANGES_NO_SEX"] and hb["flag_true"] == "NORMAL"  # CL-19
    amb = report(twenty(replace={"hemoglobin": hb}, sex=None))
    assert amb["expected"]["headline"] == "normal"
    clean = report(twenty())

    excluded = record(amb, extraction(perfect(amb["rows"])), job(sex=None))
    assert nd(me.cell_results(excluded.counts), "M6") == (0, 0)  # GT-normal, but a genuinely ambiguous row

    ready = record(clean, extraction(perfect(clean["rows"])))
    unknown_label = record(clean, extraction(perfect(clean["rows"]), pre="unknown"))
    one_flagged = perfect(clean["rows"])
    flag(one_flagged[5], "NR0-WARNING")
    flagged = record(clean, extraction(one_flagged))
    item = record(clean, extraction(perfect(clean["rows"]), items=[Reason("NR0-UNREAD", "1 unread line")]))
    for rec, want in ((ready, (1, 1)), (unknown_label, (0, 1)), (flagged, (0, 1)), (item, (0, 1))):
        assert nd(me.cell_results(rec.counts), "M6") == want, rec
    empty = record(clean, extraction([], status="empty"))
    assert nd(me.cell_results(empty.counts), "M6") == (0, 1)  # nothing extracted is never ready
    total = me.sum_counts(r.counts for r in (excluded, ready, unknown_label, flagged, item))
    assert nd(me.cell_results(total), "M6") == (1, 4)


# ======================================================================== ME-10 / ME-11


def test_me10_review_burden():
    gt = report(twenty())
    ext = perfect(gt["rows"])
    for i in (2, 7, 15):
        flag(ext[i], "NR0-WARNING")
    res = me.cell_results(record(gt, extraction(ext)).counts)
    assert nd(res, "M7") == (3, 20) and res["M7"]["value"] == pytest.approx(0.15)
    assert nd(res, "M1") == (0, 20) and nd(res, "M3") == (20, 20)


def test_me11_photo_pages_are_separate_jobs():
    glu = gt_row("glucose_fasting", 36, "mg/dL", 70, 100)
    assert glu["flag"] == "CRITICAL_LOW" and glu["is_critical"]  # CL-05: 36 mg/dL
    rows = twenty(replace={"glucose_fasting": glu})
    for i, g in enumerate(rows):
        g["page"] = 1 if i < 10 else 2  # glucose (row 14) is on page 2
    gt = report(rows)
    assert gt["expected"]["per_page_headline"] == {"1": "normal", "2": "critical"}
    p1, p2 = rows[:10], rows[10:]
    e1 = extraction(perfect(p1))
    e2_rows = perfect(p2)
    flag(e2_rows[3])  # glucose, read CRITICAL_LOW and flagged
    r1 = record(gt, e1, job(rendering="photo", pages=(1,)))
    r2 = record(gt, extraction(e2_rows, pre="critical"), job(rendering="photo", pages=(2,)))
    assert (r1.gt_headline, r2.gt_headline) == ("normal", "critical")
    assert (r1.job_id, r2.job_id) == ("X001:photo:p1", "X001:photo:p2")
    res1, res2 = me.cell_results(r1.counts), me.cell_results(r2.counts)
    assert nd(res1, "M1") == (0, 10)  # page-2 rows are not omissions of the page-1 job
    assert nd(res1, "M2") == (0, 0) and nd(res2, "M2") == (1, 1)
    assert nd(res1, "M5") == (1, 1) and nd(res2, "M5") == (1, 1)
    assert nd(res1, "M6") == (1, 1) and nd(res2, "M6") == (0, 0)
    cell = me.aggregate([r2, r1])["test"]["photo"]
    res = me.cell_results(cell)
    assert nd(res, "M5") == (2, 2) and nd(res, "M3") == (20, 20) and nd(res, "M2") == (1, 1)


# ======================================================================== ME-12


def test_me12_wilson_interval():
    lo, hi = me.wilson(19, 20)
    assert (round(lo, 3), round(hi, 3)) == (0.764, 0.991)
    r = me.rate(19, 20)
    assert (r["num"], r["den"], r["value"]) == (19, 20, 0.95) and r["ci_low"] == lo
    assert me.wilson(0, 20)[0] == 0.0 and me.wilson(20, 20)[1] == 1.0
    assert me.rate(0, 0) == {"num": 0, "den": 0, "value": None, "ci_low": None, "ci_high": None}
    assert me.wilson(25, 20) == (None, None)  # more inventions than GT rows is not a proportion


# ======================================================================== ME-13


def test_me13_review_simulator_actions():
    wbc = gt_row("wbc", 7.2, "", unit_true="x10³/µL")  # AMB_UNITLESS_COUNT: no unit, no range on the page
    assert wbc["effective_range_page"] is None
    assert wbc["effective_range_true"] == {"kind": "two_sided", "low": 4.0, "high": 11.0}
    k = gt_row("potassium", 4.2, "mmol/L", 3.5, 5.1)
    na = gt_row("sodium", 140, "mmol/L", 135, 145)
    hb = gt_row("hemoglobin", 14.2, "g/dL", 13.0, 17.0)
    G = [wbc, k, na, hb]
    E = [
        flag(ext_row(wbc, 0, ref_low=None, ref_high=None), "NR0-UNKNOWN"),  # matched, flagged
        ext_row(k, 1, value_num=4.4, value_raw="4.4"),  # matched, wrong, not flagged
        ext_row(hb, 2, payload="parsed-row"),  # matched, right, not flagged
        flag(ext_row(hb, 3, name_raw="Page", analyte_key=None, value_num=1.0, unit_raw="", flag="UNKNOWN",
                     ref_low=None, ref_high=None, source_text="Page 1 of 2"), "NR0-NOANALYTE"),  # invented, flagged
        ext_row(hb, 4, name_raw="Lab No", analyte_key=None, value_num=18385503.0, unit_raw="", flag="UNKNOWN",
                ref_low=None, ref_high=None, source_text="Lab No 18385503"),  # invented, not flagged
    ]
    a = m.align(G, E)
    assert [(p.g, p.e) for p in a.pairs] == [(0, 0), (1, 1), (3, 2)] and a.unmatched_gt == [2]
    rev = rs.simulate(G, E, [], a)
    assert rev.actions == {"edited": 1, "accepted": 2, "deleted": 1, "kept_invented": 1, "added": 0,
                           "dismissed": 0, "missing": 1}
    assert rev.sources == [0, 1, 3, None]
    edited = rev.rows[0]
    assert (edited.origin, edited.analyte_key, edited.value_num) == ("edited", "wbc", 7.2)
    assert (edited.unit_raw, edited.ref_low, edited.ref_high) == ("x10³/µL", 4.0, 11.0)  # unit_true, range_true
    kept = rev.rows[1]
    assert (kept.origin, kept.value_num, kept.extracted_index) == ("accepted", 4.4, 1)  # wrong value kept
    assert rev.rows[2].payload == "parsed-row"  # accepted rows keep the extracted payload
    assert rev.rows[3].name_raw == "Lab No"  # the unflagged invention persists
    assert rev.row_for_gt(2) is None  # the uncovered omission is not added
    # unread lines: one covers the omitted sodium (added from GT), one covers nothing (dismissed)
    U = [unread("Sodium 140 mmol/L 135-145", 0), unread("Toll free 1800-000-0123", 1)]
    a = m.align(G, E, U)
    rev = rs.simulate(G, E, U, a)
    assert (rev.actions["added"], rev.actions["dismissed"], rev.actions["missing"]) == (1, 1, 0)
    added = rev.rows[rev.row_for_gt(2)]
    assert (added.origin, added.analyte_key, added.value_num, added.ref_low, added.ref_high) == (
        "added", "sodium", 140, 135, 145)
    # review_all: nothing is confirmed
    rev = rs.simulate(G, E, U, a, review_all=True)
    assert rev.rows == [] and rev.actions["missing"] == 4


def test_sim_rows_carry_the_confirmed_row_fields_and_score_with_the_engine():
    assert [f.name for f in dataclasses.fields(rs.SimRow)] == [f.name for f in dataclasses.fields(ConfirmedRow)]
    from tools.eval.adapters import DEFAULT_SCORER, get_scorer

    engine = get_scorer(DEFAULT_SCORER)
    rows = rs.corrected_rows(_k_report()["rows"])
    got = engine.score(rows, "male", 40)
    assert got.headline == ORACLE.score(rows, "male", 40).headline == "critical"
    assert len(got.row_flags) == 20 and got.row_flags[9] == "CRITICAL_HIGH"


# ======================================================================== additivity and diagnostics


def _mixed_records():
    gt = report(twenty())
    kgt = _k_report()
    recs = [record(gt, extraction(perfect(gt["rows"])), job("X001"))]
    ext = perfect(kgt["rows"])
    ext[9] = ext_row(kgt["rows"][9], 9, value_num=4.2, flag="NORMAL")
    recs.append(record(dict(kgt, report_id="X002"), extraction(ext), job("X002")))
    recs.append(record(dict(kgt, report_id="X003"), extraction([], status="failed", review_all=True), job("X003")))
    recs.append(record(dict(gt, report_id="X004"), extraction(perfect(gt["rows"])[:19]), job("X004", "scan")))
    return recs


def test_counts_are_additive_and_order_independent():
    recs = _mixed_records()
    whole = me.aggregate(recs)
    shuffled = list(recs)
    random.Random(5).shuffle(shuffled)
    assert me.aggregate(shuffled) == whole
    shard_a, shard_b = me.aggregate(recs[:2]), me.aggregate(recs[2:])
    merged = me.add_counts(shard_a["test"]["native"], shard_b["test"]["native"])
    assert merged == whole["test"]["native"]
    native = me.cell_results(whole["test"]["native"])
    # X001: 0 silent; X002: 1 wrong; X003: review_all (0) -> 1 / 60
    assert nd(native, "M1") == (1, 60) and nd(native, "M2") == (0, 2) and nd(native, "M5") == (1, 3)
    scan = me.cell_results(whole["test"]["scan"])
    assert nd(scan, "M1") == (1, 20) and nd(scan, "M3") == (19, 20)


def test_diagnostics_per_family_legibility_and_r_nr():
    gt = report(twenty(), family="IN-A")
    ext = perfect(gt["rows"])
    ext[0] = ext_row(gt["rows"][0], 0, value_num=14.8)  # one wrong value
    raw = "Hemoglobin 14.2 g/dL WBC 7.2 Platelet Count 250"  # three printed values legible
    rec = record(gt, extraction(ext, raw_text=raw))
    res, diag = me.cell_results(rec.counts), me.cell_diagnostics(rec.counts)
    assert nd(res, "legibility") == (3, 20)
    assert nd(diag["per_family_M3"], "IN-A") == (19, 20)
    assert nd(diag, "recall") == (20, 20) and nd(diag, "precision") == (20, 20)
    assert nd(diag["field_accuracy"], "value") == (19, 20) and nd(diag["field_accuracy"], "flag") == (20, 20)
    assert diag["headline_confusion"] == {"normal>normal": 1}
    # R-NR: the ambiguous Hb row matched and flagged / ambiguous rows
    hb = gt_row("hemoglobin", 13.0, "g/dL", sex=None, true_sex="female",
                printed=cl.PrintedRange("sex", options=(cl.OptionRange("M", 13.5, 17.5, sex="M"),
                                                        cl.OptionRange("F", 12.0, 15.5, sex="F"))))
    amb = report(twenty(replace={"hemoglobin": hb}, sex=None))
    silent = record(amb, extraction(perfect(amb["rows"])), job(sex=None))
    ext = perfect(amb["rows"])
    flag(ext[0], "NR0-UNKNOWN")
    shown = record(amb, extraction(ext), job(sex=None))
    assert nd(me.cell_diagnostics(silent.counts), "R_NR") == (0, 1)
    assert nd(me.cell_diagnostics(shown.counts), "R_NR") == (1, 1)


def test_oracle_clinical_and_gt_consistency_check():
    gt = _k_report()
    rec = record(gt, extraction([], status="failed", review_all=True), oracle_scorer=ORACLE)
    assert nd(me.cell_diagnostics(rec.counts), "M5_oracle_clinical") == (1, 1)  # GT rows re-scored: critical
    bad = dict(gt, expected=dict(gt["expected"], headline="normal"))
    with pytest.raises(ValueError):
        record(bad, extraction([]))


def test_twenty_rows_table_has_twenty_distinct_keys():
    assert len({k for k, *_ in TWENTY}) == 20


# ======================================================================== P1 review MED-5: M2_post (S3 final state)


class RangeOnlyScorer:
    """A scorer with NO critical limits at all: it flags a confirmed row LOW/HIGH/NORMAL against its range only.
    It stands in for an engine that lacks a Table C limit (the v0 engine has none for HCO3, pCO2 or pO2)."""

    name, version = "range_only", "1"

    def score(self, rows, sex=None, age=None):
        flags = []
        for r in rows:
            v, lo, hi = r.value_num, r.ref_low, r.ref_high
            flags.append("LOW" if v is not None and lo is not None and v < lo else
                         "HIGH" if v is not None and hi is not None and v > hi else "NORMAL")
        head = "abnormal" if any(f != "NORMAL" for f in flags) else "normal"
        return cl.OracleScore(head, flags, head)


HCO3_CRIT = gt_row("bicarbonate", 8, "mmol/L", 22, 29)


def _hco3_report():
    return report(twenty(replace={"chloride": HCO3_CRIT}))


def test_med5_hco3_8_is_a_table_c_critical():
    # Table C bicarbonate: critical below 10 mmol/L (low guard [10, 15]); 8 < 10 -> CRITICAL_LOW, critical
    assert HCO3_CRIT["is_critical"] and HCO3_CRIT["flag"] == "CRITICAL_LOW"


def test_med5_flagged_critical_whose_confirmed_value_the_scorer_leaves_low_does_not_count():
    """S3 final state: the HCO3 8 row is read right and flagged for review, so PRD M2 (flagged CRITICAL *or*
    needs_review) counts it. The clinician confirms 8 mmol/L; a scorer without an HCO3 limit scores it LOW (8 < 22),
    so no critical alert is raised after confirmation: M2_post must not count it."""
    gt = _hco3_report()
    ext = perfect(gt["rows"])
    ext[10] = flag(ext_row(gt["rows"][10], 10, flag="LOW"), "NR0-WARNING")  # engine: LOW, but flagged
    res = me.cell_results(record(gt, extraction(ext), scorer=RangeOnlyScorer()).counts)
    assert nd(res, "M2") == (1, 1)  # matched and needs_review: recalled before confirmation (PRD M2 as written)
    assert nd(res, "M2_post") == (0, 1)  # confirmed 8 mmol/L scored LOW: no alert -> not handled
    assert nd(res, "M5") == (0, 1)  # GT headline critical, confirmed headline abnormal
    # the same confirmed row under Table C (the GT rules) ends up CRITICAL_LOW: handled
    res_c = me.cell_results(record(gt, extraction(ext), scorer=ORACLE).counts)
    assert nd(res_c, "M2") == (1, 1) and nd(res_c, "M2_post") == (1, 1) and nd(res_c, "M5") == (1, 1)


def test_med5_unflagged_critical_shown_critical_and_accepted_counts_for_both():
    # read right, flagged CRITICAL_LOW by the product, not needs_review: accepted as is; Table C keeps it critical
    gt = _hco3_report()
    ext = perfect(gt["rows"])
    assert ext[10].flag == "CRITICAL_LOW" and not ext[10].needs_review
    res = me.cell_results(record(gt, extraction(ext)).counts)
    assert nd(res, "M2") == (1, 1) and nd(res, "M2_post") == (1, 1)


def test_med5_m2_post_is_reported_for_every_cell_and_is_additive():
    gt = _hco3_report()
    ext = perfect(gt["rows"])
    ext[10] = flag(ext_row(gt["rows"][10], 10, flag="LOW"), "NR0-WARNING")
    a = record(gt, extraction(ext), scorer=RangeOnlyScorer()).counts  # 0/1
    b = record(gt, extraction(ext), scorer=ORACLE).counts  # 1/1
    assert nd(me.cell_results(me.add_counts(a, b)), "M2_post") == (1, 2)
    assert "M2_post" in me.METRIC_IDS and "M2_post" not in me.cell_diagnostics(a)


# ======================================================================== P1 review LOW-1: M4_post diagnostic


class InrOverThreeScorer(RangeOnlyScorer):
    """Flags INR > 3.0 CRITICAL_HIGH (the v0 engine's INR limit, Appendix A), otherwise range only."""

    name = "inr_over_3"

    def score(self, rows, sex=None, age=None):
        base = super().score(rows, sex, age)
        flags = ["CRITICAL_HIGH" if r.analyte_key == "inr" and (r.value_num or 0) > 3.0 else f
                 for r, f in zip(rows, base.row_flags, strict=True)]
        head = "critical" if "CRITICAL_HIGH" in flags else base.headline
        return cl.OracleScore(head, flags, head)


def test_low1_m4_post_counts_critical_flags_raised_after_confirmation_on_non_critical_gt():
    # INR 3.2 on warfarin: Table C INR critical only above 5.0 (high guard [3.5, 5.0]) -> HIGH, not critical
    inr = gt_row("inr", 3.2, "", 0.8, 1.2)
    assert inr["flag"] == "HIGH" and not inr["is_critical"]
    gt = report(twenty(replace={"chloride": inr}))
    ext = perfect(gt["rows"])  # read right and shown HIGH before review: no pre-review false critical
    res = me.cell_results(record(gt, extraction(ext), scorer=InrOverThreeScorer()).counts)
    diag = me.cell_diagnostics(record(gt, extraction(ext), scorer=InrOverThreeScorer()).counts)
    assert nd(res, "M4") == (0, 20)  # nothing CRITICAL before review
    assert nd(diag, "M4_post") == (1, 20)  # the confirmed INR 3.2 is scored CRITICAL_HIGH: a false alert
    assert nd(me.cell_diagnostics(record(gt, extraction(ext)).counts), "M4_post") == (0, 20)  # Table C: none


# ======================================================================== P1 review LOW-4: CSV legibility


def test_low4_csv_legibility_reads_comma_delimited_fields_one_by_one():
    gt = report(twenty())
    # comma-delimited split-range export: the general tokenizer glues "7.2,10" and "250,10" (digit grouping)
    raw = "Analyte,Result,Unit,Ref Low,Ref High\nWBC Count,7.2,10^3/uL,4.0,11.0\nPlatelet Count,250,10^3/uL,150,450\n"
    as_csv = record(gt, extraction(perfect(gt["rows"]), raw_text=raw), job(rendering="csv"))
    as_pdf = record(gt, extraction(perfect(gt["rows"]), raw_text=raw), job(rendering="native"))
    assert nd(me.cell_results(as_csv.counts), "legibility") == (2, 20)  # WBC 7.2 and platelets 250 are printed
    assert nd(me.cell_results(as_pdf.counts), "legibility") == (0, 20)  # the glued tokens hide both
    # a semicolon file with decimal commas keeps "12,2" whole
    assert m.csv_tokens("Parameter;Value;Unit\nHaemoglobin;12,2;g/dL\n") == [
        "parameter", "value", "unit", "hemoglobin", "12,2", "g", "dl"]


def test_low4_csv_legibility_reads_the_pipelines_structured_text_numerically():
    """What the pipeline actually saw for a GEN-CSV job is its structured text, not the file: decimal commas become
    points and trailing zeros go ("#structured-lab-csv v1 / Haemoglobin 12.2 g/dL 11.5-15.5 / Hematocrit 37 % 35-45").
    The page printed "12,2" (decimal-comma dialect) and "37.0"; both values were seen, so both are legible. A value
    the text does not hold ("44.0" for MCV) is not."""
    hb = dict(gt_row("hemoglobin", 12.2, "g/dL", 11.5, 15.5, value_text="12,2"), features=["decimal_comma"])
    hct = dict(gt_row("hematocrit", 37.0, "%", 35, 45, value_text="37,0"), features=["decimal_comma"])
    mcv = gt_row("mcv", 44.0, "fL", 80, 100, value_text="44.0")
    gt = report([hb, hct, mcv])
    raw = "#structured-lab-csv v1\nHaemoglobin 12.2 g/dL 11.5-15.5\nHematocrit 37 % 35-45\nMCV 88 fL 80-100\n"
    rec = record(gt, extraction(perfect(gt["rows"]), raw_text=raw), job(rendering="csv"))
    assert nd(me.cell_results(rec.counts), "legibility") == (2, 3)
    # the plain value printed "4.20" and read "4.2" is the same number; "42" is not
    k = gt_row("potassium", 4.2, "mmol/L", 3.5, 5.1, value_text="4.20")
    kgt = report([k])
    seen = record(kgt, extraction(perfect(kgt["rows"]), raw_text="Potassium 4.2 mmol/L"), job())
    misread = record(kgt, extraction(perfect(kgt["rows"]), raw_text="Potassium 42 mmol/L"), job())
    assert nd(me.cell_results(seen.counts), "legibility") == (1, 1)
    assert nd(me.cell_results(misread.counts), "legibility") == (0, 1)
