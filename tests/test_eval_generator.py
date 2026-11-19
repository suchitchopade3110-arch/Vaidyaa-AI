"""Generator checks for the clinical/scenario layer (P1 plan section 11: GN-02, GN-03, GN-15)."""

from __future__ import annotations

import random

import pytest

from tools.eval import config as cfg
from tools.eval import critical_table as ct
from tools.eval import scenarios as sc
from tools.eval import units
from tools.eval.catalogue import CATALOGUE

SPLITS = [("dev", 240), ("test", 80), ("heldout", 80)]


@pytest.mark.parametrize("split,n", SPLITS)
def test_gn02_scenario_mix(split, n):
    classes = sc.allocate_scenario_classes(n, random.Random(cfg.seed_for(cfg.master_seed(split), split, 0, "scenario")))
    assert len(classes) == n
    for cls, share in (("normal", 0.30), ("mild", 0.25), ("severe", 0.20), ("critical", 0.25)):
        assert abs(classes.count(cls) - share * n) <= 1, (split, cls)
    assert classes.count("critical") >= 0.24 * n


@pytest.mark.parametrize("split,n", SPLITS)
def test_gn03_demographics(split, n):
    pts = sc.build_patients(n, random.Random(cfg.seed_for(cfg.master_seed(split), split, 0, "patient")))
    pp = 3 / 100

    def share(pred, pool=pts):
        return sum(1 for p in pool if pred(p)) / len(pool)

    assert abs(share(lambda p: p.input_sex and p.input_age is not None) - 0.82) <= pp
    assert abs(share(lambda p: p.input_sex is None and p.input_age is not None) - 0.06) <= pp
    assert abs(share(lambda p: p.input_sex is not None and p.input_age is None) - 0.06) <= pp
    assert abs(share(lambda p: p.input_sex is None and p.input_age is None) - 0.06) <= pp
    known = [p for p in pts if p.input_age is not None]
    assert abs(share(lambda p: p.age_group == "paediatric", known) - 0.13) <= pp
    assert abs(share(lambda p: p.age_group == "adult", known) - 0.63) <= pp
    assert abs(share(lambda p: p.age_group == "elderly", known) - 0.24) <= pp
    assert abs(share(lambda p: p.sex_true == "male") - 0.5) <= pp
    for p in pts:
        if p.age_group == "paediatric":
            assert 2 <= p.age_true <= 17 and p.input_age == p.age_true
        if p.input_age is None:
            assert 18 <= p.age_true <= 80  # age-unknown reports are true adults
        if p.input_sex is not None:
            assert p.input_sex == p.sex_true


def test_gn03_paediatric_scenarios_use_only_paediatric_panels_and_printed_ranges():
    rnd = random.Random(3)
    kids = [p for p in sc.build_patients(200, rnd) if p.is_paediatric]
    assert kids
    for p in kids:
        keys = sc.eligible_keys(p)
        assert keys and all(set(CATALOGUE[k].panels) & set(cfg.PAEDIATRIC_PANELS) for k in keys)
        spec = sc.plan_scenario("critical", rnd, p)
        for d in sc.draw_values(p, spec, rnd, keys):
            assert d.printed is not None  # paediatric rows always carry a printed range


def test_gn15_invariants_on_sampled_reports():
    rnd = random.Random(15)
    pts = sc.build_patients(60, rnd)
    classes = sc.allocate_scenario_classes(60, rnd)
    seen = crit = 0
    for p, cls in zip(pts, classes, strict=True):
        spec = sc.plan_scenario(cls, rnd, p)
        keys = sc.eligible_keys(p)
        for d in sc.draw_values(p, spec, rnd, keys, lab_variant=rnd.randint(0, 2)):
            seen += 1
            v = float(d.sampled.value)
            canon, _ = units.to_canonical(d.key, v, d.sampled.unit)
            a = CATALOGUE[d.key]
            assert not ct.in_guard(d.key, canon, units.to_canonical(d.key, d.printed.high, d.sampled.unit)[0]
                                   if d.printed.high is not None else None), (d.key, v)
            assert v != d.printed.low and v != d.printed.high, (d.key, v)
            if a.plaus:
                assert a.plaus[0] <= canon <= a.plaus[1]
            assert not ct.violates_cap(d.key, canon)
            crit += ct.critical_direction(d.key, canon) is not None
    assert seen > 1000 and crit > 0


# ======================================================================================================
# T05: layout kit and template families IN-A, IN-B, IN-C, US-A (GN-07, GN-08, GN-10)
# ======================================================================================================

import json  # noqa: E402
import re  # noqa: E402

import fitz  # noqa: E402

from tools.eval import clinical  # noqa: E402
from tools.eval.layouts import base as lbase  # noqa: E402
from tools.eval.layouts import templates as tpl  # noqa: E402

T05_FAMILIES = ("IN-A", "IN-B", "IN-C", "US-A")

# Independent copy of the plan section 3.9 deny-list (the test does not import the generator's copy).
DENY = ("Lal", "PathLabs", "SRL", "Agilus", "Metropolis", "Thyrocare", "Quest", "LabCorp", "NHS", "Apollo", "Fortis",
        "Medanta", "Manipal", "Neuberg", "Vijaya", "Suburban", "Redcliffe", "Healthians", "Pathkind", "1mg", "Mayo",
        "ARUP", "BioReference", "Sonic", "Eurofins", "NABL", "CAP", "AIIMS")
DENY_RE = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(map(re.escape, DENY)) + r")(?![A-Za-z0-9])", re.I)


def _t05_patients():
    pts = sc.build_patients(80, random.Random(7))
    adult = next(p for p in pts if p.input_sex and p.input_age is not None and p.age_group == "adult")
    unknown = next(p for p in pts if p.input_sex is None and p.input_age is None)
    kid = next(p for p in pts if p.age_group == "paediatric")
    return adult, unknown, kid


# (patient index, scenario class, pages): adult/critical/2, unknown demographics/severe/1, paediatric/mild/2
T05_CASES = ((0, "critical", 2), (1, "severe", 1), (2, "mild", 2))


@pytest.fixture(scope="module")
def t05_reports(tmp_path_factory):
    out = {}
    pts = _t05_patients()
    d = tmp_path_factory.mktemp("t05")
    for fam in T05_FAMILIES:
        for n, (pi, cls, pages) in enumerate(T05_CASES):
            rep = tpl.build_report(fam, split="dev", index=n, patient=pts[pi], scenario_class=cls, target_pages=pages)
            path = d / f"{fam}_{n}.pdf"
            tpl.native_pdf(rep, str(path))
            out[(fam, n)] = (rep, lbase.build_gt(rep).to_dict(), path)
    return out


