"""P1 T11: the adapter layer (plans/P1.md 6.2-6.4, 6.12; tests HA-01..HA-04 of section 11).

Expectations come from the plan and the GT clinical rules (``tools.eval.clinical``, Table C), never from adapter
output. HA-02 compares the adapter with an independent ``run_report_pipeline`` call on the same file; its reports
are built directly with the T05/T07 layout kit and the T09 native writer (the T10 golden files are not required).

Native PDFs here run with the Paddle boxes stubbed to ``[]`` (as HA-01 specifies), so the pipeline keeps the
native text layer; HA-04 runs real PaddleOCR on a scan rendering.
"""

from __future__ import annotations

import ast
import collections
import json
import os
import pathlib
import random
import shutil
import subprocess
import sys
import time
import types

import pytest

pytest.importorskip("fitz")

from tools.eval import clinical as cl  # noqa: E402
from tools.eval import render  # noqa: E402
from tools.eval import scenarios as sc  # noqa: E402
from tools.eval.adapters import ADAPTERS, DEFAULT_ADAPTER, DEFAULT_SCORER, SCORERS, get_scorer  # noqa: E402
from tools.eval.adapters import base  # noqa: E402
from tools.eval.adapters import pipeline_v0 as v0mod  # noqa: E402
from tools.eval.adapters.base import ConfirmedRow, EvalJob, ExtractedRow, ScoreResult  # noqa: E402
from tools.eval.adapters.pipeline_v0 import EngineFloorScorerV0, PipelineV0Adapter  # noqa: E402
from tools.eval.catalogue import CATALOGUE  # noqa: E402
from tools.eval.clinical import PrintedRange as PR  # noqa: E402
from tools.eval.layouts import generic, templates  # noqa: E402
from tools.eval.ocr_cache import OcrCache, compute_fingerprint, fingerprint_parts  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]

# The plan's label table (6.3.2 step 8), written out independently of the adapter's mapping function.
PLAN_LABEL_TABLE = {"critical": "critical", "high": "abnormal", "moderate": "abnormal", "medium": "abnormal",
                    "low": "normal", "normal": "normal", "unknown": "unknown"}


def _plan_headline(label) -> str:
    return PLAN_LABEL_TABLE.get(str(label).lower(), "unknown") if label is not None else "unknown"


def _no_boxes(image):
    """Paddle stub: no boxes, so every native page keeps its text layer (HA-01)."""
    return []


def _tiny_pdf(path: pathlib.Path, lines: list[str], size=(420.0, 90.0)) -> str:
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=size, invariant=1)
    c.setFont("Helvetica", 10)
    y = size[1] - 24
    for line in lines:
        c.drawString(20, y, line)
        y -= 14
    c.showPage()
    c.save()
    return str(path)


def _job(path: str, *, sex=None, age=None, rendering="native", pages=(1,), rid="T900") -> EvalJob:
    return EvalJob(job_id=f"{rid}:{rendering}", report_id=rid, split="dev", rendering=rendering, files=(path,),
                   pages=pages, sex=sex, age=age)


def _codes(row: ExtractedRow) -> set[str]:
    return {code for code, _ in row.review_reasons}


@pytest.fixture
def v0():
    adapter = PipelineV0Adapter(ocr_cache=None)
    try:
        yield adapter
    finally:
        adapter.close()


@pytest.fixture
def no_ocr_boxes(monkeypatch):
    import app.services.ocr_service as ocr_service

    monkeypatch.setattr(ocr_service, "ocr_image", _no_boxes)


# ======================================================================== HA-01: v0 rules on real engine output

