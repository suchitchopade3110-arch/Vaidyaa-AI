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