def _words(page):
    return [(w[0], w[1], w[2], w[3], w[4]) for w in page.get_text("words")]


def _inside(w, bbox, tol=2.0):
    return w[0] >= bbox[0] - tol and w[1] >= bbox[1] - tol and w[2] <= bbox[2] + tol and w[3] <= bbox[3] + tol


def test_gn07_bbox_text(t05_reports):
    for (fam, n), (rep, gt, path) in t05_reports.items():
        doc = fitz.open(str(path))
        words = {i + 1: _words(pg) for i, pg in enumerate(doc)}
        assert gt["rows"], (fam, n)
        for r in gt["rows"]:
            inbox = [w[4] for w in words[r["page"]] if _inside(w, r["bbox"])]
            assert r["value_text"] in inbox, (fam, n, r["row_id"], r["value_text"], inbox)
            assert any(t in inbox for t in r["name"].split()), (fam, n, r["row_id"], r["name"], inbox)
        for dcy in gt["decoys"]:
            inbox = [w[4] for w in words[dcy["page"]] if _inside(w, dcy["bbox"])]
            for tok in dcy["text"].split():
                assert tok in inbox, (fam, n, dcy["kind"], tok)


def test_gn07_rows_do_not_overlap_and_stay_on_page(t05_reports):
    for (fam, n), (rep, gt, _) in t05_reports.items():
        w, h = rep.page_size
        by_page = {}
        for r in gt["rows"]:
            x0, y0, x1, y1 = r["bbox"]
            assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, (fam, n, r["row_id"])
            by_page.setdefault(r["page"], []).append(r["bbox"])
        for boxes in by_page.values():
            boxes.sort(key=lambda b: b[1])
            for a, b in zip(boxes, boxes[1:], strict=False):
                assert a[3] <= b[1] + 0.5, (fam, n, a, b)  # successive rows are vertically disjoint


def test_gn08_pages(t05_reports):
    for (fam, n), (rep, gt, path) in t05_reports.items():
        doc = fitz.open(str(path))
        assert 1 <= len(doc) <= 3
        assert len(doc) == len(gt["pages"]) == T05_CASES[n][2], (fam, n)
        assert {r["page"] for r in gt["rows"]} <= set(range(1, len(doc) + 1))
        for pg, meta in zip(doc, gt["pages"], strict=True):
            assert (round(pg.rect.width, 1), round(pg.rect.height, 1)) == (round(meta["width_pt"], 1),
                                                                             round(meta["height_pt"], 1))


@pytest.mark.parametrize("fam", T05_FAMILIES)
def test_gn08_three_page_target_and_repeated_headers(fam, tmp_path):
    pt = sc.build_patients(40, random.Random(11))[0]
    pt = next(p for p in sc.build_patients(80, random.Random(7)) if p.input_sex and p.input_age and not p.is_paediatric)
    rep = tpl.build_report(fam, split="dev", index=9, patient=pt, scenario_class="normal", target_pages=3)
    path = tmp_path / "r.pdf"
    tpl.native_pdf(rep, str(path))
    doc = fitz.open(str(path))
    assert len(doc) == 3
    for i, pg in enumerate(doc, 1):  # header repeats on every page; footer carries 'Page i of 3'
        txt = pg.get_text()
        assert pt.patient_id in txt and f"Page {i} of 3" in txt
    assert "End of Report" in doc[2].get_text() and "End of Report" not in doc[0].get_text()
    gt = lbase.build_gt(rep).to_dict()
    # a panel continuing across a page break repeats its title with (contd.)
    titles_by_page = {}
    for s in gt["sections"]:
        for p in s["pages"]:
            titles_by_page.setdefault(p, []).append(s["title"])
    for s in gt["sections"]:
        if len(s["pages"]) > 1:
            assert "(contd.)" in doc[s["pages"][1] - 1].get_text()


def test_t05_build_is_deterministic(tmp_path):
    pt = _t05_patients()[0]
    outs = []
    for k in range(2):
        rep = tpl.build_report("IN-A", split="dev", index=3, patient=pt, scenario_class="severe", target_pages=2)
        p = tmp_path / f"a{k}.pdf"
        tpl.native_pdf(rep, str(p))
        outs.append((p.read_bytes(), json.dumps(lbase.build_gt(rep).to_dict(), sort_keys=True)))
    assert outs[0] == outs[1]


# ---- hand-derived clinical expectations on the printed page

_NUM = r"\d[\d,]*\.?\d*"


def _num(s):
    return float(s.replace(",", ""))


def _hand_region(text, value, sex, age):
    """LOW/NORMAL/HIGH from the printed range text alone, written from the plan's range syntaxes."""
    lines = re.split(r"\s+(?=(?:M|F|Male|Female|Adult|Child):)", text.strip())
    chosen = None
    for ln in lines:
        m = re.match(r"^(M|F|Male|Female|Adult|Child):\s*(.*)$", ln.strip())
        if m:
            tag = m.group(1)
            if tag in ("M", "Male") and sex == "male" or tag in ("F", "Female") and sex == "female" \
                    or tag == "Adult" and age >= 18 or tag == "Child" and age < 18:
                chosen = m.group(2)
        elif chosen is None:
            chosen = ln.strip()
    t = chosen
    m = re.search(rf"^[\[(]?({_NUM})\s*(?:-|–|to)\s*({_NUM})[\])]?", t)
    if m:
        lo, hi = _num(m.group(1)), _num(m.group(2))
        return "LOW" if value < lo else "HIGH" if value > hi else "NORMAL"
    m = re.search(rf"^(?:<|Up to)\s*({_NUM})", t)
    if m:
        return "HIGH" if value >= _num(m.group(1)) else "NORMAL"
    m = re.search(rf"^>\s*({_NUM})", t)
    if m:
        return "LOW" if value <= _num(m.group(1)) else "NORMAL"
    return None


