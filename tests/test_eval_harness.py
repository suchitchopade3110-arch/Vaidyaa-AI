"""Gate, heldout hygiene, determinism, sampling, aggregate, errors and report (P1 plan 6.9 to 6.12, 11: HA-05..HA-11).

The harness is driven with a FakeAdapter that rebuilds the extraction from the GT file next to each job's input
(no OCR, no product import), so only the harness itself is under test. Gate expectations are worked out by hand from
the plan's tolerance rules and the PRD section 3 target values, not from harness output.
"""

from __future__ import annotations

import json
import logging
import math
import pathlib
import re
import shutil
import subprocess
import sys
from collections import Counter

import pytest

from tools.eval import config as cfg
from tools.eval import gate, hygiene, report, run
from tools.eval import generate as gen
from tools.eval import metrics as me
from tools.eval import targets as tg
from tools.eval.adapters.base import ExtractedRow, ExtractionResult

ROOT = pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- fakes

class FakeAdapter:
    """Reads the GT beside the job's file and 'extracts' it back (needs_review where the GT is ambiguous)."""

    name = "fake_adapter"
    version = "9"

    def __init__(self, raise_on: str | None = None, chatter: str = "") -> None:
        self.raise_on = raise_on
        self.chatter = chatter
        self.calls = 0

    def extract(self, job):
        self.calls += 1
        gt = json.loads((pathlib.Path(job.files[0]).parents[1] / "gt" / f"{job.report_id}.json").read_text())
        if self.chatter:
            print(self.chatter)
            print(self.chatter, file=sys.stderr)
            logging.getLogger("app.pipeline").warning(self.chatter)
        if self.raise_on and self.calls == 2:
            raise RuntimeError(f"cannot parse {self.raise_on}")
        rows = []
        for i, g in enumerate(r for r in gt["rows"] if not job.pages or r["page"] in job.pages):
            rng = g["effective_range_page"] or {}
            rows.append(ExtractedRow(
                row_index=i, name_raw=g["name"], analyte_key=g["analyte_key"], value_raw=g["value_text"],
                value_num=g["value"], qualifier=g["qualifier"], text_value=g["text_value"], unit_raw=g["unit"],
                ref_low=rng.get("low"), ref_high=rng.get("high"), printed_flag=g["printed_flag"] or None,
                specimen=g["specimen"], flag=g["flag"], needs_review=g["expected_needs_review"],
                source_page=g["page"], source_text=g["line_text"]))
        return ExtractionResult(job.job_id, "ok", rows, pre_review_headline=gt["expected"]["headline"],
                                raw_text=" ".join(g["value_text"] for g in gt["rows"]))


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("corpus")
    gen.materialize("test", "native", root=root)
    gen.materialize("heldout", "native", root=root)
    return root


def argv(corpus_root, out, *extra):
    return ["--corpus-dir", str(corpus_root), "--out", str(out), "--baseline", str(out / "baseline.json"),
            *extra]


def go(corpus_root, out, *extra, adapter=None):
    from tools.eval.clinical import ClinicalRulesScorer

    return run.main(argv(corpus_root, out, *extra), adapter=adapter or FakeAdapter(), scorer=ClinicalRulesScorer())


def det(path: pathlib.Path) -> dict:
    d = json.loads(path.read_text())
    d.pop("runtime")
    return d


# ---------------------------------------------------------------- gate fixtures

def cell(m1=(0, 1000), m2=(100, 100), m3=(950, 1000), m4=(0, 1000), m5=(99, 100), m6=(9, 10), m7=(10, 100),
         crashes=None, m2p=None, jobs=1, m4p=(0, 1000)):
    c = {"M1": me.rate(*m1), "M2": me.rate(*m2), "M2_post": me.rate(*(m2p or (100, 100))), "M3": me.rate(*m3),
         "M4": me.rate(*m4), "M4_post": me.rate(*m4p), "M5": me.rate(*m5), "M6": me.rate(*m6), "M7": me.rate(*m7),
         "components": {"M1": {"wrong": 0, "invented": 0, "omitted": 0}},
         "legibility": me.rate(1, 1), "jobs": jobs, "crashes": crashes or {}}
    return c


JUDGE = {"digest": "j" * 64, "modules": {"tools/eval/metrics.py": "m" * 64}}


def doc(cells, fp="f" * 64, profile="ci", adapter=("a", "1"), scorer=("s", "1"), judge=None, shard=None):
    return {"run": {"profile": profile, "samples": {"first": None}, "adapter": dict(zip(("name", "version"), adapter)),
                    "scorer": dict(zip(("name", "version"), scorer)), "judge": judge or JUDGE, "shard": shard},
            "corpus": {"test": {"fingerprint": fp}}, "results": {"test": cells}}


TOL = {"native": 0, "csv": 0, "scan": 1, "photo": 1, "rot90": 1}


def evaluate(mode, cur, base):
    return gate.evaluate(mode, cur, split="test", baseline=base, tolerance=TOL)


# ---------------------------------------------------------------- HA-05 gate

def test_ha05_ratchet_equal_to_baseline_passes():
    base = doc({"native": cell(), "scan": cell()})
    res = evaluate("ratchet", base, base)
    assert res["passed"] and gate.exit_code(res) == 0 and res["failures"] == []


def test_ha05_native_m2_numerator_drop_of_one_exits_1():
    base = doc({"native": cell()})
    cur = doc({"native": cell(m2=(99, 100))})
    res = evaluate("ratchet", cur, base)
    assert gate.exit_code(res) == 1
    assert [(f["rendering"], f["metric"]) for f in res["failures"]] == [("native", "M2")]


def test_ha05_image_regression_of_one_count_passes_and_two_fails():
    base = doc({"scan": cell(m3=(800, 1000))})
    one = evaluate("ratchet", doc({"scan": cell(m3=(799, 1000))}), base)
    two = evaluate("ratchet", doc({"scan": cell(m3=(798, 1000))}), base)
    assert one["passed"] and not two["passed"] and gate.exit_code(two) == 1
    # lower-is-better metrics regress upward: M1 +1 count passes, +2 fails (photo, tolerance 1)
    pbase = doc({"photo": cell(m1=(10, 1000))})
    assert evaluate("ratchet", doc({"photo": cell(m1=(11, 1000))}), pbase)["passed"]
    assert not evaluate("ratchet", doc({"photo": cell(m1=(12, 1000))}), pbase)["passed"]


# EV-05 (P2 plan 9.4, P1 review LOW-3): M2 and M2_post have zero ratchet tolerance on every rendering. The image
# tolerance of one count is for noisy extraction metrics; a critical lost is never noise (S3).
@pytest.mark.parametrize("rendering", ["scan", "photo", "rot90"])
def test_ev05_one_count_m2_regression_on_an_image_rendering_fails(rendering):
    base = doc({rendering: cell(m2=(36, 36))})
    res = evaluate("ratchet", doc({rendering: cell(m2=(35, 36))}), base)  # one of 36 criticals no longer recalled
    assert gate.exit_code(res) == 1
    assert [(f["rendering"], f["metric"], f["tolerance"]) for f in res["failures"]] == [(rendering, "M2", 0)]


