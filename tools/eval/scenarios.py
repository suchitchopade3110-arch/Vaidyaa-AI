"""Patients, scenario classes and value sampling for the eval corpus (P1 plan sections 3.3, 3.6).

GT-side module: no import from ``app``. Randomness comes only from the ``random.Random``
instances passed in (seeded via ``config.seed_for``); nothing here reads time or global state.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from tools.eval import config as cfg
from tools.eval import critical_table as ct
from tools.eval import units
from tools.eval.catalogue import CATALOGUE, Analyte, Rng, paediatric_band

STATES = ("normal", "mild", "severe", "critical")
MAX_TRIES = 50
INF = math.inf

# ---------------------------------------------------------------- allocation helpers

def allocate_counts(n: int, shares: dict[str, float]) -> dict[str, int]:
    """Largest-remainder allocation of n items over shares (ties broken by key order), exact total n."""
    raw = {k: n * v for k, v in shares.items()}
    out = {k: int(math.floor(x)) for k, x in raw.items()}
    rest = n - sum(out.values())
    order = sorted(raw, key=lambda k: (-(raw[k] - out[k]), list(shares).index(k)))
    for k in order[:rest]:
        out[k] += 1
    return out


def allocate_scenario_classes(n: int, rng: random.Random) -> list[str]:
    """Exact stratified scenario classes for n reports, shuffled with ``rng``."""
    counts = allocate_counts(n, cfg.SCENARIO_SHARES)
    out = [k for k, c in counts.items() for _ in range(c)]
    rng.shuffle(out)
    return out


# ---------------------------------------------------------------- patients

@dataclass(frozen=True)
class Patient:
    name: str
    patient_id: str
    sex_true: str  # 'male' | 'female'
    age_true: int
    input_sex: str | None
    input_age: int | None
    age_group: str  # paediatric | adult | elderly

    @property
    def is_paediatric(self) -> bool:
        return self.age_true < 18

    @property
    def sex_code(self) -> str:
        return "M" if self.sex_true == "male" else "F"


def build_patients(n: int, rng: random.Random) -> list[Patient]:
    """n patients with exact (stratified) demographic and age-group mixes (section 3.3)."""
    completeness = allocate_counts(n, cfg.DEMOGRAPHIC_SHARES)
    comp = [k for k, c in completeness.items() for _ in range(c)]
    rng.shuffle(comp)
    age_known = [i for i, c in enumerate(comp) if c in ("both", "no_sex")]
    groups_n = allocate_counts(len(age_known), cfg.AGE_GROUP_SHARES)
    groups = [g for g, c in groups_n.items() for _ in range(c)]
    rng.shuffle(groups)
    group_of = dict(zip(age_known, groups, strict=True))
    sexes = ["male"] * (n // 2) + ["female"] * (n - n // 2)
    rng.shuffle(sexes)
    out: list[Patient] = []
    for i in range(n):
        sex = sexes[i]
        group = group_of.get(i, "adult")
        lo, hi = cfg.AGE_RANGES[group] if i in group_of else cfg.AGE_UNKNOWN_RANGE
        age = rng.randint(lo, hi)
        first = rng.choice(cfg.FIRST_NAMES_M if sex == "male" else cfg.FIRST_NAMES_F)
        c = comp[i]
        out.append(Patient(
            name=f"{first} {rng.choice(cfg.SURNAMES)}",
            patient_id=f"PID-{rng.randint(100000, 999999)}",
            sex_true=sex, age_true=age,
            input_sex=None if c in ("no_sex", "neither") else sex,
            input_age=None if c in ("no_age", "neither") else age,
            age_group=group,
        ))
    return out


# ---------------------------------------------------------------- printed ranges

def printed_range_for(a: Analyte, variant: int, sex_code: str, age: int) -> tuple[Rng, str] | None:
    """The numeric range a lab prints for this patient: (range, unit) in the variant's own unit.

    Paediatric patients get Table P bands (age 2-17); analytes without a band fall back to the adult variant.
    Returns None when the analyte has no numeric range (qualitative) or is not reported for this sex (PSA).
    """
    if not a.refs:
        return None
    if a.sex_only and sex_code != a.sex_only:
        return None
    if age < 18 and a.ped:
        v = a.ped[paediatric_band(age)]
    else:
        v = a.refs[variant % len(a.refs)]
    r = v.unisex if not v.sex_specific else (v.male if sex_code == "M" else v.female)
    return r, (v.unit or a.unit)


# ---------------------------------------------------------------- value sampling (section 3.6)

@dataclass(frozen=True)
class Sampled:
    key: str
    state: str
    direction: str | None  # 'high' | 'low' | None
    value: Decimal  # as printed
    text: str
    unit: str
    canonical: float


def round_half_up(x: float, decimals: int) -> Decimal:
    return Decimal(repr(x)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)


def _bounds_canonical(key: str, r: Rng, unit: str) -> tuple[float | None, float | None]:
    lo = units.to_canonical(key, r.low, unit)[0] if r.low is not None else None
    hi = units.to_canonical(key, r.high, unit)[0] if r.high is not None else None
    return lo, hi


def _consensus_all_variants(a: Analyte) -> tuple[float, float, list[Rng]]:
    """Furthest-out bounds across every catalogue variant (canonical units) for range-absent rows."""
    his, los, all_r = [], [], []
    for v in a.refs:
        for r in ((v.unisex,) if not v.sex_specific else (v.male, v.female)):
            if r is None:
                continue
            lo, hi = _bounds_canonical(a.key, r, v.unit or a.unit)
            if hi is not None:
                his.append(hi)
            if lo is not None:
                los.append(lo)
            all_r.append(r)
    return (max(his) if his else INF), (min(los) if los else -INF), all_r


def _zone(key: str, printed_upper: float | None) -> tuple[float, float]:
    """Open non-critical zone (lo, hi) in canonical units: values must lie strictly inside."""
    e = ct.CRITICAL.get(key)
    lo, hi = -INF, INF
    if e is not None:
        if e.low_guard is not None:
            lo = e.low_guard[1]
        if e.high_guard is not None:
            hi = e.high_guard[0] if e.high_guard[0] is not None else (
                printed_upper if printed_upper is not None else ct.guard_bands(key)[-1][0])
    return lo, hi


# Analytes on a logarithmic scale, where "3-25% beyond the bound" is meaningless (pH 7.35 x 0.97 = 7.13 is already
# critical): mild and severe are additive offsets beyond the printed bound, still clipped by the Table C guards.
# pH 7.30-7.34 (mild) and 7.26-7.29 (severe) are non-critical acidaemia, 7.46-7.54 alkalaemia (P1 review MED-4).
ADDITIVE_STATES: dict[str, dict[str, tuple[float, float]]] = {
    "blood_ph": {"mild": (0.01, 0.05), "severe": (0.05, 0.10)},
}


def _intervals(key, state, direction, lo, hi, *, absent_hi=None, absent_lo=None, kind="two_sided"):
    """Candidate canonical interval for (state, direction) relative to printed bounds lo/hi (None = absent)."""
    if key in ADDITIVE_STATES and absent_hi is None and absent_lo is None:
        a, b = ADDITIVE_STATES[key][state]
        if direction == "high" and hi is not None:
            return (hi + a, hi + b)
        if direction == "low" and lo is not None:
            return (lo - b, lo - a)
        return None
    if absent_hi is not None or absent_lo is not None:  # range-absent: clear margins around ALL variants
        H, Lo = absent_hi, absent_lo
        if state == "normal":
            return None  # handled by caller (middle 60%)
        if direction == "high" and H not in (None, INF):
            return (1.15 * H, 1.30 * H) if state == "mild" else (1.30 * H, 3.0 * H)
        if direction == "low" and Lo not in (None, -INF) and Lo > 0:
            return (0.70 * Lo, 0.85 * Lo) if state == "mild" else (0.25 * Lo, 0.70 * Lo)
        return None
    if direction == "high":
        if hi is None:
            return None
        return (1.03 * hi, 1.25 * hi) if state == "mild" else (1.25 * hi, 3.0 * hi)
    if direction == "low":
        if lo is None or lo <= 0:
            return None
        return (0.80 * lo, 0.97 * lo) if state == "mild" else (0.25 * lo, 0.80 * lo)
    return None


def _normal_interval(lo, hi, kind, middle60=False):
    if lo is not None and hi is not None:
        w = hi - lo
        m = 0.2 if middle60 else 0.1
        return lo + m * w, hi - m * w
    if hi is not None:  # '< b'
        return (0.2 * hi, 0.8 * hi) if middle60 else (0.4 * hi, 0.9 * hi)
    return (1.3 * lo, 2.0 * lo) if middle60 else (1.15 * lo, 2.0 * lo)  # '> a'


def _clip(iv, zone, plaus, cap):
    lo, hi = iv
    lo, hi = max(lo, zone[0]), min(hi, zone[1])
    if plaus is not None:
        lo, hi = max(lo, plaus[0]), min(hi, plaus[1])
    if cap is not None:
        if cap[0] is not None:
            lo = max(lo, cap[0])
        if cap[1] is not None:
            hi = min(hi, cap[1])
    return (lo, hi) if lo < hi else None


def _step(decimals: int) -> float:
    return 10.0 ** (-decimals)


def invariants_ok(
    key: str, value_printed: Decimal, unit: str, printed_bounds: list[float], *, check_zone: bool = True,
    printed_upper_canon: float | None = None,
) -> bool:
    """Section 3.6 hard invariants on a printed value: not in a guard band, at least one display step from every
    printed bound, within plausibility and caps."""
    a = CATALOGUE[key]
    v = float(value_printed)
    c, _ = units.to_canonical(key, v, unit)
    if ct.in_guard(key, c, printed_upper_canon):
        return False
    if a.plaus is not None and not (a.plaus[0] <= c <= a.plaus[1]):
        return False
    if ct.violates_cap(key, c):
        return False
    step = _step(units.decimals_for(key, unit))
    return all(abs(v - b) >= step - 1e-9 for b in printed_bounds)


def sample_value(
    key: str, state: str, rng: random.Random, *, printed: Rng | None, unit: str | None = None,
    direction: str | None = None, sex_code: str = "M", range_absent: bool = False,
) -> Sampled | None:
    """One value for ``key`` in ``state`` (normal/mild/severe/critical) relative to ``printed`` (in ``unit``).

    ``range_absent`` samples against the consensus interval with clear margins. Returns None when the state
    cannot be produced (guard bands, caps or plausibility leave no room) after 50 tries.
    """
    a = CATALOGUE[key]
    unit = unit or a.unit
    dec = units.decimals_for(key, unit)
    if range_absent or printed is None:
        v0 = a.consensus
        base = v0.unisex if not v0.sex_specific else (v0.male if sex_code == "M" else v0.female)
        if base is None:
            return None
        blo, bhi = _bounds_canonical(key, base, v0.unit or a.unit)
        absent_hi, absent_lo, variants = _consensus_all_variants(a)
        pb = [b for r in variants for b in (r.low, r.high) if b is not None]
        printed_bounds = [units.from_canonical(key, units.to_canonical(key, b, a.unit)[0], unit) for b in pb]
        kind = base.kind
    else:
        blo, bhi = _bounds_canonical(key, printed, unit)
        absent_hi = absent_lo = None
        printed_bounds = [b for b in (printed.low, printed.high) if b is not None]
        kind = printed.kind
    printed_upper = bhi
    zone = _zone(key, printed_upper)
    cap = ct.CAPS.get(key)
    e = ct.CRITICAL.get(key)

    dirs: list[str | None]
    if state == "normal":
        dirs = [None]
    elif direction is not None:
        dirs = [direction]
    else:
        dirs = [d for d, ok in (("high", bhi is not None), ("low", blo is not None and blo > 0)) if ok]
        rng.shuffle(dirs)
    for d in dirs:
        for _ in range(MAX_TRIES):
            if state == "critical":
                if e is None:
                    return None
                emit = e.crit_high if d == "high" else e.crit_low
                if emit is None:
                    break
                iv = (emit[0], emit[1])
                iv = _clip(iv, (-INF, INF), a.plaus, cap)
            elif state == "normal":
                iv = _normal_interval(blo, bhi, kind, middle60=range_absent or printed is None)
                iv = _clip(iv, zone, a.plaus, cap)
            else:
                raw = _intervals(key, state, d, blo, bhi, absent_hi=absent_hi if (range_absent or printed is None) else None,
                                 absent_lo=absent_lo if (range_absent or printed is None) else None)
                iv = _clip(raw, zone, a.plaus, cap) if raw else None
            if iv is None:
                break
            c = rng.uniform(*iv)
            vp = units.from_canonical(key, c, unit)
            dp = round_half_up(vp, dec)
            c2 = units.to_canonical(key, float(dp), unit)[0]
            if not invariants_ok(key, dp, unit, printed_bounds, printed_upper_canon=printed_upper):
                continue
            if not _state_ok(key, state, d, c2, blo, bhi, zone, e):
                continue
            return Sampled(key, state, d, dp, format(dp, "f"), unit, c2)
    return None


def _state_ok(key, state, d, c, blo, bhi, zone, e) -> bool:
    """After rounding, the value must still be in the intended region."""
    if state == "critical":
        return ct.critical_direction(key, c) == d
    if ct.critical_direction(key, c) is not None:
        return False
    lo_ok = blo is None or c > blo
    hi_ok = bhi is None or c < bhi
    if state == "normal":
        return lo_ok and hi_ok
    return (c > bhi) if d == "high" else (c < blo)


# ---------------------------------------------------------------- derived values (section 3.6)

def _r(x: float, key: str) -> float:
    return float(round_half_up(x, CATALOGUE[key].decimals))


def indirect_bilirubin(total: float, direct: float) -> float:
    return _r(total - direct, "bilirubin_indirect")


def globulin(tp: float, alb: float) -> float:
    return _r(tp - alb, "globulin")


def ag_ratio(alb: float, glob: float) -> float:
    return _r(alb / glob, "ag_ratio")


def vldl(tg: float) -> float:
    return _r(tg / 5, "vldl")


def ldl_friedewald(tc: float, hdl: float, tg: float) -> float | None:
    """None when TG >= 400 (the report prints 'Not calculated' and no LDL row)."""
    if tg >= 400:
        return None
    return _r(tc - hdl - tg / 5, "ldl")


def non_hdl(tc: float, hdl: float) -> float:
    return _r(tc - hdl, "non_hdl")


def chol_hdl_ratio(tc: float, hdl: float) -> float:
    return _r(tc / hdl, "chol_hdl_ratio")


def ldl_hdl_ratio(ldl: float, hdl: float) -> float:
    return _r(ldl / hdl, "ldl_hdl_ratio")


def mchc(hb: float, hct: float) -> float:
    return _r(hb / hct * 100, "mchc")


def mch(hb: float, rbc: float) -> float:
    return _r(hb * 10 / rbc, "mch")


def mcv(hct: float, rbc: float) -> float:
    return _r(hct * 10 / rbc, "mcv")


def hct_from(hb: float, mchc_value: float = 33.0) -> float:
    """Haematocrit consistent with Hb and a typical MCHC; the row is dropped when it falls outside the cap."""
    return _r(hb / mchc_value * 100, "hematocrit")


def abs_count(wbc: float, pct: float) -> float:
    return float(round_half_up(wbc * pct / 100, 2))


def egfr_ckd_epi_2021(creat_mg_dl: float, age: int, sex_code: str) -> float:
    female = sex_code == "F"
    kappa, alpha = (0.7, -0.241) if female else (0.9, -0.302)
    ratio = creat_mg_dl / kappa
    v = 142 * min(ratio, 1) ** alpha * max(ratio, 1) ** -1.200 * 0.9938 ** age * (1.012 if female else 1.0)
    return float(round_half_up(v, 0))


# ---------------------------------------------------------------- scenarios and clusters (section 3.3)

# cluster -> ((key, direction), ...) used to bias analyte choice
CLUSTERS: dict[str, tuple[tuple[str, str], ...]] = {
    "anaemia": (("hemoglobin", "low"), ("hematocrit", "low"), ("mcv", "low"), ("ferritin", "low"), ("rbc", "low")),
    "sepsis_infection": (("wbc", "high"), ("neutrophils_pct", "high"), ("crp", "high"), ("procalcitonin", "high")),
    "dengue": (("platelets", "low"), ("wbc", "low"), ("hematocrit", "high"), ("ast", "high"), ("alt", "high")),
    "aki_ckd": (("creatinine", "high"), ("urea", "high"), ("potassium", "high"), ("egfr", "low"), ("bicarbonate", "low")),
    "dka": (("glucose_random", "high"), ("bicarbonate", "low"), ("blood_ph", "low"), ("potassium", "high")),
    "hyponatraemia": (("sodium", "low"), ("urine_sodium", "high")),
    "hypokalaemia": (("potassium", "low"), ("magnesium", "low")),
    "hepatitis": (("alt", "high"), ("ast", "high"), ("bilirubin_total", "high"), ("alp", "high")),
    "cholestasis": (("alp", "high"), ("ggt", "high"), ("bilirubin_total", "high"), ("bilirubin_direct", "high")),
    "hypothyroid": (("tsh", "high"), ("free_t4", "low"), ("t3", "low")),
    "hyperthyroid": (("tsh", "low"), ("free_t4", "high"), ("t3", "high")),
    "diabetes": (("glucose_fasting", "high"), ("hba1c", "high"), ("glucose_pp", "high")),
    "dyslipidaemia": (("cholesterol", "high"), ("triglycerides", "high"), ("ldl", "high"), ("hdl", "low")),
    "vitamin_deficiency": (("vitamin_d", "low"), ("vitamin_b12", "low"), ("folate", "low")),
    "warfarin": (("inr", "high"), ("pt", "high")),
    "respiratory_acidosis": (("blood_ph", "low"), ("pco2", "high"), ("po2", "low"), ("bicarbonate", "high")),
    "uti": (("urine_pus_cells", "high"), ("wbc", "high")),
    "meningitis": (("csf_wbc", "high"), ("csf_protein", "high"), ("csf_glucose", "low")),
    "hypocalcaemia": (("calcium", "low"), ("phosphorus", "high"), ("magnesium", "low")),
    "hypercalcaemia": (("calcium", "high"), ("phosphorus", "low")),
    "thrombocytosis": (("platelets", "high"), ("ferritin", "high")),
}


# Critical-coverage strata (P1 review MED-4). Before them pH, pCO2, pO2, ionised calcium, hs-troponin T and the
# absolute neutrophil count were never critical in the corpus (troponin I only by chance), and no critical count was ever printed unit-less, so
# the eval could not see the engine's missing pCO2/pO2 limits (M2) or the S6 case "WBC 0.8, no unit". A uniform
# draw over a report's analytes rarely picks an analyte of one optional panel, so ``generate.plan_split`` deals these
# strata over the critical-class reports of the shared families that can print them (balanced per split, at most
# ``COVERAGE_ROUNDS`` per stratum and split) and the family builder makes the stratum the report's FIRST critical
# target (``plan_scenario(prefer_critical=...)``). Values still come from Table C's emitted-critical ranges.
COVERAGE_PREFER: dict[str, tuple[tuple[str, str | None], ...]] = {
    "blood_ph": (("blood_ph", None),),
    "pco2": (("pco2", None),),
    "po2": (("po2", None),),
    "ionized_calcium": (("ionized_calcium", None),),
    "hs_troponin_t": (("hs_troponin_t", None),),
    "troponin_i": (("troponin_i", None),),  # the other optional cardiac panel
    "neutrophils_abs": (("wbc", "low"),),  # with printed absolute counts a critical low WBC gives a critical ANC
    "count_unitless": (("wbc", None), ("platelets", None)),  # a critical count printed with no unit (S6, CL-27)
}
COVERAGE_KEYS = tuple(COVERAGE_PREFER)
COVERAGE_ROUNDS = 2


@dataclass(frozen=True)
class Target:
    key: str
    state: str
    direction: str | None


@dataclass
class ScenarioSpec:
    cls: str
    clusters: list[str] = field(default_factory=list)
    targets: list[Target] = field(default_factory=list)


def eligible_keys(patient: Patient, candidates: list[str] | None = None) -> list[str]:
    """Numeric analytes that can carry an abnormal value for this patient (sorted for determinism)."""
    keys = sorted(candidates if candidates is not None else CATALOGUE)
    out = []
    for k in keys:
        a = CATALOGUE[k]
        if a.value_type != "numeric" or not a.refs or k == "egfr":
            continue
        if a.sex_only and a.sex_only != patient.sex_code:
            continue
        if patient.is_paediatric and not set(a.panels) & set(cfg.PAEDIATRIC_PANELS):
            continue
        out.append(k)
    return out


def _feasible(key: str, state: str, direction: str | None, patient: Patient) -> bool:
    a = CATALOGUE[key]
    pr = printed_range_for(a, 0, patient.sex_code, patient.age_true)
    if pr is None:
        return False
    probe = random.Random(12345)
    return any(
        sample_value(key, state, probe, printed=pr[0], unit=pr[1], direction=direction, sex_code=patient.sex_code)
        for _ in range(3)
    )


def plan_scenario(cls: str, rng: random.Random, patient: Patient, candidates: list[str] | None = None, *,
                  prefer_critical: tuple[tuple[str, str | None], ...] = ()) -> ScenarioSpec:
    """Choose clusters and per-analyte target states for a scenario class (section 3.3).

    ``prefer_critical`` ((key, direction) pairs, a coverage stratum from ``COVERAGE_PREFER``) is tried first for the
    first critical slot. No extra random draw is made when it is empty, so other reports are unchanged."""
    if cls not in cfg.SCENARIO_SHARES:
        raise ValueError(cls)
    spec = ScenarioSpec(cls)
    if cls == "normal":
        return spec
    keys = eligible_keys(patient, candidates)
    spec.clusters = sorted(rng.sample(sorted(CLUSTERS), rng.randint(0, 2)))
    slots: list[str] = {
        "mild": ["mild"] * rng.randint(1, 3),
        "severe": ["severe"] * rng.randint(1, 2) + ["mild"] * rng.randint(0, 3),
        "critical": ["critical"] * rng.randint(1, 3) + [rng.choice(["severe", "mild"]) for _ in range(rng.randint(0, 3))],
    }[cls]
    biased = [(k, d) for c in spec.clusters for (k, d) in CLUSTERS[c] if k in keys]
    prefer = [(k, d) for k, d in prefer_critical if k in keys]
    used: set[str] = set()
    for state in slots:
        pool = [(k, d) for k, d in biased if k not in used]
        rng.shuffle(pool)
        pool += [(k, None) for k in rng.sample(keys, len(keys)) if k not in used]
        if state == "critical" and prefer:  # the coverage stratum leads the first critical slot only
            rng.shuffle(prefer)
            pool = prefer + pool
            prefer = []
        for k, d in pool:
            if k in used:
                continue
            if _feasible(k, state, d, patient):
                spec.targets.append(Target(k, state, d))
                used.add(k)
                break
    return spec


@dataclass(frozen=True)
class Drawn:
    key: str
    state: str
    sampled: Sampled
    printed: Rng
    variant: int


def draw_values(patient: Patient, spec: ScenarioSpec, rng: random.Random, keys: list[str],
                lab_variant: int = 0) -> list[Drawn]:
    """Sample one value per key: targets take their planned state, everything else is normal.

    Rows that cannot satisfy the invariants after 50 tries are dropped by the caller's choice of keys
    (this function simply omits them); ``verify`` counts drops against the 0.5% budget.
    """
    want = {t.key: t for t in spec.targets}
    out: list[Drawn] = []
    for k in keys:
        a = CATALOGUE[k]
        pr = printed_range_for(a, lab_variant, patient.sex_code, patient.age_true)
        if pr is None:
            continue
        t = want.get(k)
        s = sample_value(k, t.state if t else "normal", rng, printed=pr[0], unit=pr[1],
                         direction=t.direction if t else None, sex_code=patient.sex_code)
        if s is not None:
            out.append(Drawn(k, s.state, s, pr[0], lab_variant))
    return out