def test_t05_flags_match_hand_reading_of_the_printed_range(t05_reports):
    checked = 0
    for (fam, n), (rep, gt, _) in t05_reports.items():
        pat = gt["patient"]
        for r in gt["rows"]:
            if r["value_type"] != "numeric" or r["range"]["kind"] in ("interpretive", "none") or r["qualifier"]:
                continue
            if r["is_critical"] or r["flag"] == "UNKNOWN":
                continue
            exp = _hand_region(r["range"]["text"], r["value"], pat["sex_true"], pat["age_true"])
            assert exp is not None, (fam, r["range"]["text"])
            assert r["flag_true"] == exp, (fam, n, r["name"], r["value_text"], r["range"]["text"], r["flag_true"])
            checked += 1
    assert checked > 150


# Hand-copied from plan Table C (canonical conventional units): (critical below, critical above)
HAND_TABLE_C = {
    "potassium": (2.5, 6.5), "sodium": (118, 160), "hemoglobin": (6.0, 21.0), "platelets": (20, 1000),
    "wbc": (1.0, 50), "glucose_fasting": (40, 500), "glucose_pp": (40, 500), "calcium": (6.0, 13.5),
    "creatinine": (None, 10.0), "inr": (None, 5.0), "bicarbonate": (10, 45),
}


def test_t05_criticality_matches_hand_table_c(t05_reports):
    n_crit = 0
    for (fam, n), (rep, gt, _) in t05_reports.items():
        for r in gt["rows"]:
            if r["analyte_key"] not in HAND_TABLE_C or r["value_type"] != "numeric":
                continue
            lo, hi = HAND_TABLE_C[r["analyte_key"]]
            v = r["canonical"]["value"]
            exp = (lo is not None and v < lo) or (hi is not None and v > hi)
            assert r["is_critical"] == exp, (fam, n, r["analyte_key"], v)
            n_crit += exp
        if T05_CASES[n][1] == "critical":
            assert gt["expected"]["headline"] == "critical" and gt["expected"]["critical_rows"]
        if T05_CASES[n][1] == "mild":
            assert gt["expected"]["headline"] == "abnormal"
    assert n_crit >= 1


def test_t05_gt_schema_and_unknown_demographics(t05_reports):
    from tools.eval.gt_schema import validate_gt

    for (fam, n), (rep, gt, _) in t05_reports.items():
        assert validate_gt(gt) == [], (fam, n)
        pat = gt["patient"]
        txt = rep.all_text()
        if pat["input_sex"] is None and pat["input_age"] is None:
            assert "Age" not in txt.split("Referred")[0] and "Sex:" not in txt  # unknown demographics are not printed
        if pat["input_age"] is not None:
            assert f"{pat['input_age']} Years" in txt or f"Age: {pat['input_age']}" in txt
        for r in gt["rows"]:
            assert r["unit_true"] == r["unit"] and r["value_text"] in r["line_text"]


def test_t05_ldl_not_calculated_when_tg_high():
    # Friedewald is invalid at TG >= 400 mg/dL (plan 3.6): the helper returns None and the template prints a decoy.
    assert sc.ldl_friedewald(250.0, 40.0, 450.0) is None
    assert sc.ldl_friedewald(200.0, 50.0, 150.0) == 120.0  # 200 - 50 - 150/5


# ---- GN-10 fictional identity

PHONE_ALLOWED = (r"\(\d{3}\) 555-01\d{2}", r"01632 960\d{3}", r"0000-2\d{6}", r"\+91 00000 0\d{4}",
                 r"1800-000-0\d{3}")
PHONE_ANY = re.compile(r"\(\d{3}\) \d{3}-\d{4}|\+91 \d{5} \d{5}|\b0000-\d{7}\b|\b1800-\d{3}-\d{4}\b|\b\d{4}-\d{7}\b")


def test_gn10_deny_list_matcher_semantics():
    assert DENY_RE.search("Report from QUEST labs") and DENY_RE.search("cap")
    assert not DENY_RE.search("Questionnaire") and not DENY_RE.search("capsule") and not DENY_RE.search("CLIA")
    assert lbase.deny_hits("Mayo and CLIA") == ["Mayo"]


def test_gn10_name_pools_are_clean():
    pools = (lbase.LAB_NAMES, lbase.STREETS_IN, lbase.CITIES_IN, lbase.STREETS_US, lbase.CITIES_US,
             lbase.DOCTOR_FIRST, lbase.DOCTOR_LAST, cfg.FIRST_NAMES_F, cfg.FIRST_NAMES_M, cfg.SURNAMES)
    for pool in pools:
        for item in pool:
            assert not DENY_RE.search(item), item


def test_gn10_fictional_identity(t05_reports):
    for (fam, n), (rep, gt, path) in t05_reports.items():
        doc = fitz.open(str(path))
        page_text = "\n".join(pg.get_text() for pg in doc)
        blob = json.dumps(gt, ensure_ascii=False) + "\n" + page_text
        assert not DENY_RE.search(blob), (fam, n, DENY_RE.search(blob).group(0))
        assert gt["lab"]["fictional"] is True
        phones = PHONE_ANY.findall(page_text)
        assert phones, (fam, n)
        for ph in phones:
            assert any(re.fullmatch(p, ph) for p in PHONE_ALLOWED), (fam, n, ph)
        for host in re.findall(r"[\w.+-]+@([\w.-]+)", page_text) + re.findall(r"www\.([\w.-]+)", page_text):
            assert host.endswith(("example.org", "example.com")), (fam, n, host)
        assert re.search(r"Accreditation Cert\. No\. FA-\d{4}", page_text)
        assert re.search(r"Reg\. No\. FX-\d{5}", page_text)
        if fam == "US-A":
            assert re.search(r"\(\d{3}\) 555-01\d{2}", page_text)
        else:
            assert re.search(r"0000-2\d{6}|\+91 00000 0\d{4}", page_text) and re.search(r"1800-000-0\d{3}", page_text)
        assert gt["decoys"] and {"phone", "tollfree", "page_x_of_y", "accreditation", "address"} <= {
            d["kind"] for d in gt["decoys"]}


def test_t05_standard14_fonts_only(t05_reports):
    for (fam, n), (rep, gt, path) in t05_reports.items():
        doc = fitz.open(str(path))
        for pg in doc:
            for f in pg.get_fonts():
                assert f[3] in ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Times-Roman", "Times-Bold"), f
        for pg in doc:
            pg.get_text().encode("cp1252")  # WinAnsi only; the plan's '<=' / '>=' substitutes stay ASCII
        assert not any(c in rep.all_text() for c in "\u2264\u2265")