@pytest.mark.parametrize("rendering", ["scan", "photo", "rot90"])
def test_ev05_one_count_m2_post_regression_on_an_image_rendering_fails(rendering):
    base = doc({rendering: cell(m2p=(36, 36))})
    res = evaluate("ratchet", doc({rendering: cell(m2p=(35, 36))}), base)
    assert gate.exit_code(res) == 1
    assert [(f["rendering"], f["metric"], f["tolerance"]) for f in res["failures"]] == [(rendering, "M2_post", 0)]


def test_ev05_other_metrics_keep_the_image_tolerance_and_m2_improvement_passes():
    base = doc({"scan": cell(m2=(35, 36), m3=(800, 1000))})
    assert evaluate("ratchet", doc({"scan": cell(m2=(36, 36), m3=(799, 1000))}), base)["passed"]


def test_ev05_a_config_can_never_shrink_the_zero_tolerance_set(tmp_path):
    cfgfile = tmp_path / "gate.json"
    cfgfile.write_text(json.dumps({"zero_tolerance": []}))
    assert gate.load_config(cfgfile)["zero_tolerance"] == ["M2", "M2_post"]
    cfgfile.write_text(json.dumps({"zero_tolerance": ["M5"]}))
    assert gate.load_config(cfgfile)["zero_tolerance"] == ["M2", "M2_post", "M5"]
    base = doc({"scan": cell(m2=(36, 36))})
    cur = doc({"scan": cell(m2=(35, 36))})
    # even a caller that passes an empty set gets the floor
    assert not gate.evaluate("ratchet", cur, split="test", baseline=base, tolerance=TOL, zero_tolerance=[])["passed"]


# EV-06 (P1 review LOW-1): M4_post, the false critical alerts after confirmation, is ratcheted like M4.
def test_ev06_m4_post_regression_fails_the_ratchet():
    base = doc({"native": cell(), "scan": cell()})
    res = evaluate("ratchet", doc({"native": cell(m4p=(1, 1000)), "scan": cell()}), base)
    assert gate.exit_code(res) == 1
    assert [(f["rendering"], f["metric"]) for f in res["failures"]] == [("native", "M4_post")]
    # lower is better and the image tolerance of one count still applies: +1 passes, +2 fails on scan
    assert evaluate("ratchet", doc({"native": cell(), "scan": cell(m4p=(1, 1000))}), base)["passed"]
    assert not evaluate("ratchet", doc({"native": cell(), "scan": cell(m4p=(2, 1000))}), base)["passed"]
    assert evaluate("ratchet", base, doc({"native": cell(m4p=(3, 1000)), "scan": cell()}))["passed"]  # improvement


def test_ev06_a_baseline_without_m4_post_is_refused_not_compared():
    old = doc({"native": cell()})
    del old["results"]["test"]["native"]["M4_post"]
    with pytest.raises(gate.GateConfigError):
        evaluate("ratchet", doc({"native": cell()}), old)


def test_ha05_native_has_no_tolerance_and_improvements_pass():
    base = doc({"native": cell(m3=(950, 1000), m1=(5, 1000))})
    assert not evaluate("ratchet", doc({"native": cell(m3=(949, 1000), m1=(5, 1000))}), base)["passed"]
    assert not evaluate("ratchet", doc({"native": cell(m3=(950, 1000), m1=(6, 1000))}), base)["passed"]
    assert evaluate("ratchet", doc({"native": cell(m3=(960, 1000), m1=(0, 1000))}), base)["passed"]


def test_ha05_m7_and_m6_are_target_only_so_a_rise_alone_passes_ratchet():
    base = doc({"native": cell(m7=(10, 100), m6=(9, 10))})
    cur = doc({"native": cell(m7=(40, 100), m6=(1, 10))})
    assert evaluate("ratchet", cur, base)["passed"]
    assert not evaluate("targets", cur, base)["passed"]  # but the targets (M7 <= 15%, M6 >= 90%) do fail it


def test_ha05_crash_count_may_not_rise():
    base = doc({"native": cell(crashes={"ValueError": 1})})
    assert evaluate("ratchet", doc({"native": cell(crashes={"KeyError": 1})}), base)["passed"]
    res = evaluate("ratchet", doc({"native": cell(crashes={"KeyError": 1, "ValueError": 1})}), base)
    assert not res["passed"] and res["failures"][0]["kind"] == "crashes"


def test_ha05_context_mismatch_exits_2_never_silently_reused():
    base = doc({"native": cell()})
    for bad in (doc({"native": cell()}, fp="0" * 64), doc({"native": cell()}, profile="dev"),
                doc({"native": cell()}, adapter=("a", "2")), doc({"native": cell()}, scorer=("other", "1"))):
        with pytest.raises(gate.GateConfigError):
            evaluate("ratchet", bad, base)
    with pytest.raises(gate.GateConfigError):
        evaluate("ratchet", base, None)  # no baseline
    with pytest.raises(gate.GateConfigError):
        evaluate("ratchet", doc({"native": cell(), "csv": cell()}), base)  # a cell the baseline never had


def test_ha05_ratchet_on_a_subset_of_the_baseline_cells_exits_2():
    """T16 (zoro): a ratchet run that measured fewer renderings than the baseline (e.g. ``--renderings native
    --gate ratchet``) would compare only what it ran and pass while scan/photo/rot90 regressed unseen."""
    base = doc({"native": cell(), "scan": cell(m2=(100, 100))})
    with pytest.raises(gate.GateConfigError, match="scan"):
        evaluate("ratchet", doc({"native": cell()}), base)
    with pytest.raises(gate.GateConfigError):
        evaluate("targets", doc({"native": cell()}), base)


@pytest.mark.parametrize("m1,passes", [((6, 1000), False), ((5, 1000), True)])
def test_ha05_targets_native_m1_half_percent(m1, passes):
    c = doc({"native": cell(m1=m1)})
    base = doc({"native": cell(m1=m1)})  # ratchet part equal; only the target can fail
    assert evaluate("targets", c, base)["passed"] is passes


def test_ha05_targets_m2_and_m7():
    base = doc({"native": cell(m2=(99, 100))})
    res = evaluate("targets", doc({"native": cell(m2=(99, 100))}), base)
    assert gate.exit_code(res) == 1 and [f["metric"] for f in res["failures"]] == ["M2"]
    assert evaluate("targets", doc({"native": cell()}), doc({"native": cell()}))["passed"]
    over = doc({"native": cell(m7=(16, 100))})
    r7 = evaluate("targets", over, over)
    assert gate.exit_code(r7) == 1 and [f["metric"] for f in r7["failures"]] == ["M7"]
    assert evaluate("targets", doc({"native": cell(m7=(15, 100))}), doc({"native": cell(m7=(15, 100))}))["passed"]


def test_ha05_none_and_report_modes_never_fail_and_need_no_baseline():
    bad = doc({"native": cell(m1=(900, 1000), m2=(0, 100))})
    for mode in ("none", "report"):
        res = gate.evaluate(mode, bad, split="test")
        assert res["passed"] and gate.exit_code(res) == 0