def test_ha01_v0_rules_on_real_engine_output(v0, no_ocr_boxes, tmp_path):
    k69 = cl.derive_row("potassium", value=6.9, unit="mmol/L", printed_range=PR("two_sided", 3.5, 5.1))
    na = cl.derive_row("sodium", value=139, unit="mmol/L", printed_range=PR("two_sided", 135, 145))
    k42h = cl.derive_row("potassium", value=4.2, unit="mmol/L", printed_range=PR("two_sided", 3.5, 5.1),
                         printed_flag="H")
    assert k69.flag == "CRITICAL_HIGH" and k69.is_critical  # Table C: K > 6.5
    assert na.flag == "NORMAL" and not na.ambiguity
    assert k42h.flag == "NORMAL" and "AMB_PRINTED_FLAG_CONFLICT" in k42h.ambiguity  # a lab error to review
    assert "zinc" not in CATALOGUE  # no clinical interpretation exists for it in this corpus

    cases = {
        "k69": "Potassium 6.9 mmol/L 3.5-5.1",
        "hb": "Hemoglobin 13.0 g/dL",
        "na": "Sodium 139 mmol/L 135-145",
        "zinc": "Zinc 85 µg/dL 60-120",
        "k42h": "Potassium 4.2 H mmol/L 3.5-5.1",
    }
    out = {}
    for key, line in cases.items():
        res = v0.extract(_job(_tiny_pdf(tmp_path / f"{key}.pdf", [line])))
        assert res.status == "ok", (key, res.meta)
        assert len(res.extracted) == 1, (key, [r.name_raw for r in res.extracted])
        assert not any(t.startswith("Patient ") for _, t in res.report_review_items), key  # sex/age note
        out[key] = res.extracted[0]

    r = out["k69"]
    assert r.analyte_key == "potassium" and r.flag == k69.flag
    assert r.needs_review and "NR0-CRITICAL" in _codes(r)
    assert (r.value_num, r.unit_raw, r.ref_low, r.ref_high) == (6.9, "mmol/L", 3.5, 5.1)

    # No printed range, no sex, no age: the product never assumes a sex (plan HA-01) -> not assessed, for review.
    r = out["hb"]
    assert r.flag == "UNKNOWN" and r.needs_review and "NR0-UNKNOWN" in _codes(r)
    assert (r.ref_low, r.ref_high) == (None, None)

    r = out["na"]
    assert r.flag == na.flag and not r.needs_review and r.review_reasons == []

    r = out["zinc"]
    assert r.analyte_key is None and r.needs_review and "NR0-NOANALYTE" in _codes(r)

    r = out["k42h"]
    assert r.printed_flag == "H" and r.needs_review and "NR0-FLAGCONFLICT" in _codes(r)


def test_ha01_v0_value_type_rule_and_report_items(v0, no_ocr_boxes, tmp_path):
    """NR0-VALUETYPE (unconvertible unit; two unlabelled values) and the unread-line count as a report item."""
    multi = cl.derive_row("sodium", value=141, unit="mmol/L", printed_range=PR("two_sided", 135, 145),
                          unlabelled_multivalue=True, input_sex="male", input_age=40)
    assert multi.flag == "UNKNOWN" and "AMB_UNLABELLED_MULTIVALUE" in multi.ambiguity  # must be reviewed
    assert "g/dL" not in (CATALOGUE["potassium"].unit, *CATALOGUE["potassium"].unit_spellings)
    pdf = _tiny_pdf(tmp_path / "vt.pdf", ["Potassium 4.2 g/dL 3.5-5.1", "Sodium 139 141 mmol/L 135-145",
                                          "Chloride 101 mmol/L 98-107", "Glucose 12.0.4 mg/dL"],
                    size=(420.0, 120.0))
    res = v0.extract(_job(pdf, sex="male", age=40))
    rows = {r.name_raw: r for r in res.extracted}
    assert set(rows) == {"Potassium", "Sodium", "Chloride"}, list(rows)
    assert rows["Potassium"].needs_review and "NR0-VALUETYPE" in _codes(rows["Potassium"])
    assert rows["Sodium"].needs_review and {"NR0-VALUETYPE", "NR0-UNKNOWN"} <= _codes(rows["Sodium"])
    assert rows["Sodium"].value_num is None and rows["Sodium"].value_raw == "139 141"
    assert not rows["Chloride"].needs_review
    # the unreadable glucose line: counted only (v0 has no unread_line rows), stated once as a report item
    assert res.unread_lines == []
    assert ("NR0-UNREAD", "1 unread numeric line(s)") in res.report_review_items
    shown = res.payload["response"].warnings
    for code, text in res.report_review_items:  # each item is shown by the product and owned by no row
        assert code in ("NR0-REPORT-WARNING", "NR0-UNREAD")
        if code == "NR0-REPORT-WARNING":
            assert text in shown and not any(text.startswith(f"{n}:") for n in rows)
    assert not any("numeric line(s) could not be read" in t for _, t in res.report_review_items)