def test_t05_family_structure(t05_reports):
    feats = {fam: set() for fam in T05_FAMILIES}
    for (fam, n), (rep, gt, _) in t05_reports.items():
        for r in gt["rows"]:
            feats[fam].update(r["features"])
    assert "method_column" in feats["IN-A"] and "technology_column" in feats["IN-B"] and "glued_flag" in feats["IN-B"]
    assert "method_under_name" in feats["IN-C"] and "flag_column" in feats["US-A"]
    for (fam, n), (rep, gt, path) in t05_reports.items():
        txt = "\n".join(pg.get_text() for pg in fitz.open(str(path)))
        if fam == "IN-A":
            assert "Method" in txt and "Bio. Ref. Interval" in txt
        if fam == "IN-B":
            assert "Technology" in txt
        if fam == "US-A":
            assert "FINAL REPORT" in txt and "Flag" in txt and "Lab" in txt
        if fam == "US-A":
            assert gt["unit_system"] == "conventional"
        flagged = [r for r in gt["rows"] if r["printed_flag"]]
        if fam in ("IN-A", "IN-C"):
            assert not flagged
        else:
            for r in flagged:
                assert r["printed_flag"] in ("H", "L", "HH", "LL")
                if fam == "IN-B":
                    assert r["printed_flag"] in ("H", "L") and f"{r['value_text']} {r['printed_flag']}" in r["line_text"]


def test_t05_sex_labelled_range_without_sex_is_ambiguous_only_when_sides_differ():
    # Hand case: Hb 12.5 against 'M: 13.0 - 17.0 / F: 12.0 - 15.0' with sex unknown. Low for a man, normal for a
    # woman -> not on the same side of every range -> UNKNOWN + AMB_SEX_RANGES_NO_SEX; truth follows the true sex.
    pr = clinical.PrintedRange("sex", options=(clinical.OptionRange("M", 13.0, 17.0, sex="M"),
                                               clinical.OptionRange("F", 12.0, 15.0, sex="F")))
    t = clinical.derive_row("hemoglobin", value=12.5, unit="g/dL", printed_range=pr, input_sex=None, true_sex="female")
    assert t.flag == "UNKNOWN" and t.ambiguity == ["AMB_SEX_RANGES_NO_SEX"] and t.flag_true == "NORMAL"
    # Hb 10.5 is below both ranges: determinable LOW, not ambiguous (plan 3.8).
    t2 = clinical.derive_row("hemoglobin", value=10.5, unit="g/dL", printed_range=pr, input_sex=None, true_sex="male")
    assert t2.flag == "LOW" and not t2.ambiguity


def test_t05_sample_invariants_class_and_pages():
    """Across 20 reports per family: pages == target, headline follows the scenario class (plan 3.3/4.2.5),
    no printed value sits in a Table C guard band or on a printed bound (GN-15 on these families)."""
    n = 20
    pts = sc.build_patients(n, random.Random(21))
    classes = sc.allocate_scenario_classes(n, random.Random(22))
    pages = [1] * 9 + [2] * 8 + [3] * 3
    random.Random(23).shuffle(pages)
    want = {"normal": "normal", "mild": "abnormal", "severe": "abnormal", "critical": "critical"}
    for fam in T05_FAMILIES:
        for i in range(n):
            rep = tpl.build_report(fam, split="test", index=i, patient=pts[i], scenario_class=classes[i],
                                   target_pages=pages[i])
            gt = lbase.build_gt(rep).to_dict()
            assert len(gt["pages"]) == pages[i], (fam, i)
            assert gt["expected"]["headline"] == want[classes[i]], (fam, i, classes[i])
            for r in gt["rows"]:
                if r["value_type"] != "numeric":
                    continue
                key, c = r["analyte_key"], r["canonical"]["value"]
                if not r["qualifier"]:
                    assert not ct.in_guard(key, c, None), (fam, i, key, c)
                bounds = [b for b in (r["range"]["low"], r["range"]["high"]) if b is not None]
                bounds += [v for o in r["range"]["options"] for v in (o["low"], o["high"]) if v is not None]
                assert r["value"] not in bounds or r["qualifier"], (fam, i, key, r["value"])
                if r["analyte_key"] != "egfr" and CATALOGUE[key].plaus:
                    lo, hi = CATALOGUE[key].plaus
                    assert lo <= c <= hi, (fam, i, key, c)


# ======================================================================================================
# T06 section: IN-D, IN-E, UK-A (native PDF) and GEN-CSV (text export): GN-07, GN-08, GN-10 + family structure
# ======================================================================================================

import csv as _csv  # noqa: E402
import datetime as _dt  # noqa: E402
import io as _io  # noqa: E402

from tools.eval import layouts as _layouts  # noqa: E402
from tools.eval.layouts import csv_export as _csvx  # noqa: E402

T06_PDF = ("IN-D", "IN-E", "UK-A")
T06_ALL = (*T06_PDF, "GEN-CSV")


def _t06_build(fam, n, pt, cls, pages, split="dev"):
    return tpl.build_report(fam, split=split, index=n, patient=pt, scenario_class=cls, target_pages=pages)


@pytest.fixture(scope="module")
def t06_reports(tmp_path_factory):
    out = {}
    pts = _t05_patients()
    d = tmp_path_factory.mktemp("t06")
    for fam in T06_ALL:
        for n, (pi, cls, pages) in enumerate(T05_CASES):
            rep = _t06_build(fam, n, pts[pi], cls, pages)
            path = d / f"{fam}_{n}.{'csv' if fam == 'GEN-CSV' else 'pdf'}"
            if fam == "GEN-CSV":
                _csvx.write_csv(rep, str(path))
            else:
                tpl.native_pdf(rep, str(path))
            out[(fam, n)] = (rep, lbase.build_gt(rep).to_dict(), path)
    return out


def _pdf_items(t06):
    return {k: v for k, v in t06.items() if k[0] in T06_PDF}


def test_t06_families_are_registered_templates():
    for fid in T06_ALL:
        e = _layouts.FAMILIES[fid]
        assert (e.kind, e.scope) == ("template", "shared")
    rep = _layouts.FAMILIES["UK-A"].builder("UK-A", split="dev", index=0, patient=_t05_patients()[0],
                                            scenario_class="normal", target_pages=1)
    assert rep.family == "UK-A"


# ---- GN-07 / GN-08 on the three PDF families