def test_ha05_cli_ratchet_end_to_end(corpus, tmp_path):
    out = tmp_path / "o"
    base_args = ("--splits", "test", "--sample-n", "4", "--renderings", "native")
    assert go(corpus, out, *base_args, "--gate", "ratchet") == 2  # no baseline: clear exit 2, nothing written
    assert not (out / "metrics.json").exists()
    assert go(corpus, out, *base_args, "--update-baseline") == 0
    recorded = json.loads((out / "baseline.json").read_text())
    assert "runtime" not in recorded and recorded["results"]["test"]["native"]["jobs"] == 4
    assert go(corpus, out, *base_args, "--gate", "ratchet") == 0
    recorded["corpus"]["test"]["fingerprint"] = "0" * 64
    (out / "baseline.json").write_text(json.dumps(recorded))
    assert go(corpus, out, *base_args, "--gate", "ratchet") == 2


def test_ha05_cli_ratchet_detects_a_regression(corpus, tmp_path):
    out = tmp_path / "o"
    args = ("--splits", "test", "--sample-n", "4", "--renderings", "native")
    assert go(corpus, out, *args, "--update-baseline") == 0
    b = json.loads((out / "baseline.json").read_text())
    c = b["results"]["test"]["native"]["M3"]
    c["num"] = c["den"] + 5  # a baseline better than anything achievable: the run regresses by 5 counts
    c["value"] = c["num"] / c["den"]
    (out / "baseline.json").write_text(json.dumps(b))
    assert go(corpus, out, *args, "--gate", "ratchet") == 1


# ---------------------------------------------------------------- HA-06 heldout guard

def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _all_keys(v)


WHITELIST = {"M1", "M2", "M2_post", "M3", "M4", "M4_post", "M5", "M6", "M7", "num", "den", "value", "ci_low", "ci_high", "target", "pass",
             "components", "wrong", "invented", "omitted", "legibility", "jobs", "crashes", "RuntimeError"}


def test_ha06_dump_failures_with_heldout_exits_3_and_creates_nothing(corpus, tmp_path):
    dump = tmp_path / "X"
    assert go(corpus, tmp_path / "o", "--splits", "heldout", "--dump-failures", str(dump)) == 3
    assert not dump.exists() and not (tmp_path / "o").exists()
    assert go(corpus, tmp_path / "o", "--profile", "full", "--dump-failures", str(dump)) == 3


def test_ha06_heldout_run_is_aggregate_only_and_quiet(corpus, tmp_path, capfd):
    out = tmp_path / "o"
    names = set()
    for rid in sorted(json.loads((corpus / "heldout/manifest.json").read_text())["reports"], key=lambda e: e["id"])[:4]:
        gt = json.loads((corpus / "heldout" / rid["files"]["gt"]["path"]).read_text())
        names |= {g["name"] for g in gt["rows"]} | {g["analyte_key"] for g in gt["rows"] if g["analyte_key"]}
    names = sorted(n for n in names if len(n) >= 6)
    assert names
    adapter = FakeAdapter(raise_on=names[0], chatter=f"reading {names[-1]}")
    code = go(corpus, out, "--splits", "heldout", "--renderings", "native", "--gate", "report", adapter=adapter)
    captured = capfd.readouterr()
    assert code == 0
    metrics = json.loads((out / "metrics.json").read_text())
    assert "heldout" not in metrics["diagnostics"] and metrics["diagnostics"] == {}
    cell_ = metrics["results"]["heldout"]["native"]
    assert set(_all_keys(cell_)) <= WHITELIST
    assert cell_["crashes"] == {"RuntimeError": 1}  # the class name only
    n_native = sum(1 for e in json.loads((corpus / "heldout/manifest.json").read_text())["reports"] if not e["is_csv"])
    assert cell_["jobs"] == n_native  # the whole cell: heldout is never narrowed (P1 review MED-3)
    texts = [(out / "metrics.json").read_text(), (out / "report.md").read_text(), captured.out, captured.err]
    for text in texts:
        assert not re.search(r"H\d{3}", text)
        assert hygiene.scan_for_leaks(text, names) == []
    assert sorted(p.name for p in out.iterdir()) == ["metrics.json", "report.md"]  # no per-report files


def test_ha06_whitelist_redaction_rejects_extra_keys():
    good = cell()
    assert set(hygiene.redact_heldout(good)) == set(good)
    for extra in ({"per_family": {}}, {"diagnostics": {}}, {"top_error_signatures": []}):
        with pytest.raises(hygiene.HygieneError):
            hygiene.redact_heldout({**good, **extra})
    bad = dict(good, M1=dict(good["M1"], note="x"))
    with pytest.raises(hygiene.HygieneError):
        hygiene.redact_heldout(bad)
    with pytest.raises(hygiene.HygieneError):
        hygiene.redact_heldout(dict(good, crashes={"error: potassium 6.9": 1}))  # a message, not a class name


def test_ha06_guard_and_quiet():
    with pytest.raises(hygiene.HygieneError):
        hygiene.guard_per_report_output("heldout")
    hygiene.guard_per_report_output("dev")
    hygiene.guard_per_report_output("test")
    assert hygiene.crash_class(ValueError("secret text")) == "ValueError"


def test_ha06_quiet_swallows_stdout_stderr_and_logs(capfd):
    log = logging.getLogger("tools.eval.test_quiet")
    with hygiene.quiet():
        print("to stdout")
        print("to stderr", file=sys.stderr)
        log.error("logged")
        import os

        os.write(1, b"raw fd 1\n")
        os.write(2, b"raw fd 2\n")
    print("after")
    out = capfd.readouterr()
    assert out.out.strip() == "after" and out.err == ""
    assert logging.root.manager.disable == 0  # logging restored


def test_ha06_no_heldout_corpus_file_is_tracked_by_git():
    res = subprocess.run(["git", "ls-files", "eval/corpus"], cwd=ROOT, capture_output=True, text=True, check=True)
    assert res.stdout.strip() == ""


def test_ha06_shard_files_for_heldout_hold_only_redacted_cells(corpus, tmp_path):
    shard = tmp_path / "s.json"
    assert go(corpus, tmp_path / "o", "--splits", "heldout", "--renderings", "csv", "--shard-out", str(shard)) == 0
    text = shard.read_text()
    d = json.loads(text)
    cellh = d["cells"]["heldout"]["csv"]
    assert set(cellh) == {"redacted"} and set(_all_keys(cellh["redacted"])) <= WHITELIST
    assert d["shard"] is None and d["planned"] == {"heldout": {"csv": cellh["redacted"]["jobs"]}}  # aggregate only
    assert not re.search(r"H\d{3}", text) and "counts" not in cellh
    assert not (tmp_path / "o").exists()


def test_ha06_shard_runtime_is_not_in_job_order():
    """T16 (zoro, LOW): the shard's per-rendering wall times were listed in sorted-job-id order, so position i of a
    heldout shard's list was the time of job H(i+1) (per-report detail on disk). They are written sorted: p50/p95
    and the aggregate merge are order-independent."""
    out = run._Outcome()
    for secs in (3.5, 1.25, 2.0):
        out.add("heldout", "native", me.empty_counts(), secs, (0, 0), None)
    doc = run.shard_doc({"profile": "full", "splits": ["heldout"], "renderings": ["native"], "samples": {}}, {}, out,
                        None, 1.0)
    assert doc["runtime"]["seconds"]["native"] == [1.25, 2.0, 3.5]