# ======================================================================== HA-02: fidelity

def _adult(pts, *, sex_known=True):
    return [p for p in pts if (p.input_sex is not None) == sex_known and p.input_age is not None
            and not p.is_paediatric]


HA02_CASES = (  # (family, builder module, index, scenario class, pages, sex known)
    ("IN-A", templates, 0, "critical", 2, True),   # method column, two pages, a critical
    ("G08", generic, 1, "mild", 2, True),          # blood + urine routine: merged legacy branch
    ("G08", generic, 4, "normal", 1, True),        # urine routine + spot electrolytes
    ("US-A", templates, 2, "severe", 1, False),    # flag column; sex not entered
)


def _response_multiset(lab_values) -> collections.Counter:
    return collections.Counter((d["test"], d["result"], d["unit"], d["flag"]) for d in lab_values)


def _adapter_multiset(res) -> collections.Counter:
    analysis = res.payload["analysis"]
    got = collections.Counter()
    for row in res.extracted:
        if row.origin == "legacy_urine":
            d = row.payload
            got[(d["test"], d["result"], d["unit"], d["flag"])] += 1
            continue
        sr = next(s for s in analysis.rows if s.parsed is row.payload)
        assert row.name_raw == sr.display_name and row.flag == sr.flag
        got[(sr.display_name, sr.value if sr.value is not None else sr.text_value, sr.unit, sr.flag)] += 1
    return got


@pytest.mark.parametrize("family,mod,index,cls,pages,sex_known", HA02_CASES,
                         ids=[f"{c[0]}-{c[2]}" for c in HA02_CASES])
def test_ha02_fidelity_with_run_report_pipeline(v0, no_ocr_boxes, tmp_path, family, mod, index, cls, pages,
                                                 sex_known):
    pt = _adult(sc.build_patients(80, random.Random(7)), sex_known=sex_known)[index]
    rep = mod.build_report(family, split="dev", index=index, patient=pt, scenario_class=cls, target_pages=pages)
    path = str(tmp_path / f"{family}.pdf")
    render.native_pdf(rep, path)
    assert rep.n_pages == pages
    res = _assert_fidelity(v0, path, pt.input_sex, pt.input_age, rep.n_pages)
    if family == "G08" and index == 1:
        assert res.meta["branch"] == "merged"


GOLDEN = REPO / "tests" / "fixtures" / "eval_golden"


@pytest.mark.parametrize("gt_path", sorted(GOLDEN.glob("*.gt.json")), ids=lambda p: p.name.split(".")[0])
def test_ha02_fidelity_on_golden_reports(v0, no_ocr_boxes, gt_path):
    gt = json.loads(gt_path.read_text(encoding="utf-8"))
    pdf = gt_path.with_name(gt_path.name.replace(".gt.json", ".native.pdf"))
    patient = gt["patient"]
    _assert_fidelity(v0, str(pdf), patient["input_sex"], patient["input_age"], len(gt["pages"]))


def _assert_fidelity(v0, path: str, sex, age, n_pages: int):
    import app.pipeline as pipeline

    # independent run of the product function, same offline configuration, no spies
    direct = pipeline.run_report_pipeline(path, report_type="lab", gender=sex, age=age)
    assert direct.status == "completed", direct.error

    res = v0.extract(_job(path, sex=sex, age=age, pages=tuple(range(1, n_pages + 1))))
    assert res.status == ("ok" if direct.lab_values else "empty")
    assert _adapter_multiset(res) == _response_multiset(direct.lab_values)
    assert res.pre_review_headline == _plan_headline(direct.risk_label)
    assert res.product_label == direct.risk_label
    assert [r.row_index for r in res.rows] == list(range(len(res.rows)))
    for row in res.extracted:
        if row.origin == "legacy_urine":  # plan 6.3.2 steps 5-7: no uncertainty signal, mapped flags
            assert not row.needs_review and row.specimen == "urine"
            raw = str(row.payload["flag"]).upper()
            key = row.analyte_key  # unmapped key: qualitative when the value is not a number (adapter docstring)
            qualitative = CATALOGUE[key].value_type == "qualitative" if key in CATALOGUE else row.value_num is None
            expected = {("ABNORMAL", True): "POSITIVE", ("NORMAL", True): "NEGATIVE",
                        ("ABNORMAL", False): "ABNORMAL", ("NORMAL", False): "NORMAL"}[(raw, qualitative)]
            assert row.flag == expected
        else:
            assert row.origin == "text"  # native text layer kept on every page
            assert row.source_page in range(1, n_pages + 1)
            assert row.needs_review == bool(row.review_reasons)
    return res