def test_gn07_t06_bbox_text(t06_reports):
    for (fam, n), (rep, gt, path) in _pdf_items(t06_reports).items():
        doc = fitz.open(str(path))
        words = {i + 1: _words(pg) for i, pg in enumerate(doc)}
        assert gt["rows"], (fam, n)
        for r in gt["rows"]:
            inbox = [w[4] for w in words[r["page"]] if _inside(w, r["bbox"])]
            assert r["value_text"] in inbox, (fam, n, r["row_id"], r["value_text"], inbox)
            assert any(t in inbox for t in r["name"].split()), (fam, n, r["row_id"], r["name"], inbox)
        for dcy in gt["decoys"]:
            inbox = [w[4] for w in words[dcy["page"]] if _inside(w, dcy["bbox"])]
            for tok in dcy["text"].split():
                assert tok in inbox, (fam, n, dcy["kind"], tok)


def _overlap(a, b):
    return min(a[2], b[2]) - max(a[0], b[0]) > 0.5 and min(a[3], b[3]) - max(a[1], b[1]) > 0.5


def test_gn07_t06_row_boxes_stay_on_page_and_never_overlap(t06_reports):
    for (fam, n), (rep, gt, _) in _pdf_items(t06_reports).items():
        w, h = rep.page_size
        boxes = {}
        for r in gt["rows"]:
            x0, y0, x1, y1 = r["bbox"]
            assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, (fam, n, r["row_id"])
            boxes.setdefault(r["page"], []).append(r["bbox"])
        for bs in boxes.values():
            for i, a in enumerate(bs):
                for b in bs[i + 1:]:
                    assert not _overlap(a, b), (fam, n, a, b)


def test_gn08_t06_pages_and_orientation(t06_reports):
    for (fam, n), (rep, gt, path) in _pdf_items(t06_reports).items():
        doc = fitz.open(str(path))
        assert len(doc) == len(gt["pages"]) == T05_CASES[n][2], (fam, n)
        for pg, meta in zip(doc, gt["pages"], strict=True):
            assert (round(pg.rect.width, 1), round(pg.rect.height, 1)) == (round(meta["width_pt"], 1),
                                                                             round(meta["height_pt"], 1))
            assert (pg.rect.width > pg.rect.height) == (fam == "IN-D")  # only the cumulative report is landscape


@pytest.mark.parametrize("fam", T06_PDF)
def test_gn08_t06_three_page_target_and_repeated_headers(fam, tmp_path):
    pt = next(p for p in sc.build_patients(80, random.Random(7)) if p.input_sex and p.input_age and not p.is_paediatric)
    rep = tpl.build_report(fam, split="dev", index=9, patient=pt, scenario_class="normal", target_pages=3)
    path = tmp_path / "r.pdf"
    tpl.native_pdf(rep, str(path))
    doc = fitz.open(str(path))
    assert len(doc) == 3
    for i, pg in enumerate(doc, 1):
        txt = pg.get_text()
        assert pt.patient_id in txt and f"Page {i} of 3" in txt
    assert "End of Report" in doc[2].get_text() and "End of Report" not in doc[0].get_text()
    gt = lbase.build_gt(rep).to_dict()
    for s in gt["sections"]:
        if len(s["pages"]) > 1:
            assert "(contd.)" in doc[s["pages"][1] - 1].get_text(), (fam, s["title"])


def test_t06_builds_are_deterministic(tmp_path):
    pt = _t05_patients()[0]
    for fam in T06_ALL:
        outs = []
        for k in range(2):
            rep = _t06_build(fam, 3, pt, "severe", 2)
            if fam == "GEN-CSV":
                outs.append((_csvx.csv_text(rep), json.dumps(lbase.build_gt(rep).to_dict(), sort_keys=True)))
            else:
                p = tmp_path / f"{fam}{k}.pdf"
                tpl.native_pdf(rep, str(p))
                outs.append((p.read_bytes(), json.dumps(lbase.build_gt(rep).to_dict(), sort_keys=True)))
        assert outs[0] == outs[1], fam


def test_t06_gt_schema_and_flags_match_hand_reading(t06_reports):
    from tools.eval.gt_schema import validate_gt

    checked = 0
    for (fam, n), (rep, gt, _) in t06_reports.items():
        assert validate_gt(gt) == [], (fam, n)
        pat = gt["patient"]
        for r in gt["rows"]:
            assert r["unit_true"] == r["unit"] or r["unit_placement"] == "none", (fam, r["row_id"])
            if r["value_type"] != "numeric" or r["range"]["kind"] in ("interpretive", "none") or r["qualifier"]:
                continue
            if r["is_critical"] or r["flag"] == "UNKNOWN":
                continue
            exp = _hand_region(r["range"]["text"], r["value"], pat["sex_true"], pat["age_true"])
            assert exp is not None, (fam, r["range"]["text"])
            assert r["flag_true"] == exp, (fam, n, r["name"], r["value_text"], r["range"]["text"], r["flag_true"])
            checked += 1
    assert checked > 150


def test_t06_criticality_matches_hand_table_c_and_class_headline(t06_reports):
    n_crit = 0
    for (fam, n), (rep, gt, _) in t06_reports.items():
        for r in gt["rows"]:
            if r["analyte_key"] not in HAND_TABLE_C or r["value_type"] != "numeric":
                continue
            lo, hi = HAND_TABLE_C[r["analyte_key"]]
            v = r["canonical"]["value"]
            exp = (lo is not None and v < lo) or (hi is not None and v > hi)
            assert r["is_critical"] == exp, (fam, n, r["analyte_key"], v)
            n_crit += exp
        if T05_CASES[n][1] == "critical":
            assert gt["expected"]["headline"] == "critical" and gt["expected"]["critical_rows"], (fam, n)
        if T05_CASES[n][1] == "mild":
            assert gt["expected"]["headline"] == "abnormal", (fam, n)
    assert n_crit >= 1