def test_ha07_same_seed_same_metrics_and_different_seed_changes_fingerprint(corpus, tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    assert go(corpus, a, "--profile", "smoke") == 0
    assert go(corpus, b, "--profile", "smoke") == 0
    assert (a / "metrics.json").read_text().split('"runtime"')[0] == (b / "metrics.json").read_text().split(
        '"runtime"')[0] or det(a / "metrics.json") == det(b / "metrics.json")
    assert det(a / "metrics.json") == det(b / "metrics.json")
    # the deterministic part of report.md is identical too (everything before the runtime section)
    ra = (a / "report.md").read_text().split("## Runtime")[0]
    assert ra == (b / "report.md").read_text().split("## Runtime")[0]
    m = det(a / "metrics.json")
    assert m["run"]["profile"] == "smoke" and m["run"]["samples"]["first"] == 6
    assert set(m["results"]["test"]) <= {"native", "csv"}
    before = gen.fingerprint("test")
    monkeypatch.setattr(cfg, "DEVTEST_SEED", cfg.DEVTEST_SEED + 1)
    assert gen.fingerprint("test") != before


def test_ha07_metrics_json_keys_are_sorted(corpus, tmp_path):
    assert go(corpus, tmp_path, "--profile", "smoke") == 0
    text = (tmp_path / "metrics.json").read_text()
    assert json.dumps(json.loads(text), sort_keys=True, indent=2, ensure_ascii=False) + "\n" == text


# ---------------------------------------------------------------- HA-08 sampling

@pytest.fixture(scope="module")
def manifest(corpus):
    return json.loads((corpus / "test/manifest.json").read_text())


def test_ha08_sampling_is_stratified_stable_and_independent(manifest):
    pool = [e for e in manifest["reports"] if not e["is_csv"]]
    picks = run.sample_ids(pool, 0.25)
    assert picks == run.sample_ids(list(reversed(pool)), 0.25)  # order of the manifest is irrelevant
    by_class = Counter(e["scenario_class"] for e in pool)
    got = Counter(next(e["scenario_class"] for e in pool if e["id"] == i) for i in picks)
    for cls, n in by_class.items():
        assert got[cls] == math.floor(0.25 * n + 0.5), cls  # exactly round(25%) per scenario class
    # removing an unselected report of one class changes no pick of any other class
    cls0 = sorted(by_class)[0]
    victim = next(e for e in pool if e["scenario_class"] == cls0 and e["id"] not in picks)
    rest = [e for e in pool if e["id"] != victim["id"]]
    other = {i for i in picks if next(e for e in pool if e["id"] == i)["scenario_class"] != cls0}
    other_after = {i for i in run.sample_ids(rest, 0.25) if next(e for e in pool if e["id"] == i)["scenario_class"]
                   != cls0}
    assert other == other_after
    assert run.sample_ids(pool, 1.0) == sorted(e["id"] for e in pool) and run.sample_ids(pool, 0.0) == []


def test_ha08_ci_profile_job_units(manifest, corpus):
    args = run.run_parser().parse_args(["--profile", "ci"])
    plan = run.resolve(args)
    manifest = json.loads(json.dumps(manifest))  # image files are not needed to plan jobs: fake their entries
    for e in manifest["reports"]:
        e["files"]["scan"] = {"path": f"scan/{e['id']}.pdf", "sha256": "0"}
        e["files"]["rot90"] = {"path": f"rot90/{e['id']}.pdf", "sha256": "0"}
        e["files"]["photo"] = {"pages": [{"path": f"photo/{e['id']}.p{n}.png", "sha256": "0"}
                                         for n in range(1, e["n_pages"] + 1)]}
    jobs = run.build_jobs("test", manifest, corpus, plan)
    again = run.build_jobs("test", manifest, corpus, plan)
    assert [j.job_id for j, _ in jobs] == [j.job_id for j, _ in again] == sorted(j.job_id for j, _ in jobs)
    by = Counter(j.rendering for j, _ in jobs)
    n_csv = sum(e["is_csv"] for e in manifest["reports"])
    n_rot = sum(1 for e in manifest["reports"] if e["rot90"] and not e["is_csv"])
    assert by["csv"] == n_csv and by["native"] == 80 - n_csv and by["rot90"] == n_rot
    scan_ids = {j.report_id for j, _ in jobs if j.rendering == "scan"}
    photo_ids = {j.report_id for j, _ in jobs if j.rendering == "photo"}
    assert scan_ids == photo_ids  # photo uses the same reports as scan
    pages = {e["id"]: e["n_pages"] for e in manifest["reports"]}
    assert by["photo"] == sum(pages[i] for i in scan_ids)  # one photo job per page
    pj = next(j for j, _ in jobs if j.rendering == "photo")
    assert pj.pages == (int(pj.job_id.rsplit(":p", 1)[1]),) and pj.job_id.startswith(pj.report_id + ":photo:p")


def test_ha08_shard_partition_is_deterministic_and_complete(manifest, corpus):
    plan = run.resolve(run.run_parser().parse_args(["--renderings", "native"]))
    jobs = run.build_jobs("test", manifest, corpus, plan)
    parts = [run.apply_shard(jobs, (k, 3)) for k in (1, 2, 3)]
    ids = [j.job_id for p in parts for j, _ in p]
    assert sorted(ids) == [j.job_id for j, _ in jobs] and len(set(ids)) == len(ids)
    assert parts[0][0][0].job_id == jobs[0][0].job_id and parts[1][0][0].job_id == jobs[1][0].job_id


# ---------------------------------------------------------------- HA-09 aggregate

def test_ha09_shards_aggregate_to_the_single_run(corpus, tmp_path):
    common = ("--splits", "test", "--sample-n", "8", "--sample", "scan=1.0")
    single = tmp_path / "single"
    assert go(corpus, single, *common, "--renderings", "native,scan", "--gate", "none") == 0
    shards = tmp_path / "shards"
    assert go(corpus, tmp_path / "x", *common, "--sample", "scan=1.0", "--renderings", "native",
              "--shard-out", str(shards / "test-native.json")) == 0
    assert go(corpus, tmp_path / "x", *common, "--renderings", "scan", "--shard-out",
              str(shards / "test-scan.json")) == 0
    agg = tmp_path / "agg"
    assert run.main(["aggregate", "--in", str(shards), "--out", str(agg), "--gate", "none"]) == 0
    assert det(agg / "metrics.json") == det(single / "metrics.json")
    assert set(det(agg / "metrics.json")["results"]["test"]) == {"native", "scan"}
    assert (agg / "report.md").read_text().split("## Runtime")[0] == (single / "report.md").read_text().split(
        "## Runtime")[0]


def test_ha09_cell_split_across_shards_merges_to_the_same_counts(corpus, tmp_path):
    args = ("--splits", "test", "--sample-n", "9", "--renderings", "native")
    single = tmp_path / "single"
    assert go(corpus, single, *args, "--gate", "none") == 0
    for k in (1, 2):
        assert go(corpus, tmp_path / "x", *args, "--shard", f"{k}/2", "--shard-out",
                  str(tmp_path / "sh" / f"s{k}.json")) == 0
    assert run.main(["aggregate", "--in", str(tmp_path / "sh"), "--out", str(tmp_path / "agg"), "--gate", "none"]) == 0
    assert det(tmp_path / "agg/metrics.json") == det(single / "metrics.json")


def test_ha09_heldout_shards_merge_redacted_cells(corpus, tmp_path):
    """Heldout shards are whole split x rendering cells (the nightly matrix); --shard k/n is refused for heldout."""
    single = tmp_path / "single"
    assert go(corpus, single, "--splits", "heldout", "--renderings", "native,csv", "--gate", "none") == 0
    for r in ("native", "csv"):
        assert go(corpus, tmp_path / "x", "--splits", "heldout", "--renderings", r, "--shard-out",
                  str(tmp_path / "sh" / f"heldout-{r}.json")) == 0
    assert run.main(["aggregate", "--in", str(tmp_path / "sh"), "--out", str(tmp_path / "agg"), "--gate", "none"]) == 0
    assert det(tmp_path / "agg/metrics.json") == det(single / "metrics.json")


def test_ha09_aggregate_refuses_mismatched_shards(corpus, tmp_path):
    assert go(corpus, tmp_path / "x", "--splits", "test", "--sample-n", "3", "--renderings", "native",
              "--shard-out", str(tmp_path / "sh" / "a.json")) == 0
    assert go(corpus, tmp_path / "x", "--splits", "test", "--sample-n", "4", "--renderings", "csv",
              "--shard-out", str(tmp_path / "sh" / "b.json")) == 0
    assert run.main(["aggregate", "--in", str(tmp_path / "sh"), "--out", str(tmp_path / "agg")]) == 2
    assert run.main(["aggregate", "--in", str(tmp_path / "empty"), "--out", str(tmp_path / "agg")]) == 2


# ---------------------------------------------------------------- HA-10 harness errors

def test_ha10_missing_corpus_file_exits_2_and_writes_no_metrics(corpus, tmp_path, capsys):
    copy = tmp_path / "corpus"
    shutil.copytree(corpus / "test", copy / "test")
    man = json.loads((copy / "test/manifest.json").read_text())
    first = sorted((e for e in man["reports"] if not e["is_csv"]), key=lambda e: e["id"])[0]
    (copy / "test" / first["files"]["native"]["path"]).unlink()
    out = tmp_path / "o"
    code = go(copy, out, "--splits", "test", "--sample-n", "80", "--renderings", "native", "--no-materialize",
              "--gate", "none")
    err = capsys.readouterr().err
    assert code == 2 and "missing" in err and "make eval-corpus" in err
    assert not (out / "metrics.json").exists() and not (out / "report.md").exists()


def test_ha10_missing_manifest_and_stale_fingerprint_exit_2(corpus, tmp_path):
    assert go(tmp_path / "nowhere", tmp_path / "o", "--splits", "test", "--no-materialize", "--gate", "none") == 2
    copy = tmp_path / "corpus"
    shutil.copytree(corpus / "test", copy / "test")
    man = json.loads((copy / "test/manifest.json").read_text())
    man["fingerprint"] = "0" * 64
    (copy / "test/manifest.json").write_text(json.dumps(man))
    assert go(copy, tmp_path / "o", "--splits", "test", "--no-materialize", "--gate", "none") == 2
    assert not (tmp_path / "o").exists()


def test_ha10_harness_config_error_maps_to_exit_2(monkeypatch, tmp_path, capsys):
    from tools.eval import adapters as registry
    from tools.eval.adapters.pipeline_v0 import HarnessConfigError

    def boom(**_kw):
        raise HarnessConfigError("SECRET_KEY not set")

    monkeypatch.setitem(registry.ADAPTERS, "pipeline_v0", boom)
    code = run.main(["--splits", "test", "--sample-n", "1", "--renderings", "native", "--gate", "none", "--out",
                     str(tmp_path / "o"), "--corpus-dir", str(tmp_path / "c")])
    assert code == 2 and "SECRET_KEY" in capsys.readouterr().err
    assert not (tmp_path / "o" / "metrics.json").exists()


def test_ha10_atomic_write_leaves_no_partial_file(tmp_path, monkeypatch):
    target = tmp_path / "metrics.json"
    target.write_text("old\n")

    def fail(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(report.os, "replace", fail)
    with pytest.raises(OSError):
        report.write_metrics(target, {"a": 1})
    assert target.read_text() == "old\n"  # the previous file is intact
    assert [p.name for p in tmp_path.iterdir()] == ["metrics.json"]  # and no temp file is left behind
    monkeypatch.undo()
    report.write_metrics(target, {"b": 2, "a": 1})
    assert json.loads(target.read_text()) == {"a": 1, "b": 2}


def test_ha10_bad_arguments_exit_2(tmp_path):
    for bad in (["--splits", "nope"], ["--renderings", "tiff"], ["--sample", "scan=2"], ["--shard", "3/2"]):
        assert run.main([*bad, "--out", str(tmp_path / "o")]) == 2


# ---------------------------------------------------------------- HA-11 report.md

def test_ha11_report_md_content(corpus, tmp_path):
    out = tmp_path / "o"
    assert go(corpus, out, "--splits", "test", "--sample-n", "6", "--renderings", "native,csv", "--gate",
              "report") == 0
    text = (out / "report.md").read_text()
    metrics = json.loads((out / "metrics.json").read_text())
    assert report.BANNER in text and text.splitlines()[2].startswith("> P1 baseline")
    assert "| Split | Rendering | Metric | Target | Value | Count | 95% CI | Result |" in text
    assert "| test | native | M1 silent-error rate | <= 0.5% |" in text
    assert "| test | native | M2 critical recall | = 100% |" in text
    assert re.search(r"\| test \| native \| M3 .*\| (PASS|FAIL|n/a) \|", text)
    assert metrics["corpus"]["test"]["fingerprint"] in text
    assert "`fake_adapter` version `9`" in text and "`clinical_rules` version `1`" in text
    assert metrics["run"]["git_sha"] in text and re.fullmatch(r"[0-9a-f]{40}|unknown", metrics["run"]["git_sha"])
    assert text.index("## Known v0 limitations") < text.index("## Runtime (non-deterministic)")
    assert text.rstrip().splitlines()[-1].startswith("- ") or "p95" in text


def test_ha11_report_heldout_section_is_a_fixed_template(corpus, tmp_path):
    out = tmp_path / "o"
    assert go(corpus, out, "--profile", "full", "--splits", "test,heldout", "--renderings", "csv", "--gate",
              "none") == 0
    text = (out / "report.md").read_text()
    assert "## Heldout (aggregate only)" in text
    held = text.split("## Heldout (aggregate only)")[1].split("## Gate")[0]
    assert "| Family |" not in held and "signature" not in held.lower() and not re.search(r"H\d{3}", text)
    dev_part = text.split("## Heldout")[0]
    assert "Dev and test diagnostics" in dev_part and "| Family | M3 |" in dev_part


def test_failure_dumps_are_written_for_dev_and_test_only(corpus, tmp_path):
    d = tmp_path / "dump"
    assert go(corpus, tmp_path / "o", "--splits", "test", "--sample-n", "6", "--renderings", "native", "--gate",
              "none", "--dump-failures", str(d)) == 0
    files = list(d.glob("*.json")) if d.exists() else []
    for f in files:
        assert json.loads(f.read_text())["job_id"].endswith(":native")


def test_makefile_targets_call_run_py():
    mk = (ROOT / "Makefile").read_text()
    assert "python -m tools.eval.run --profile ci $(EVAL_ARGS)" in mk
    assert "python -m tools.eval.run --profile full $(EVAL_ARGS)" in mk
    assert "python -m tools.eval.run --profile ci --update-baseline $(EVAL_ARGS)" in mk
    assert "not implemented" not in mk


# ---------------------------------------------------------------- committed eval/gate.json (P1 T15, plan 6.9)

def test_committed_gate_json_equals_builtin_defaults():
    path = ROOT / "eval" / "gate.json"
    assert path.exists()
    assert gate.load_config(path) == gate.load_config(None) == gate.DEFAULT_CONFIG
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw == {"mode": "ratchet", "profile": "ci", "split": "test", "baseline": "eval/baseline.json",
                   "tolerance": {"native": 0, "csv": 0, "scan": 1, "photo": 1, "rot90": 1},
                   "zero_tolerance": ["M2", "M2_post"]}


# ---------------------------------------------------------------- P1 review MED-1: subset runs are never ratcheted

def _tiny_jobs(n_jobs=20):
    """The reviewer's repro: 20 three-row jobs; the odd-indexed ones (shard 2 of 2 under ``run.apply_shard``) hold a
    critical K 6.7 (Table C: K > 6.5). Returns (jobs, gts) with job ids in sorted order."""
    from tests.test_eval_matching import gt_row
    from tools.eval.adapters.base import EvalJob

    jobs, gts = [], []
    for i in range(n_jobs):
        k = gt_row("potassium", 6.7 if i % 2 else 4.2, "mmol/L", 3.5, 5.1)
        rows = [dict(gt_row("hemoglobin", 14.2, "g/dL", 13.0, 17.0), row_id="r1"), dict(k, row_id="r2"),
                dict(gt_row("sodium", 140, "mmol/L", 135, 145), row_id="r3")]
        h = "critical" if i % 2 else "normal"
        gts.append({"report_id": f"T{i + 1:03d}", "family": "G01", "rows": rows, "decoys": [],
                    "expected": {"headline": h, "per_page_headline": {"1": h}}})
        jobs.append(EvalJob(f"T{i + 1:03d}:native", f"T{i + 1:03d}", "test", "native", ("x.pdf",), (), "male", 40))
    return jobs, gts


def _metrics_doc(jobs, gts, regress_shard2: bool, judge=None):
    from tests.test_eval_matching import ext_row
    from tools.eval.clinical import ClinicalRulesScorer

    recs = []
    for i, (j, g) in enumerate(zip(jobs, gts, strict=True)):
        ext = [ext_row(r, n) for n, r in enumerate(g["rows"])]
        if regress_shard2 and i % 2:  # reads the previous-column K, shows it NORMAL, flags nothing
            ext[1] = ext_row(g["rows"][1], 1, value_num=4.2, value_raw="4.2", flag="NORMAL")
        recs.append(me.job_record(j, g, ExtractionResult(j.job_id, "ok", ext, pre_review_headline="normal"),
                                  ClinicalRulesScorer()))
    cells = {r: tg.annotate(me.cell_results(c), r) for r, c in me.aggregate(recs)["test"].items()}
    return doc(cells, judge=judge)


def test_med1_reviewer_repro_full_run_fails_and_a_shard_is_refused_not_passed():
    jobs, gts = _tiny_jobs()
    base = _metrics_doc(jobs, gts, regress_shard2=False)
    assert base["results"]["test"]["native"]["M2"]["num"] == 10  # 10 criticals, all shown CRITICAL_HIGH
    full = _metrics_doc(jobs, gts, regress_shard2=True)
    assert (full["results"]["test"]["native"]["M1"]["num"], full["results"]["test"]["native"]["M2"]["num"]) == (10, 0)
    assert gate.exit_code(evaluate("ratchet", full, base)) == 1  # the regression is caught on the whole run
    k1 = [i for i, (j, _) in enumerate(run.apply_shard([(j, None) for j in jobs], (1, 2)))]
    shard1 = _metrics_doc([jobs[i * 2] for i in k1], [gts[i * 2] for i in k1], regress_shard2=True)
    cur = shard1["results"]["test"]["native"]
    assert cur["jobs"] == 10 and (cur["M1"]["num"], cur["M2"]["den"]) == (0, 0)  # looks clean on its half
    with pytest.raises(gate.GateConfigError, match="different set of test jobs"):
        evaluate("ratchet", shard1, base)  # before the fix this passed (exit 0)
    with pytest.raises(gate.GateConfigError):
        evaluate("targets", shard1, base)


def test_med1_equal_job_count_but_different_gt_denominator_is_refused():
    base = doc({"native": cell(m1=(5, 1000), m2=(10, 10), jobs=40)})
    with pytest.raises(gate.GateConfigError, match="M1 denominator"):
        evaluate("ratchet", doc({"native": cell(m1=(0, 900), m2=(10, 10), jobs=40)}), base)
    with pytest.raises(gate.GateConfigError, match="M2 denominator"):
        evaluate("ratchet", doc({"native": cell(m1=(5, 1000), m2=(9, 9), jobs=40)}), base)
    with pytest.raises(gate.GateConfigError, match="shard"):
        evaluate("ratchet", doc({"native": cell(m1=(5, 1000), m2=(10, 10), jobs=40)}, shard=[1, 2]), base)


def test_med1_cli_shard_with_ratchet_exits_2_before_any_work(corpus, tmp_path):
    out = tmp_path / "o"
    args = ("--splits", "test", "--sample-n", "8", "--renderings", "native")
    assert go(corpus, out, *args, "--update-baseline") == 0
    adapter = FakeAdapter()
    for mode in ("ratchet", "targets"):
        assert go(corpus, tmp_path / "x", *args, "--shard", "1/2", "--gate", mode, adapter=adapter) == 2
    assert adapter.calls == 0 and not (tmp_path / "x").exists()
    assert go(corpus, out, *args, "--shard", "1/2", "--update-baseline") == 2  # a baseline is a whole run
    assert go(corpus, out, *args, "--gate", "ratchet") == 0  # the whole run still passes against itself


def test_med1_aggregate_with_a_missing_or_duplicated_shard_file_exits_2(corpus, tmp_path):
    out = tmp_path / "o"
    args = ("--splits", "test", "--sample-n", "9", "--renderings", "native")
    assert go(corpus, out, *args, "--update-baseline") == 0
    sh = tmp_path / "sh"
    for k in (1, 2, 3):
        assert go(corpus, tmp_path / "x", *args, "--shard", f"{k}/3", "--shard-out", str(sh / f"s{k}.json")) == 0
    complete = ["aggregate", "--in", str(sh), "--out", str(tmp_path / "agg"), "--baseline", str(out / "baseline.json")]
    assert run.main([*complete, "--gate", "ratchet"]) == 0  # all three parts: the whole cell, gated
    (sh / "s2.json").unlink()  # one shard file lost (an artifact upload failed)
    for mode in ("ratchet", "targets", "report", "none"):
        assert run.main([*complete[:-2], "--gate", mode, "--baseline", str(out / "baseline.json")]) == 2, mode
    shutil.copy(sh / "s1.json", sh / "s2.json")  # part 1 twice, part 2 missing: the job count would even match 6
    assert run.main([*complete, "--gate", "none"]) == 2


def test_med1_shard_files_record_the_full_cell_size_and_partition(corpus, tmp_path):
    f = tmp_path / "s.json"
    assert go(corpus, tmp_path / "x", "--splits", "test", "--sample-n", "9", "--renderings", "native", "--shard",
              "2/3", "--shard-out", str(f)) == 0
    d = json.loads(f.read_text())
    n_native = sum(1 for e in sorted(json.loads((corpus / "test/manifest.json").read_text())["reports"],
                                     key=lambda e: e["id"])[:9] if not e["is_csv"])
    assert d["shard"] == [2, 3] and d["planned"] == {"test": {"native": n_native}}  # hand count of the first 9
    assert d["cells"]["test"]["native"]["counts"]["jobs"] == len(range(1, n_native, 3))  # jobs 2, 5, 8, ...


# ---------------------------------------------------------------- P1 review MED-2: definition changes stop the ratchet

def test_med2_judge_digest_covers_the_judge_and_adapter_modules():
    paths = set(gate.judge_paths())
    for rel in ("tools/eval/match.py", "tools/eval/metrics.py", "tools/eval/review_sim.py", "tools/eval/targets.py",
                "tools/eval/gate.py", "tools/eval/run.py", "tools/eval/ocr_cache.py",
                "tools/eval/adapters/__init__.py", "tools/eval/adapters/base.py",
                "tools/eval/adapters/pipeline_v0.py"):
        assert rel in paths, rel
    js = gate.judge_sources()
    assert set(js["modules"]) == paths and re.fullmatch(r"[0-9a-f]{64}", js["digest"])


def _imports(path: pathlib.Path) -> set[str]:
    import ast

    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("tools.eval"):
            out.add(node.module)
            out |= {f"{node.module}.{a.name}" for a in node.names}
        elif isinstance(node, ast.Import):
            out |= {a.name for a in node.names if a.name.startswith("tools.eval")}
    return out


def test_med2_every_module_a_gated_count_depends_on_is_fingerprinted():
    """Transitive tools.eval imports of the judge and adapter modules: each is in the judge digest or the split
    fingerprint, except the two that only format or redact counts (report.py, hygiene.py)."""
    covered = set(gate.judge_paths()) | set(gen._COMMON_MODULES)
    presentation_only = {"tools/eval/report.py", "tools/eval/hygiene.py", "tools/eval/__init__.py"}
    todo, seen = list(gate.judge_paths()), set()
    while todo:
        rel = todo.pop()
        if rel in seen:
            continue
        seen.add(rel)
        for mod in _imports(ROOT / rel):
            for cand in (mod.replace(".", "/") + ".py", mod.replace(".", "/") + "/__init__.py"):
                if (ROOT / cand).exists():
                    todo.append(cand)
    missing = sorted(p for p in seen - covered - presentation_only if "/heldout/" not in p)
    assert missing == [], missing


def test_med2_flipping_a_metric_definition_makes_the_ratchet_refuse(tmp_path, monkeypatch):
    """The reviewer's repro: counting every row as needs_review lowers M1 and raises M2, which the ratchet alone
    reads as an improvement. With the judge digest the edited definition stops the gate (exit 2) instead."""
    jobs, gts = _tiny_jobs()
    root = tmp_path / "repo"
    for rel in gate.judge_paths():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / rel, root / rel)
    before = gate.judge_sources(root)
    base = _metrics_doc(jobs, gts, regress_shard2=True, judge=before)
    src = (root / "tools/eval/metrics.py").read_text()
    flipped = src.replace('    return bool(field(r, "needs_review"))', "    return True", 1)
    assert flipped != src
    (root / "tools/eval/metrics.py").write_text(flipped)
    after = gate.judge_sources(root)
    assert after["digest"] != before["digest"]
    monkeypatch.setattr(me, "_flagged", lambda r: True)  # what the flipped source does
    cur = _metrics_doc(jobs, gts, regress_shard2=True, judge=after)
    c, b = cur["results"]["test"]["native"], base["results"]["test"]["native"]
    assert (b["M1"]["num"], b["M2"]["num"]) == (10, 0) and (c["M1"]["num"], c["M2"]["num"]) == (0, 10)
    assert evaluate("ratchet", dict(cur, run=dict(cur["run"], judge=before)), base)["passed"]  # unseen without it
    with pytest.raises(gate.GateConfigError, match=r"judge/adapter definitions \(tools/eval/metrics.py\)"):
        evaluate("ratchet", cur, base)


def test_med2_cli_baseline_from_other_judge_sources_exits_2(corpus, tmp_path, monkeypatch):
    out = tmp_path / "o"
    args = ("--splits", "test", "--sample-n", "4", "--renderings", "native")
    assert go(corpus, out, *args, "--update-baseline") == 0
    assert json.loads((out / "baseline.json").read_text())["run"]["judge"] == gate.judge_sources()
    assert go(corpus, out, *args, "--gate", "ratchet") == 0
    real = gate.judge_sources

    def edited(root=None):
        js = real(root)
        mods = dict(js["modules"], **{"tools/eval/adapters/pipeline_v0.py": "0" * 64})  # an NR0 rule changed
        return {"digest": "1" * 64, "modules": mods}

    monkeypatch.setattr(gate, "judge_sources", edited)
    assert go(corpus, out, *args, "--gate", "ratchet") == 2
    b = json.loads((out / "baseline.json").read_text())
    b["run"].pop("judge")  # a baseline recorded before the digest existed is never reused either
    (out / "baseline.json").write_text(json.dumps(b))
    monkeypatch.setattr(gate, "judge_sources", real)
    assert go(corpus, out, *args, "--gate", "ratchet") == 2


# ---------------------------------------------------------------- P1 review MED-3: heldout is never narrowed

@pytest.mark.parametrize("extra", [
    ("--sample-n", "1"), ("--sample", "1"), ("--sample", "scan=0.5"), ("--sample", "native=1.0"),
    ("--shard", "1/80", "--shard-out", "SHARD"), ("--shard", "3/3"),
])
def test_med3_heldout_narrowing_flags_exit_3_before_any_work(corpus, tmp_path, extra):
    adapter = FakeAdapter()
    extra = tuple(str(tmp_path / "s.json") if x == "SHARD" else x for x in extra)
    assert go(corpus, tmp_path / "o", "--profile", "heldout", "--renderings", "native", *extra,
              adapter=adapter) == 3
    assert adapter.calls == 0 and not (tmp_path / "o").exists() and not (tmp_path / "s.json").exists()


@pytest.mark.parametrize("profile_args", [
    ("--profile", "full", "--sample-n", "3"),  # full includes heldout
    ("--profile", "ci", "--splits", "heldout"),  # ci samples scan and photo at 25%
    ("--profile", "smoke", "--splits", "heldout"),  # smoke takes the first 6 reports
    ("--profile", "dev", "--splits", "test,heldout", "--renderings", "photo"),  # 25% of photo pages
])
def test_med3_profiles_that_would_narrow_heldout_exit_3(corpus, tmp_path, profile_args):
    adapter = FakeAdapter()
    assert go(corpus, tmp_path / "o", *profile_args, adapter=adapter) == 3
    assert adapter.calls == 0


def test_med3_whole_heldout_cells_are_allowed_and_complete(corpus, tmp_path):
    assert go(corpus, tmp_path / "o", "--profile", "heldout", "--renderings", "csv", "--gate", "none") == 0
    cell_ = json.loads((tmp_path / "o/metrics.json").read_text())["results"]["heldout"]["csv"]
    n_csv = sum(1 for e in json.loads((corpus / "heldout/manifest.json").read_text())["reports"] if e["is_csv"])
    assert cell_["jobs"] == n_csv  # every heldout csv report, aggregate only


def test_med3_backstop_refuses_a_partial_heldout_cell():
    with pytest.raises(hygiene.HygieneError):
        hygiene.check_heldout_complete("heldout", "native", 1, 77)
    hygiene.check_heldout_complete("heldout", "native", 77, 77)
    hygiene.check_heldout_complete("test", "native", 1, 76)  # dev and test may be sampled


def test_med3_aggregate_of_a_heldout_cell_missing_jobs_exits_3(corpus, tmp_path):
    sh = tmp_path / "sh"
    assert go(corpus, tmp_path / "x", "--splits", "heldout", "--renderings", "csv", "--shard-out",
              str(sh / "h.json")) == 0
    d = json.loads((sh / "h.json").read_text())
    d["planned"]["heldout"]["csv"] += 1  # the file claims a bigger cell than it holds
    (sh / "h.json").write_text(json.dumps(d))
    assert run.main(["aggregate", "--in", str(sh), "--out", str(tmp_path / "agg"), "--gate", "none"]) == 3


# ---------------------------------------------------------------- P1 review MED-6: make eval never clobbers eval/

def test_med6_non_full_runs_default_to_eval_runs_profile(corpus, tmp_path, monkeypatch):
    fake_eval = tmp_path / "eval"
    fake_eval.mkdir()
    for name in run.PHASE_END_FILES:
        (fake_eval / name).write_text("phase-end record\n")
    monkeypatch.setattr(cfg, "EVAL_DIR", fake_eval)
    from tools.eval.clinical import ClinicalRulesScorer

    code = run.main(["--profile", "ci", "--corpus-dir", str(corpus), "--splits", "test", "--sample-n", "4",
                     "--renderings", "native", "--baseline", str(tmp_path / "b.json")], adapter=FakeAdapter(),
                    scorer=ClinicalRulesScorer())
    assert code == 0
    assert all((fake_eval / n).read_text() == "phase-end record\n" for n in run.PHASE_END_FILES)
    assert (fake_eval / "runs" / "ci" / "metrics.json").exists() and (fake_eval / "runs/ci/report.md").exists()
    # an explicit --out eval/ with a partial run is refused (exit 2) and leaves the record intact
    assert go(corpus, fake_eval, "--splits", "test", "--sample-n", "4", "--renderings", "native") == 2
    assert all((fake_eval / n).read_text() == "phase-end record\n" for n in run.PHASE_END_FILES)


def test_med6_only_a_complete_full_profile_run_is_phase_end():
    def resolved(*a):
        return run.resolve(run.run_parser().parse_args(list(a)))

    assert run.is_phase_end(resolved("--profile", "full"))
    for partial in (("--profile", "ci"), ("--profile", "full", "--sample-n", "3"),
                    ("--profile", "full", "--splits", "dev,test"), ("--profile", "full", "--renderings", "native"),
                    ("--profile", "full", "--shard", "1/2"), ("--profile", "full", "--sample", "photo=0.5"),
                    ("--profile", "heldout")):
        assert not run.is_phase_end(resolved(*partial)), partial
    assert run.default_out("full") == cfg.EVAL_DIR and run.default_out("ci") == cfg.EVAL_DIR / "runs" / "ci"


def test_med6_ci_uploads_the_ci_run_and_runs_dir_is_ignored():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    gate_job = ci.split("eval-gate:")[1].split("eval-full-on-label:")[0]
    assert "eval/runs/ci/metrics.json" in gate_job and "eval/runs/ci/report.md" in gate_job
    res = subprocess.run(["git", "check-ignore", "-q", "eval/runs/ci/metrics.json"], cwd=ROOT, check=False)
    assert res.returncode == 0


# ---------------------------------------------------------------- P1 review LOW-7: CI token and nightly aggregate

def test_low7_ci_workflow_token_is_read_only():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    top = ci.split("\njobs:")[0]
    assert re.search(r"^permissions:\n  contents: read$", top, re.M)


def _shard_file(path: pathlib.Path, cells: dict, planned: dict, profile="ci") -> None:
    d = {"schema": run.SHARD_SCHEMA, "shard": None, "planned": planned,
         "run": {"profile": profile, "splits": sorted(planned), "renderings": sorted({r for v in planned.values()
                                                                                     for r in v}),
                 "samples": {"first": None, "fractions": {}}},
         "corpus": {}, "ident": ["a", "1", "s", "1"], "cells": cells,
         "runtime": {"seconds": {}, "hits": 0, "misses": 0, "wall": 0.0}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d))