# ======================================================================== HA-03: hermetic environment

def test_ha03_hermetic_env_and_fast_urine_report(monkeypatch, no_ocr_boxes, tmp_path):
    import app.pipeline as pipeline
    import app.services.clinical_ner_service as ner
    import app.services.ingest_urinalysis_kb as kb
    import app.utils.retry as retry

    monkeypatch.setenv("GROQ_API_KEY", "dummy-not-a-real-key")
    sleeps: list[float] = []
    real_sleep = time.sleep
    monkeypatch.setattr(retry, "time", types.SimpleNamespace(sleep=lambda s: (sleeps.append(s), real_sleep(s))))
    seen: list[tuple] = []
    original_run = pipeline.run_report_pipeline

    def watched_run(*args, **kwargs):
        seen.append((os.environ.get("GROQ_API_KEY"), os.environ.get("HF_HUB_OFFLINE"),
                     os.environ.get("TRANSFORMERS_OFFLINE"), os.environ.get("OCR_ENGINE"),
                     ner._call_groq, kb.query_urinalysis_kb, pipeline._rag_service()))
        return original_run(*args, **kwargs)

    monkeypatch.setattr(pipeline, "run_report_pipeline", watched_run)
    urine = _tiny_pdf(tmp_path / "urine.pdf", [
        "URINE ROUTINE EXAMINATION", "Specific Gravity 1.020", "pH 6.0", "Protein Nil", "Glucose Nil",
        "Ketones Negative", "Nitrite Negative", "Leukocyte Esterase Negative",
    ], size=(420.0, 160.0))
    warm = _tiny_pdf(tmp_path / "warm.pdf", ["Sodium 139 mmol/L 135-145"])

    with PipelineV0Adapter(ocr_cache=None) as adapter:
        assert "GROQ_API_KEY" not in os.environ
        adapter.extract(_job(warm))  # first job of a process pays one-off model loading; not what HA-03 times
        t0 = time.perf_counter()
        res = adapter.extract(_job(urine, sex="female", age=34))
        elapsed = time.perf_counter() - t0

    # the run saw no key, the offline settings, and the three stubs
    assert seen and all(s[0] is None for s in seen)
    assert all(s[1:4] == ("1", "1", "paddle") for s in seen)
    assert all(s[4] is v0mod._groq_disabled and s[5] is v0mod._no_urinalysis_kb and s[6] is None for s in seen)
    assert sleeps == []  # no retry back-off (2 s + 4 s without the stub)
    assert elapsed < 2.0, elapsed
    # the urinalysis branch ran: legacy rows replace the engine rows (plan 6.3.2 step 5)
    assert res.meta["branch"] == "urine_only" and res.status == "ok"
    assert res.rows and all(r.origin == "legacy_urine" and not r.needs_review for r in res.rows)
    by_test = {r.payload["test"]: r for r in res.rows}
    for test in ("protein", "glucose", "ketones", "nitrite", "leukocyte_esterase"):
        assert by_test[test].analyte_key == f"urine_{test}"
        # Nil / Negative is NEGATIVE under the clinical rules (CL-29) and the plan's legacy mapping
        assert cl.qualitative_class(by_test[test].value_raw) == "NEGATIVE"
        assert by_test[test].flag == "NEGATIVE"
    # closing restored the environment the test set up
    assert os.environ.get("GROQ_API_KEY") == "dummy-not-a-real-key"
    assert ner._call_groq is not v0mod._groq_disabled and pipeline._rag_service is not v0mod._no_rag