def test_t06_sample_invariants_class_pages_guard_bands():
    n = 12
    pts = sc.build_patients(n, random.Random(31))
    classes = sc.allocate_scenario_classes(n, random.Random(32))
    pages = [1] * 5 + [2] * 5 + [3] * 2
    random.Random(33).shuffle(pages)
    want = {"normal": "normal", "mild": "abnormal", "severe": "abnormal", "critical": "critical"}
    for fam in T06_ALL:
        for i in range(n):
            rep = _t06_build(fam, i, pts[i], classes[i], pages[i], split="test")
            gt = lbase.build_gt(rep).to_dict()
            assert len(gt["pages"]) == (1 if fam == "GEN-CSV" else pages[i]), (fam, i)
            assert gt["expected"]["headline"] == want[classes[i]], (fam, i, classes[i])
            for r in gt["rows"]:
                if r["value_type"] != "numeric":
                    continue
                key, c = r["analyte_key"], r["canonical"]["value"]
                if not r["qualifier"]:
                    assert not ct.in_guard(key, c, None), (fam, i, key, c)
                bounds = [b for b in (r["range"]["low"], r["range"]["high"]) if b is not None]
                bounds += [v for o in r["range"]["options"] for v in (o["low"], o["high"]) if v is not None]
                assert r["value"] not in bounds or r["qualifier"], (fam, i, key, r["value"])
                if key != "egfr" and CATALOGUE[key].plaus:
                    lo, hi = CATALOGUE[key].plaus
                    assert lo <= c <= hi, (fam, i, key, c)


# ---- GN-10 fictional identity (PDF families and CSV)

PHONE_UK = re.compile(r"\b01632 960\d{3}\b")


def test_gn10_t06_fictional_identity(t06_reports):
    for (fam, n), (rep, gt, path) in t06_reports.items():
        if fam == "GEN-CSV":
            page_text = path.read_text(encoding="utf-8")
        else:
            page_text = "\n".join(pg.get_text() for pg in fitz.open(str(path)))
        blob = json.dumps(gt, ensure_ascii=False) + "\n" + page_text
        assert not DENY_RE.search(blob), (fam, n, DENY_RE.search(blob).group(0))
        assert gt["lab"]["fictional"] is True
        if fam == "GEN-CSV":
            continue  # a CSV carries no phone, address or accreditation lines
        phones = PHONE_ANY.findall(page_text) + PHONE_UK.findall(page_text)
        assert phones, (fam, n)
        allowed = PHONE_ALLOWED + (r"01632 960\d{3}",)
        for ph in phones:
            assert any(re.fullmatch(p, ph) for p in allowed), (fam, n, ph)
        if fam == "UK-A":
            assert PHONE_UK.search(page_text) and not PHONE_ANY.search(page_text)
            assert "NHS" not in page_text.upper().split() and "nhs" not in page_text.lower()
        else:
            assert re.search(r"0000-2\d{6}|\+91 00000 0\d{4}", page_text) and re.search(r"1800-000-0\d{3}", page_text)
        for host in re.findall(r"[\w.+-]+@([\w.-]+)", page_text) + re.findall(r"www\.([\w.-]+)", page_text):
            assert host.endswith(("example.org", "example.com")), (fam, n, host)
        assert re.search(r"Accreditation Cert\. No\. FA-\d{4}", page_text)
        assert re.search(r"Reg\. No\. FX-\d{5}", page_text)
        assert {"phone", "tollfree", "page_x_of_y", "accreditation", "address"} <= {d["kind"] for d in gt["decoys"]}


def test_gn10_t06_uk_name_pools_are_clean():
    for item in (*tpl.UK_STREETS, *tpl.UK_CITIES):
        assert not DENY_RE.search(item), item


def test_t06_standard14_fonts_only(t06_reports):
    for (fam, n), (rep, gt, path) in _pdf_items(t06_reports).items():
        doc = fitz.open(str(path))
        for pg in doc:
            for f in pg.get_fonts():
                assert f[3] in ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique"), (fam, f)
            pg.get_text().encode("cp1252")
        assert not any(c in rep.all_text() for c in "≤≥")


# ---- IN-D: cumulative landscape

_DATE_RE = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def _d(s):
    return _dt.datetime.strptime(s, "%d/%m/%Y").date()


def _ind_columns(words, row_bbox):
    """Header date words (x0, text) of the column header closest above a row, found from the page alone."""
    cand = [w for w in words if _DATE_RE.match(w[4]) and w[3] <= row_bbox[1] + 0.5]
    ymax = max(w[1] for w in cand)
    return sorted((w[0], w[4]) for w in cand if abs(w[1] - ymax) < 2.0)


def test_t06_ind_current_column_holds_gt_value_and_previous_is_a_different_earlier_value(t06_reports):
    checked = prev_checked = 0
    for (fam, n), (rep, gt, path) in t06_reports.items():
        if fam != "IN-D":
            continue
        doc = fitz.open(str(path))
        collected = re.search(r"Collected: (\d{2}/\d{2}/\d{4})", doc[0].get_text()).group(1)
        for r in gt["rows"]:
            words = _words(doc[r["page"] - 1])
            cols = _ind_columns(words, r["bbox"])
            dates = [_d(t) for _, t in cols]
            assert 2 <= len(cols) <= 3 and dates == sorted(dates) and len(set(dates)) == len(dates), (n, cols)
            assert cols[-1][1] == collected  # the newest (rightmost) column is the current report's date
            assert len(r["previous"]) == len(cols) - 1 and "cumulative" in r["features"]
            xs = [x for x, _ in cols] + [cols[-1][0] + 70.0]
            cells = []
            for k in range(len(cols)):
                inrow = [w for w in words if _inside(w, r["bbox"]) and xs[k] - 1.5 <= w[0] < xs[k + 1] - 1.5]
                cells.append(" ".join(w[4] for w in sorted(inrow, key=lambda w: (w[0], w[1]))))
            if "unlabelled_multivalue" in r["features"]:
                assert "AMB_UNLABELLED_MULTIVALUE" in r["ambiguity"] and r["expected_needs_review"]
                assert cells[0].split() == [p["value_text"] for p in r["previous"]] + [r["value_text"]]
                continue
            cur = f"{r['qualifier']} {r['value_text']}" if r["qualifier"] else r["value_text"]
            assert cells[-1] == cur, (n, r["name"], cells, cur)  # current column = the GT value
            same_analyte = {x["value_text"] for x in gt["rows"] if x["analyte_key"] == r["analyte_key"]}
            for k, p in enumerate(r["previous"]):
                assert cells[k] == p["value_text"], (n, r["name"], cells, p)
                assert p["date"] == cols[k][1] and _d(p["date"]) < _d(collected)
                assert p["value_text"] not in same_analyte and p["value"] != r["value"]  # earlier value is not in GT
                prev_checked += 1
            checked += 1
    assert checked > 40 and prev_checked > 40