def test_low7_nightly_aggregate_exits_nonzero_when_a_job_crashed(tmp_path):
    from tests.test_eval_matching import gt_row
    from tools.eval.adapters.base import EvalJob
    from tools.eval.clinical import ClinicalRulesScorer

    g = {"report_id": "T001", "family": "G01", "rows": [dict(gt_row("sodium", 140, "mmol/L", 135, 145), row_id="r")],
         "decoys": [], "expected": {"headline": "normal"}}
    j = EvalJob("T001:native", "T001", "test", "native", ("x.pdf",), (), "male", 40)
    crashed = me.crash_record(j, g, "RuntimeError", ClinicalRulesScorer()).counts
    sh = tmp_path / "sh"
    _shard_file(sh / "native.json", {"test": {"native": {"counts": crashed}}}, {"test": {"native": 1}})
    for r in ("csv", "scan", "photo", "rot90"):  # every other cell of the ci profile is present and empty
        _shard_file(sh / f"{r}.json", {}, {"test": {r: 0}})
    agg = ["aggregate", "--in", str(sh), "--out", str(tmp_path / "agg")]
    assert run.main([*agg, "--gate", "report"]) == 1  # one crashed job: the nightly is not green
    m = json.loads((tmp_path / "agg/metrics.json").read_text())
    assert m["gate"]["passed"] is False and m["gate"]["failures"][0]["kind"] == "crashes"
    assert run.main([*agg, "--gate", "none"]) == 0  # exploration only
    (sh / "rot90.json").unlink()  # a shard job died before uploading its file
    assert run.main([*agg, "--gate", "report"]) == 2


def test_med2_report_header_shows_the_judge_digest(corpus, tmp_path):
    assert go(corpus, tmp_path, "--splits", "test", "--sample-n", "3", "--renderings", "native") == 0
    m = json.loads((tmp_path / "metrics.json").read_text())
    assert m["run"]["judge"]["digest"] == gate.judge_sources()["digest"]
    assert "Judge digest" in (tmp_path / "report.md").read_text() and m["run"]["judge"]["digest"] in (
        tmp_path / "report.md").read_text()