_FRESH_PROCESS = r"""
import json, os, sys
from tools.eval.adapters import get_adapter
from tools.eval.adapters.base import EvalJob
adapter = get_adapter("pipeline_v0", ocr_cache=None)  # sets the hermetic env, then imports app
import app.pipeline as pipeline
import app.services.ocr_service as ocr_service
ocr_service.ocr_image = lambda image: []
seen = []
original = pipeline.run_report_pipeline
def watched(*a, **k):
    seen.append(os.environ.get("GROQ_API_KEY"))
    return original(*a, **k)
pipeline.run_report_pipeline = watched
res = adapter.extract(EvalJob("T901:native", "T901", "dev", "native", (sys.argv[1],), (1,), "male", 40))
print(json.dumps({"preimported": adapter.app_preimported, "seen": seen, "flags": [r.flag for r in res.rows]}))
"""


def test_ha03_fresh_process_inherits_key_but_run_sees_none(tmp_path):
    pdf = _tiny_pdf(tmp_path / "k.pdf", ["Potassium 6.9 mmol/L 3.5-5.1"])
    env = {**os.environ, "GROQ_API_KEY": "dummy-not-a-real-key", "PYTHONPATH": str(REPO)}
    out = subprocess.run([sys.executable, "-c", _FRESH_PROCESS, pdf], cwd=REPO, env=env, capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    got = json.loads(out.stdout.strip().splitlines()[-1])
    assert got["preimported"] is False  # the env was set before app was imported
    assert got["seen"] == [None]
    assert got["flags"] == ["CRITICAL_HIGH"]  # Table C: K 6.9 > 6.5


def test_ha03_missing_settings_is_a_config_error(tmp_path):
    env = {"GROQ_API_KEY": "x"}
    with pytest.raises(v0mod.HarnessConfigError) as err:
        v0mod.prepare_hermetic_env(env, dotenv_path=tmp_path / "absent.env")
    assert err.value.exit_code == 2 and "SECRET_KEY" in str(err.value) and "POSTGRES_PASSWORD" in str(err.value)
    assert env == {"GROQ_API_KEY": "x"}  # nothing changed

    dotenv = tmp_path / ".env"
    dotenv.write_text("SECRET_KEY=throwaway\nPOSTGRES_PASSWORD=throwaway\n")
    previous = v0mod.prepare_hermetic_env(env, dotenv_path=dotenv)
    assert "GROQ_API_KEY" not in env
    assert env == {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "WHO_MULTIMODAL_STARTUP_INGEST": "false",
                   "OCR_ENGINE": "paddle"}
    v0mod.restore_env(previous, env)
    assert env == {"GROQ_API_KEY": "x"}


# ======================================================================== HA-04: OCR cache

def test_ha04_ocr_cache_second_run_makes_no_paddle_calls(monkeypatch, tmp_path):
    pytest.importorskip("paddleocr")
    import app.services.ocr_service as ocr_service

    pt = _adult(sc.build_patients(80, random.Random(7)), sex_known=True)[2]
    rep = templates.build_report("US-A", split="dev", index=2, patient=pt, scenario_class="severe", target_pages=1)
    native = str(tmp_path / "native.pdf")
    scan = str(tmp_path / "scan.pdf")
    render.native_pdf(rep, native)
    render.render_scan(native, scan, seed=20261119)
    job = _job(scan, sex=pt.input_sex, age=pt.input_age, rendering="scan")

    paddle_calls: list[tuple] = []
    real_ocr_image = ocr_service.ocr_image

    def counting_ocr_image(image):
        paddle_calls.append(image.shape)
        return real_ocr_image(image)

    monkeypatch.setattr(ocr_service, "ocr_image", counting_ocr_image)
    cache = OcrCache(tmp_path / "ocr")
    with PipelineV0Adapter(ocr_cache=cache) as adapter:
        first = adapter.extract(job)
        assert len(paddle_calls) == rep.n_pages == 1
        assert first.timing["ocr_cache"] == {"hits": 0, "misses": 1}
        assert first.meta["ocr_methods"] == ["paddle_ocr"]  # a scan has no text layer
        assert first.status == "ok" and first.extracted, "Paddle read nothing from the scan"

        second = adapter.extract(job)
        assert len(paddle_calls) == 1  # 0 Paddle calls on the second run
        assert second.timing["ocr_cache"] == {"hits": 1, "misses": 0}
        assert second == first and second.to_dict() == first.to_dict()
        assert [r.payload for r in second.rows] == [r.payload for r in first.rows]
    entries = list((tmp_path / "ocr").rglob("*.json"))
    assert len(entries) == 1 and entries[0].parent.name == entries[0].stem[:2]

    # a change to ocr_service.py moves the fingerprint, so the cache misses
    changed = tmp_path / "ocr_service.py"
    changed.write_text((REPO / "app" / "services" / "ocr_service.py").read_text() + "\n# changed\n")
    moved = compute_fingerprint(service_file=changed)
    assert moved != cache.fingerprint
    with PipelineV0Adapter(ocr_cache=OcrCache(tmp_path / "ocr", fingerprint=moved)) as adapter:
        third = adapter.extract(job)
    assert len(paddle_calls) == 2 and third.timing["ocr_cache"] == {"hits": 0, "misses": 1}


def test_ha04_fingerprint_covers_models_versions_and_salt(tmp_path):
    models = tmp_path / "paddleocr"
    shutil.copytree(REPO / "data" / "paddleocr", models)
    base_fp = compute_fingerprint(model_root=models)
    assert base_fp == compute_fingerprint(model_root=models)  # stable
    assert compute_fingerprint() == compute_fingerprint(model_root=REPO / "data" / "paddleocr")
    first = sorted(p for p in models.rglob("*") if p.is_file())[0]
    first.write_bytes(first.read_bytes() + b"\0")
    assert compute_fingerprint(model_root=models) != base_fp  # weights changed
    assert compute_fingerprint(salt="vaidya-eval-ocr-cache/2") != compute_fingerprint()
    parts = fingerprint_parts()
    assert {"version:paddleocr", "version:paddlepaddle", "ocr_service", "models", "salt"} <= set(parts)


def test_ha04_cache_keys_on_preprocessed_image_and_wrapped_callable(tmp_path):
    np = pytest.importorskip("numpy")
    cache = OcrCache(tmp_path / "c", fingerprint="f" * 64)
    calls = []

    def fake_paddle(image):
        calls.append(1)
        return [{"text": "Potassium", "confidence": np.float32(0.5), "bbox": ((1.0, 2.0), (3.0, 2.0))}]

    wrapped = cache.wrap(fake_paddle)
    img = np.zeros((4, 5, 3), dtype=np.uint8)
    a = wrapped(img)
    b = wrapped(img.copy())
    assert calls == [1] and a == b == [{"text": "Potassium", "confidence": 0.5, "bbox": [[1.0, 2.0], [3.0, 2.0]]}]
    img2 = img.copy()
    img2[0, 0, 0] = 1
    wrapped(img2)
    wrapped(img.astype(np.uint16))
    assert len(calls) == 3  # pixel and dtype changes miss

    def other_engine(image):  # e.g. a test stub: its own key space, never served the real engine's entries
        calls.append(2)
        return []

    assert cache.wrap(other_engine)(img) == [] and calls[-1] == 2
    assert cache.stats() == {"hits": 1, "misses": 4}


# ======================================================================== scorer, registries, contracts

def _confirmed(key, value, unit, lo, hi, *, name=None, specimen="serum", text=None):
    return ConfirmedRow(name_raw=name or CATALOGUE[key].display, analyte_key=key, value_num=value, text_value=text,
                        unit_raw=unit, ref_low=lo, ref_high=hi, specimen=specimen, origin="edited",
                        value_raw="" if value is None else str(value))


def test_engine_floor_scorer_v0_headlines_match_clinical_rules():
    scorer = get_scorer(DEFAULT_SCORER)
    oracle = cl.ClinicalRulesScorer()
    sets = {
        "critical": [_confirmed("potassium", 6.8, "mmol/L", 3.5, 5.1), _confirmed("sodium", 139, "mmol/L", 135, 145)],
        "abnormal": [_confirmed("alt", 85, "U/L", 7, 56)],
        "normal": [_confirmed("sodium", 139, "mmol/L", 135, 145), _confirmed("potassium", 4.2, "mmol/L", 3.5, 5.1)],
        "urine": [_confirmed("urine_protein", None, "", None, None, name="Protein", specimen="urine", text="Trace")],
    }
    expected = {"critical": "critical", "abnormal": "abnormal", "normal": "normal", "urine": "abnormal"}  # CL-28/29
    for name, rows in sets.items():
        want = oracle.score(rows, "male", 40).headline
        assert want == expected[name], name
        got = scorer.score(rows, "male", 40)
        assert isinstance(got, ScoreResult) and got.headline == want, (name, got)
        assert len(got.row_flags) == len(rows)
    assert scorer.score(sets["critical"], "male", 40).row_flags[0] == "CRITICAL_HIGH"


def test_engine_floor_scorer_uses_payload_for_accepted_rows_and_aligns_flags():
    from app.services.lab_engine import parse_report_text

    (k,), _, _ = parse_report_text("Potassium 6.9 mmol/L 3.5-5.1")
    extracted = ExtractedRow(row_index=0, name_raw="Potassium", analyte_key="potassium", value_raw="6.9",
                             value_num=6.9, unit_raw="mmol/L", ref_low=3.5, ref_high=5.1, specimen="serum",
                             flag="CRITICAL_HIGH", payload=k)
    accepted = ConfirmedRow.from_extracted(extracted)
    assert accepted.origin == "accepted" and accepted.payload is k and accepted.extracted_index == 0
    duplicate = ConfirmedRow.from_extracted(extracted)
    legacy = ConfirmedRow(name_raw="Protein", analyte_key="urine_protein", value_raw="nil", text_value="nil",
                          specimen="urine", payload={"test": "protein", "section": "dipstick"})
    res = EngineFloorScorerV0().score([accepted, duplicate, legacy], None, None)
    assert res.headline == "critical"  # Table C: K 6.9 > 6.5
    assert res.row_flags == ["CRITICAL_HIGH", "CRITICAL_HIGH", "NEGATIVE"]  # the merged duplicate takes the survivor
    assert k.value == 6.9 and k.printed_flag is None  # the payload itself was not mutated


def test_score_result_reconciles_with_oracle_score_structurally():
    import dataclasses

    assert [f.name for f in dataclasses.fields(cl.OracleScore)] == [f.name for f in dataclasses.fields(ScoreResult)]
    oracle = cl.ClinicalRulesScorer()
    out = oracle.score([_confirmed("potassium", 6.8, "mmol/L", 3.5, 5.1)])
    assert isinstance(out, base.ScoreResultLike)
    coerced = ScoreResult.coerce(out)
    assert (coerced.headline, coerced.row_flags, coerced.label_raw) == (out.headline, out.row_flags, out.label_raw)
    assert isinstance(oracle, base.Scorer) and isinstance(EngineFloorScorerV0(), base.Scorer)
    with pytest.raises(TypeError):
        ScoreResult.coerce(object())
    assert "clinical_rules" not in SCORERS  # oracle: metric tests and M5-oracle only (plan 6.4)


def test_registries_and_protocols(v0):
    assert DEFAULT_ADAPTER == "pipeline_v0" and set(ADAPTERS) == {"pipeline_v0"}
    assert DEFAULT_SCORER == "engine_floor_v0" and set(SCORERS) == {"engine_floor_v0"}
    assert isinstance(v0, base.ExtractionAdapter)
    assert (v0.name, v0.version) == ("pipeline_v0", "1")
    with pytest.raises(KeyError):
        get_scorer("nope")


def test_label_mapping_matches_the_plan_table():
    for label, want in PLAN_LABEL_TABLE.items():
        assert base.headline_from_label(label) == want
        assert base.headline_from_label(label.upper()) == want
    assert base.headline_from_label(None) == "unknown"
    assert base.headline_from_label("uncertain") == "unknown"  # never read an unrecognised label as normal


def test_legacy_urine_key_map_is_the_plan_map():
    plan = {"ph": "urine_ph", "specific_gravity": "specific_gravity", "protein": "urine_protein",
            "glucose": "urine_glucose", "ketones": "urine_ketones", "blood": "urine_blood",
            "nitrite": "urine_nitrite", "leukocyte_esterase": "urine_leukocyte_esterase",
            "bilirubin": "urine_bilirubin", "urobilinogen": "urine_urobilinogen", "wbc": "urine_pus_cells",
            "rbc": "urine_rbc", "epithelial_cells": "urine_epithelial", "rbc_casts": "urine_casts",
            "granular_casts": "urine_casts", "bacteria": "urine_bacteria"}
    for test, key in plan.items():
        assert v0mod.legacy_analyte_key(test) == key, test
        assert key in CATALOGUE, key  # every target is a GT key
    assert v0mod.legacy_analyte_key("colour") is None
    assert v0mod.legacy_flag("ABNORMAL", "urine_protein", None) == "POSITIVE"
    assert v0mod.legacy_flag("NORMAL", "urine_nitrite", None) == "NEGATIVE"
    assert v0mod.legacy_flag("ABNORMAL", "urine_pus_cells", None) == "ABNORMAL"  # never HIGH or LOW
    assert v0mod.legacy_flag("NORMAL", "specific_gravity", 1.02) == "NORMAL"


def test_eval_job_and_result_serialisation():
    job = EvalJob("D001:photo:p2", "D001", "dev", "photo", [pathlib.Path("x.png")], [2], "female", 30)
    assert job.files == ("x.png",) and job.pages == (2,)
    with pytest.raises(ValueError):
        EvalJob("j", "r", "dev", "native", ("a.pdf",), (1,), "F", None)
    row = ExtractedRow(row_index=0, name_raw="K", review_reasons=[base.Reason("NR0-CRITICAL", "x")], payload=object())
    res = base.ExtractionResult(job_id="j", status="ok", rows=[row], timing={"pipeline_s": 1.0}, payload=object())
    d = res.to_dict()
    assert "payload" not in d and "timing" not in d and "payload" not in d["rows"][0]
    assert d["rows"][0]["review_reasons"] == [["NR0-CRITICAL", "x"]]
    assert "timing" in res.to_dict(include_timing=True)


# ======================================================================== import boundary (plan 2.2, GN-11)

ADAPTER_SIDE = {"tools/eval/adapters/__init__.py", "tools/eval/adapters/base.py",
                "tools/eval/adapters/pipeline_v0.py", "tools/eval/ocr_cache.py"}
PRODUCT = ("app", "clinical_reference")
ADAPTER_PKGS = ("tools.eval.adapters", "tools.eval.ocr_cache")


def _module_name(path: pathlib.Path) -> str:
    rel = path.relative_to(REPO).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _imports(path: pathlib.Path) -> list[tuple[str, bool]]:
    """(absolute module, imported at module level) for every import statement in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    pkg = _module_name(path).split(".")
    if path.name != "__init__.py":
        pkg = pkg[:-1]
    out: list[tuple[str, bool]] = []

    def visit(node: ast.AST, top: bool) -> None:
        for child in ast.iter_child_nodes(node):
            inner = top and not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if isinstance(child, ast.Import):
                out.extend((a.name, top) for a in child.names)
            elif isinstance(child, ast.ImportFrom):
                if child.level:
                    stem = pkg[: len(pkg) - child.level + 1]
                    mod = ".".join(stem + ([child.module] if child.module else []))
                else:
                    mod = child.module or ""
                out.append((mod, top))
                out.extend((f"{mod}.{a.name}", top) for a in child.names)
            visit(child, inner)

    visit(tree, True)
    return out


def _hits(mod: str, prefixes: tuple[str, ...]) -> bool:
    return any(mod == p or mod.startswith(p + ".") for p in prefixes)


def test_judge_and_gt_side_never_import_app_or_adapters():
    files = sorted((REPO / "tools" / "eval").rglob("*.py"))
    assert files
    bad = []
    for f in files:
        rel = f.relative_to(REPO).as_posix()
        if rel in ADAPTER_SIDE:
            continue
        forbidden = PRODUCT if rel == "tools/eval/run.py" else PRODUCT + ADAPTER_PKGS  # run.py uses the registry
        bad += [(rel, m) for m, _ in _imports(f) if _hits(m, forbidden)]
    assert not bad, bad


def test_adapter_side_imports_app_only_lazily():
    bad = []
    for rel in sorted(ADAPTER_SIDE):
        bad += [(rel, m) for m, top in _imports(REPO / rel) if top and _hits(m, PRODUCT)]
    assert not bad, bad
    # and importing the whole adapter layer in a fresh interpreter loads no product module
    code = ("import sys, tools.eval.adapters, tools.eval.adapters.base, tools.eval.adapters.pipeline_v0, "
            "tools.eval.ocr_cache; print(sorted(m for m in sys.modules if m == 'app' or m.startswith('app.') "
            "or m == 'clinical_reference'))")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