def test_t06_ind_unlabelled_multivalue_rows_are_ambiguous_with_current_truth():
    # Hand case: K 6.9 mmol/L is critical (Table C: > 6.5) but with an earlier 4.1 in the same unlabelled cell the page
    # cannot say which is current -> UNKNOWN + AMB_UNLABELLED_MULTIVALUE; the truth follows the current value.
    pr = clinical.PrintedRange("two_sided", 3.5, 5.1, text="3.5 - 5.1")
    t = clinical.derive_row("potassium", value=6.9, unit="mmol/L", printed_range=pr, input_sex="male", input_age=40,
                            true_sex="male", true_age=40, unlabelled_multivalue=True)
    assert t.flag == "UNKNOWN" and t.ambiguity == ["AMB_UNLABELLED_MULTIVALUE"] and t.flag_true == "CRITICAL_HIGH"
    # And the generator prints such rows (3% of IN-D rows) as ONE cell: "<older> <current>", current last.
    pt = _t05_patients()[0]
    found = 0
    for i in range(60):
        rep = tpl.build_report("IN-D", split="dev", index=i, patient=pt, scenario_class="severe", target_pages=2)
        for r in rep.rows:
            if r.unlabelled_multivalue:
                found += 1
                assert r.line_text.split(" | ")[1].split() == [p["value_text"] for p in r.previous] + [r.value_text]
                g = next(x for x in lbase.build_gt(rep).rows if x.row_id.endswith(f"r{rep.rows.index(r) + 1:03d}"))
                assert g.flag == "UNKNOWN" and g.expected_needs_review and "unlabelled_multivalue" in g.features
    assert found >= 1


def test_t06_ind_header_marks_current_in_about_half_the_reports():
    labels = [tpl.setup(tpl.SPECS["IN-D"], split="dev", index=i, target_pages=1)[1].current_label for i in range(200)]
    assert 0.40 <= sum(labels) / 200 <= 0.60
    pt = _t05_patients()[0]
    seen = set()
    for i in range(12):
        rep = tpl.build_report("IN-D", split="dev", index=i, patient=pt, scenario_class="mild", target_pages=1)
        lay_label = tpl.setup(tpl.SPECS["IN-D"], split="dev", index=i, target_pages=1)[1].current_label
        assert ("Current" in rep.all_text().split()) == lay_label
        seen.add(lay_label)
    assert seen == {True, False}


# ---- IN-E: hospital two-panel

def test_t06_ine_two_tests_share_a_line_left_panel_drawn_before_right(t06_reports):
    paired = 0
    for (fam, n), (rep, gt, path) in t06_reports.items():
        if fam != "IN-E":
            continue
        doc = fitz.open(str(path))
        assert "Ward/Bed:" in doc[0].get_text() and any(d["kind"] == "header_id" and "Ward/Bed" in d["text"]
                                                          for d in gt["decoys"])
        for r in gt["rows"]:
            mates = [o for o in gt["rows"] if o is not r and o["page"] == r["page"]
                     and min(o["bbox"][3], r["bbox"][3]) - max(o["bbox"][1], r["bbox"][1]) > 0.5 * (r["bbox"][3] - r["bbox"][1])]
            assert len(mates) <= 1, (n, r["name"])  # at most two tests per visual line
            if mates:
                paired += 1
                assert not _overlap(r["bbox"], mates[0]["bbox"])
                assert (r["bbox"][2] < 300) != (mates[0]["bbox"][2] < 300)  # one in each half
        # text-layer order: per page and block, the whole left panel comes before the right panel
        for pgno, pg in enumerate(doc, 1):
            order = {}
            ws = _words(pg)
            for r in gt["rows"]:
                if r["page"] != pgno:
                    continue
                idx = min(i for i, w in enumerate(ws) if _inside(w, r["bbox"]) and w[4] == r["value_text"])
                order[r["section"]] = order.get(r["section"], []) + [idx]
            for left, right in (("HAEMATOLOGY", "BIOCHEMISTRY"), ("LIPID PROFILE", "LIVER FUNCTION TEST (LFT)")):
                if left in order and right in order:
                    assert max(order[left]) < min(order[right]), (n, pgno, left)
    assert paired > 20


def test_t06_ine_cbc_unit_only_in_panel_header_or_unitless_amb():
    pts = _t05_patients()
    amb_seen = hdr_seen = 0
    for i in range(60):
        rep = tpl.build_report("IN-E", split="dev", index=i, patient=pts[0], scenario_class="normal", target_pages=1)
        gt = lbase.build_gt(rep).to_dict()
        text = rep.all_text()
        lay = tpl.setup(tpl.SPECS["IN-E"], split="dev", index=i, target_pages=1)[1]
        counts = [r for r in gt["rows"] if r["analyte_key"] in ("wbc", "platelets", "rbc")]
        if lay.amb_counts:
            amb_seen += 1
            assert "(x10" not in text  # no header unit anywhere
            for r in counts:  # plan 3.8: no unit, no range, no header unit -> UNKNOWN, needs review
                assert r["unit"] == "" and r["unit_placement"] == "none" and r["range"]["kind"] == "none"
                assert r["flag"] == "UNKNOWN" and r["ambiguity"] == ["AMB_UNITLESS_COUNT"]
                assert r["expected_needs_review"] and r["row_id"] in gt["expected"]["needs_review_min_set"]
        else:
            hdr_seen += 1
            assert re.search(r"HAEMATOLOGY \(x10\^(3/uL|9/L)\)", text)
            for r in counts:
                if r["analyte_key"] in ("wbc", "platelets"):
                    assert r["unit_placement"] == "header" and r["unit"] in ("10^3/uL", "10^9/L")
                    assert r["unit"] not in r["line_text"] and r["flag"] != "UNKNOWN"
                    assert (r["unit"] == "10^9/L") == (gt["unit_system"] == "SI" or "10^9/L" in text)
    assert amb_seen >= 3 and hdr_seen >= 20
    rate = sum(tpl.setup(tpl.SPECS["IN-E"], split="dev", index=i, target_pages=1)[1].amb_counts
               for i in range(400)) / 400
    assert 0.09 <= rate <= 0.21  # plan 3.8: 15% of count rows


# ---- UK-A

HAND_SI_UNITS = {  # plan 3.4 / UK practice: SI units
    "sodium": "mmol/L", "potassium": "mmol/L", "hemoglobin": "g/L", "creatinine": "µmol/L",
    "glucose_fasting": "mmol/L", "cholesterol": "mmol/L", "hba1c": "mmol/mol", "urea": "mmol/L",
}


def test_t06_uka_si_units_normal_range_and_bracket_styles(t06_reports):
    checked = 0
    for (fam, n), (rep, gt, path) in t06_reports.items():
        if fam != "UK-A":
            continue
        txt = "\n".join(pg.get_text() for pg in fitz.open(str(path)))
        assert gt["unit_system"] == "SI" and "Normal range" in txt and "Bio. Ref" not in txt
        for r in gt["rows"]:
            if r["analyte_key"] in HAND_SI_UNITS:
                assert r["unit"] == HAND_SI_UNITS[r["analyte_key"]], (n, r["analyte_key"], r["unit"])
                checked += 1
            u = r["unit"]
            assert r["unit_placement"] == ("inline" if u else "none") and not re.search(r"mg/dL", u)
            if not u:
                continue
            if "unit_in_brackets" in r["features"]:
                assert f"({u})" in r["line_text"] or f"[{u}]" in r["line_text"], r["line_text"]
            else:
                assert f"{r['value_text']} {u}" in r["line_text"], r["line_text"]
    assert checked >= 5


def test_t06_uka_range_syntaxes_and_three_unit_styles_occur():
    styles = {tpl.setup(tpl.SPECS["UK-A"], split="dev", index=i, target_pages=1)[1].uk_style for i in range(30)}
    assert styles == {0, 1, 2}
    pt = _t05_patients()[0]
    seen = set()
    for i in range(30):
        lay = tpl.setup(tpl.SPECS["UK-A"], split="dev", index=i, target_pages=1)[1]
        rep = tpl.build_report("UK-A", split="dev", index=i, patient=pt, scenario_class="mild", target_pages=1)
        for r in rep.rows:
            t = " ".join(r.range_lines)
            if r.printed_range.kind == "two_sided":
                assert re.match(r"^(?:\(\d[\d.]* - \d[\d.]*\)|\[\d[\d.]* - \d[\d.]*\]|\d[\d.]* - \d[\d.]*)", t), t
            if lay.uk_style == 2 and r.unit:  # unit in brackets inside the Normal range cell
                assert t.endswith(f"[{r.unit}]"), (t, r.unit)
        seen.add(lay.uk_style)
    assert seen == {0, 1, 2}


# ---- GEN-CSV

CSV_DELIM = {"comma_basic": ",", "semicolon_decimal_comma": ";", "tab_split": "\t", "comma_result_unit_split": ",",
             "semicolon_result_unit": ";"}


def _csv_dialect(rep):
    (name,) = [f[len("csv_dialect_"):] for f in rep.features if f.startswith("csv_dialect_")]
    return name


def test_t06_csv_rows_parse_back_to_gt(t06_reports):
    for (fam, n), (rep, gt, path) in t06_reports.items():
        if fam != "GEN-CSV":
            continue
        assert len(gt["pages"]) == 1 and rep.n_pages == 1
        _check_csv(rep, gt, path.read_text(encoding="utf-8"))


def _check_csv(rep, gt, text):
    name = _csv_dialect(rep)
    delim = CSV_DELIM[name]
    lines = text.split("\n")
    assert lines[-1] == ""
    lines = lines[:-1]
    meta = [d for d in gt["decoys"] if d["kind"] == "csv_metadata"]
    assert 2 <= len(meta) <= 4 and len(meta) == len(gt["decoys"])
    for d in meta:
        assert lines[int(d["bbox"][1])] == d["text"]
    hdr_i = len(meta)
    header = next(_csv.reader([lines[hdr_i]], delimiter=delim))
    assert ("Result (unit)" in header) == name.endswith(("result_unit", "result_unit_split"))
    assert any(h in ("Ref Low", "RefLow") for h in header) == ("split" in name)
    assert (delim == ";") == name.startswith("semicolon")
    assert len(gt["rows"]) == len(lines) - hdr_i - 1
    for r in gt["rows"]:
        raw = lines[int(r["bbox"][1])]
        assert r["bbox"][3] == r["bbox"][1] + 1 and r["page"] == 1
        cells = next(_csv.reader([raw], delimiter=delim))
        assert len(cells) == len(header) and " | ".join(cells) == r["line_text"]
        res = cells[1]
        val = f"{r['qualifier']} {r['value_text']}" if r["qualifier"] else r["value_text"]
        combined = name.endswith(("result_unit", "result_unit_split"))
        assert res == (f"{val} {r['unit']}".strip() if combined else val), (name, res, val)
        if "semicolon" in name:
            assert "." not in r["value_text"] and float(r["value_text"].replace(",", ".")) == r["value"]
        else:
            assert float(r["value_text"]) == r["value"]
        if "result_unit" in name:
            assert res.endswith(r["unit"]) and r["unit_placement"] in ("inline", "none")
        else:
            assert cells[2] == r["unit"] and r["unit_placement"] in ("cell", "none")
        if "split" in name and r["range"]["kind"] == "two_sided":
            lo, hi = cells[-3], cells[-2]  # [..., low, high, flag]
            assert (float(lo), float(hi)) == (r["range"]["low"], r["range"]["high"])
        if name == "semicolon_result_unit":
            assert r["printed_flag"] == ""
        if r["printed_flag"]:
            assert cells[-1] == r["printed_flag"] and r["printed_flag"] in ("H", "L", "HH", "LL")


def test_t06_csv_dialects_cover_all_five_and_three_delimiters():
    pt = _t05_patients()[0]
    seen = set()
    for i in range(40):
        rep = tpl.build_report("GEN-CSV", split="dev", index=i, patient=pt, scenario_class="severe", target_pages=3)
        seen.add(_csv_dialect(rep))
        _check_csv(rep, lbase.build_gt(rep).to_dict(), _csvx.csv_text(rep))
    assert seen == set(CSV_DELIM) and {CSV_DELIM[s] for s in seen} == {",", ";", "\t"}


def test_t06_csv_has_no_native_pdf_and_roundtrips_utf8(tmp_path):
    rep = _t06_build("GEN-CSV", 1, _t05_patients()[0], "mild", 1)
    with pytest.raises(ValueError):
        tpl.native_pdf(rep, str(tmp_path / "x.pdf"))
    p = tmp_path / "r.csv"
    _csvx.write_csv(rep, str(p))
    assert p.read_text(encoding="utf-8") == _csvx.csv_text(rep) == rep.all_text() + "\n"
    assert list(_csv.reader(_io.StringIO(_csvx.csv_text(rep))))  # parses as CSV text
