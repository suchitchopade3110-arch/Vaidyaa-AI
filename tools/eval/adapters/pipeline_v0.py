"""Adapter for the current product pipeline (v0) and the v0 engine-floor scorer (P1 plan 6.3, 6.4).

``PipelineV0Adapter`` observes the real ``app.pipeline.run_report_pipeline``; nothing is re-implemented and no
product file is modified (G10). Per process it sets a hermetic, offline environment before importing ``app``
(6.3.1) and stubs the three network-ish services by monkeypatching module attributes in the harness process only:
RAG, the Groq call and the urinalysis knowledge base. Around each job it installs spies on ``_run_ocr_safe``,
``ocr_service.extract_text_from_file`` and ``app.pipeline.analyze_lab_rows`` (the engine's ``LabAnalysis``, whose
``ParsedRow``s hold the as-printed value and unit) plus the OCR cache on ``ocr_service.ocr_image``, then maps what
the product would show into ``ExtractedRow``s with the documented v0 needs_review rules (6.3.3).

What the clinician sees is decided from the response, never re-derived: engine rows are the captured
``analysis.rows`` (checked one-to-one against ``response.lab_values``), legacy urinalysis dicts follow them (merged
branch) or replace them (urine-only branch).

``EngineFloorScorerV0`` scores confirmed rows with the engine (``analyze_lab_rows`` + ``apply_risk_floor`` and the
critical ceiling). Known limitation, reported in report.md: the ensemble can raise a no-anomaly report to abnormal
and v0 does not model it (visible in M5-pre). P2 replaces both with ``ExtractedRowsAdapter`` / ``confirmed_v1``.

``app`` is imported only lazily, inside adapter / scorer construction (plan 2.2).
"""

from __future__ import annotations

import contextlib
import copy
import os
import pathlib
import re
import time
from collections.abc import Iterator, MutableMapping
from typing import Any, Sequence

from tools.eval.adapters.base import (
    EvalJob,
    ExtractedRow,
    ExtractionResult,
    Reason,
    ScoreResult,
    headline_from_label,
)
from tools.eval.catalogue import CATALOGUE

ADAPTER_NAME = "pipeline_v0"
ADAPTER_VERSION = "1"
SCORER_NAME = "engine_floor_v0"
SCORER_VERSION = "1"

# ---------------------------------------------------------------- hermetic environment (6.3.1)

HERMETIC_POP = ("GROQ_API_KEY",)
HERMETIC_SET = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "WHO_MULTIMODAL_STARTUP_INGEST": "false",
    "OCR_ENGINE": "paddle",
}
REQUIRED_SETTINGS = ("SECRET_KEY", "POSTGRES_PASSWORD")


class HarnessConfigError(RuntimeError):
    """A harness configuration error; the CLI maps it to exit code 2."""

    exit_code = 2


class AdapterFidelityError(RuntimeError):
    """The spies no longer line up with what the product returned (e.g. a pipeline refactor). Loud by design."""


def _dotenv_defines(path: pathlib.Path, name: str) -> bool:
    """True when the ``.env`` file pydantic-settings reads defines ``name`` with a non-empty value."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line.startswith(f"{name}="):
            continue
        return bool(line.split("=", 1)[1].strip().strip("'\""))
    return False


def prepare_hermetic_env(
    environ: MutableMapping[str, str] | None = None, dotenv_path: str | os.PathLike | None = None
) -> dict[str, str | None]:
    """Set the offline configuration; return the previous values of every variable touched.

    Pops ``GROQ_API_KEY`` and sets ``HF_HUB_OFFLINE``, ``TRANSFORMERS_OFFLINE``, ``WHO_MULTIMODAL_STARTUP_INGEST`` and
    ``OCR_ENGINE``. Raises ``HarnessConfigError`` (exit 2) when ``SECRET_KEY`` or ``POSTGRES_PASSWORD`` is in neither
    the environment nor ``.env``: the app settings need them even though no database is touched. Nothing is
    changed when it raises."""
    env = os.environ if environ is None else environ
    dotenv = pathlib.Path(dotenv_path) if dotenv_path is not None else pathlib.Path.cwd() / ".env"
    missing = [k for k in REQUIRED_SETTINGS if not env.get(k) and not _dotenv_defines(dotenv, k)]
    if missing:
        raise HarnessConfigError(
            f"eval harness: {', '.join(missing)} not set (environment or .env). The app settings require "
            "them at import even though the harness touches no database; set throwaway values, e.g. as CI does."
        )
    previous: dict[str, str | None] = {k: env.get(k) for k in (*HERMETIC_POP, *HERMETIC_SET)}
    for k in HERMETIC_POP:
        env.pop(k, None)
    env.update(HERMETIC_SET)
    return previous


def restore_env(previous: dict[str, str | None], environ: MutableMapping[str, str] | None = None) -> None:
    env = os.environ if environ is None else environ
    for k, v in previous.items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = v


class _Patches:
    """Ordered module-attribute patches, restored in reverse."""

    def __init__(self) -> None:
        self._saved: list[tuple[Any, str, Any]] = []

    def set(self, obj: Any, attr: str, value: Any) -> Any:
        old = getattr(obj, attr)
        self._saved.append((obj, attr, old))
        setattr(obj, attr, value)
        return old

    def restore(self) -> None:
        while self._saved:
            obj, attr, old = self._saved.pop()
            setattr(obj, attr, old)


def _no_rag() -> None:
    return None


def _groq_disabled(*_args: Any, **_kwargs: Any) -> str:
    raise RuntimeError("Groq is disabled in the eval harness (offline, no-LLM configuration)")


def _no_urinalysis_kb(*_args: Any, **_kwargs: Any) -> list:
    return []


# ---------------------------------------------------------------- v0 needs_review rules (6.3.3)

NR0_UNKNOWN = "NR0-UNKNOWN"
NR0_VALUETYPE = "NR0-VALUETYPE"
NR0_NOANALYTE = "NR0-NOANALYTE"
NR0_FLAGCONFLICT = "NR0-FLAGCONFLICT"
NR0_WARNING = "NR0-WARNING"
NR0_CRITICAL = "NR0-CRITICAL"
NR0_CODES = (NR0_UNKNOWN, NR0_VALUETYPE, NR0_NOANALYTE, NR0_FLAGCONFLICT, NR0_WARNING, NR0_CRITICAL)
# report-level items (6.3.2 step 11)
NR0_REPORT_WARNING = "NR0-REPORT-WARNING"
NR0_UNREAD = "NR0-UNREAD"

CRITICAL_TEXT = "Looks critical — please verify"
_BAD_VALUE_TYPES = ("ambiguous", "unit_mismatch", "invalid")


def attributed_warnings(row: Any, warnings: Sequence[str]) -> list[str]:
    """The row's own parse warnings plus every engine warning that starts with ``display_name + ':'``.

    Attribution is by name, so a warning reaches every row of that name (plan 6.3.3, NR0-WARNING)."""
    out: list[str] = []
    prefix = f"{row.display_name}:"
    for w in list(getattr(row.parsed, "warnings", None) or []) + [w for w in warnings if w.startswith(prefix)]:
        if w not in out:
            out.append(w)
    return out


def v0_review_reasons(row: Any, attributed: Sequence[str]) -> list[Reason]:
    """The v0 rules, derived only from signals the current product shows the clinician (r = ScoredRow)."""
    reasons: list[Reason] = []
    if row.flag == "UNKNOWN":
        reasons.append(Reason(NR0_UNKNOWN, f"Not assessed: {attributed[0]}" if attributed else "Not assessed"))
    if row.value_type in _BAD_VALUE_TYPES:
        reasons.append(Reason(NR0_VALUETYPE, row.value_type))
    if row.analyte_key is None and row.value_type in ("numeric", "qualified"):
        reasons.append(Reason(NR0_NOANALYTE, "Unknown test"))
    if row.discrepancy:
        reasons.append(Reason(NR0_FLAGCONFLICT, str(row.discrepancy)))
    reasons.extend(Reason(NR0_WARNING, w) for w in attributed)
    if row.flag in ("CRITICAL_HIGH", "CRITICAL_LOW"):
        reasons.append(Reason(NR0_CRITICAL, CRITICAL_TEXT))
    return reasons


# ---------------------------------------------------------------- legacy urinalysis rows (6.3.2 steps 5-7)

LEGACY_KEY_MAP = {
    "ph": "urine_ph",
    "specific_gravity": "specific_gravity",
    "protein": "urine_protein",
    "glucose": "urine_glucose",
    "ketones": "urine_ketones",
    "blood": "urine_blood",
    "nitrite": "urine_nitrite",
    "leukocyte_esterase": "urine_leukocyte_esterase",
    "bilirubin": "urine_bilirubin",
    "urobilinogen": "urine_urobilinogen",
    "wbc": "urine_pus_cells",
    "rbc": "urine_rbc",
    "epithelial_cells": "urine_epithelial",
    "bacteria": "urine_bacteria",
}


def legacy_analyte_key(test: str) -> str | None:
    t = str(test or "").strip().lower().replace(" ", "_")
    if t == "casts" or t.endswith("_casts"):
        return "urine_casts"
    return LEGACY_KEY_MAP.get(t)


def legacy_flag(raw_flag: Any, analyte_key: str | None, value_num: float | None) -> str:
    """Legacy NORMAL / ABNORMAL in the GT vocabulary.

    Qualitative analytes: ABNORMAL -> POSITIVE, NORMAL -> NEGATIVE. Numeric ones: ABNORMAL stays "ABNORMAL" (no
    direction, so it never equals HIGH or LOW) and NORMAL -> NORMAL. The value type comes from the GT catalogue
    for a mapped key; an unmapped row is qualitative when its value is not a number."""
    flag = str(raw_flag or "").strip().upper()
    a = CATALOGUE.get(analyte_key) if analyte_key else None
    qualitative = a.value_type == "qualitative" if a is not None else value_num is None
    if flag == "ABNORMAL":
        return "POSITIVE" if qualitative else "ABNORMAL"
    if flag == "NORMAL":
        return "NEGATIVE" if qualitative else "NORMAL"
    return flag or "UNKNOWN"


def _is_engine_dict(d: dict) -> bool:
    return "range_source" in d or "analyte_key" in d


def _is_legacy_dict(d: dict) -> bool:
    return "section" in d and not _is_engine_dict(d)


# ---------------------------------------------------------------- engine-row helpers

_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_INSUFFICIENT_RE = re.compile(r"Insufficient evidence.*review it manually", re.S)
_DEMOGRAPHICS_NOTE_RE = re.compile(r"^Patient (?:sex|age|sex and age) not provided:")
_SEX_CODE = {"male": "M", "female": "F"}


def fmt_num(x: float) -> str:
    text = repr(float(x))
    if "e" in text or "E" in text:
        text = f"{float(x):.10f}".rstrip("0").rstrip(".")
    elif text.endswith(".0"):
        text = text[:-2]
    return text


def printed_range(p: Any, sex: str | None, age: int | None) -> tuple[float | None, float | None]:
    """Effective printed range for this patient, in the printed unit (plan 6.3.2 step 4).

    ``ref_low``/``ref_high``, otherwise ``sex_ranges[input sex]``, otherwise the ``age_ranges`` band covering the
    input age, otherwise null."""
    if p.ref_low is not None or p.ref_high is not None:
        return p.ref_low, p.ref_high
    code = _SEX_CODE.get(sex or "")
    if p.sex_ranges and code and code in p.sex_ranges:
        lo, hi = p.sex_ranges[code]
        return lo, hi
    if p.age_ranges and age is not None:
        band = next((b for b in p.age_ranges if b[0] <= age <= b[1]), None)
        if band is not None:
            return band[2], band[3]
    return None, None


def specimen_of(p: Any, analyte_key: str | None) -> str:
    section = (getattr(p, "section", "") or "").lower()
    key = analyte_key or ""
    if "urine" in section or key.startswith("urine_") or key == "specific_gravity":
        return "urine"
    if "csf" in section or key.startswith("csf_"):
        return "csf"
    if "fluid" in section:
        return "fluid"
    return "serum"


def _value_fields(p: Any) -> tuple[str, float | None, str | None]:
    """(value_raw, value_num, text_value) as read."""
    text_value = p.text_value
    if p.result_range is not None:
        lo, hi = p.result_range
        rr = f"{fmt_num(lo)}-{fmt_num(hi)}"
        return rr, p.value, text_value if text_value is not None else rr
    if p.value is not None:
        return f"{p.qualifier or ''}{fmt_num(p.value)}", p.value, text_value
    if text_value is not None:
        return str(text_value), None, text_value
    if p.candidates:
        return " ".join(fmt_num(c) for c in p.candidates), None, None
    return "", None, None


def _squash(s: str) -> str:
    return " ".join(str(s or "").split())


def _locate_page(line: str, pages: Sequence[dict]) -> int | None:
    needle = _squash(line)
    if not needle:
        return None
    for pg in pages:
        if needle in _squash(pg.get("text", "")):
            return int(pg["page"])
    return None


def _origin_and_page(p: Any, pages: Sequence[dict], is_image: bool) -> tuple[str, int | None]:
    page = _locate_page(getattr(p, "line", ""), pages) if pages else None
    if page is None and len(pages) == 1:
        page = int(pages[0]["page"])
    source = getattr(p, "source", "text")
    if source == "column_parser":
        return "column", page
    if source == "csv":
        return "csv", page
    methods = [pg.get("method") for pg in pages]
    if page is not None:
        method = next((pg.get("method") for pg in pages if int(pg["page"]) == page), None)
    elif len(set(methods)) == 1:
        method = methods[0]
    elif methods:
        method = "paddle_ocr" if "paddle_ocr" in methods else "native"
    else:  # Paddle failed and run_ocr used its legacy fallback: native text for PDFs, Tesseract for images
        method = "paddle_ocr" if is_image else "native"
    return ("text" if method == "native" else "ocr"), page


class _Capture:
    def __init__(self) -> None:
        self.raw_text: str = ""
        self.ocr_confidence: float | None = None
        self.pages: list[dict] = []
        self.ocr_error: str | None = None
        self.analyses: list[Any] = []


# ---------------------------------------------------------------- the adapter

class PipelineV0Adapter:
    """The current product, observed (plan 6.3). Use as a context manager, or call ``close()`` to undo the stubs
    and restore the environment."""

    name = ADAPTER_NAME
    version = ADAPTER_VERSION

    def __init__(self, *, ocr_cache: Any = "default", environ: MutableMapping[str, str] | None = None,
                 dotenv_path: str | os.PathLike | None = None) -> None:
        import sys

        self._environ = os.environ if environ is None else environ
        self.app_preimported = "app" in sys.modules
        self._env_previous = prepare_hermetic_env(self._environ, dotenv_path)
        self._stubs = _Patches()
        try:
            import app.pipeline as pipeline  # lazy: only after the hermetic env is in place
            import app.services.clinical_ner_service as ner
            import app.services.ingest_urinalysis_kb as kb
            import app.services.ocr_service as ocr_service

            self._pipeline = pipeline
            self._ocr_service = ocr_service
            self._stubs.set(pipeline, "_rag_service", _no_rag)
            self._stubs.set(ner, "_call_groq", _groq_disabled)
            self._stubs.set(kb, "query_urinalysis_kb", _no_urinalysis_kb)
            self.cache = self._make_cache(ocr_cache)
        except BaseException:
            self.close()
            raise
        self._closed = False

    @staticmethod
    def _make_cache(spec: Any):
        from tools.eval.ocr_cache import DEFAULT_DIR, OcrCache

        if spec is None or spec is False or spec == "off":
            return None
        if isinstance(spec, OcrCache):
            return spec
        if spec == "default" or spec is True:
            return OcrCache(DEFAULT_DIR)
        return OcrCache(spec)

    # -- lifecycle
    def close(self) -> None:
        self._stubs.restore()
        if getattr(self, "_env_previous", None) is not None:
            restore_env(self._env_previous, self._environ)
            self._env_previous = None
        self._closed = True

    def __enter__(self) -> "PipelineV0Adapter":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- per-job spies
    @contextlib.contextmanager
    def _job_spies(self, cap: _Capture) -> Iterator[None]:
        pipeline, ocr_service = self._pipeline, self._ocr_service
        patches = _Patches()
        run_ocr_safe = pipeline._run_ocr_safe
        extract_file = ocr_service.extract_text_from_file
        analyze = pipeline.analyze_lab_rows

        def spy_run_ocr_safe(file_path: str):
            out = run_ocr_safe(file_path)
            cap.raw_text = getattr(out, "raw_text", "") or ""
            cap.ocr_confidence = getattr(out, "confidence", None)
            return out

        def spy_extract_text_from_file(file, file_type: str = "auto"):
            try:
                result = extract_file(file, file_type)
            except Exception as exc:
                cap.ocr_error = type(exc).__name__
                raise
            pages = result.get("pages")
            if pages:
                cap.pages = [{"page": int(pg.get("page", i + 1)), "method": pg.get("method", ""),
                              "text": pg.get("text", "")} for i, pg in enumerate(pages)]
            else:  # a single image
                cap.pages = [{"page": 1, "method": "paddle_ocr", "text": result.get("full_text", "")}]
            return result

        def spy_analyze_lab_rows(*args: Any, **kwargs: Any):
            analysis = analyze(*args, **kwargs)
            cap.analyses.append(analysis)
            return analysis

        try:
            patches.set(pipeline, "_run_ocr_safe", spy_run_ocr_safe)
            patches.set(ocr_service, "extract_text_from_file", spy_extract_text_from_file)
            patches.set(pipeline, "analyze_lab_rows", spy_analyze_lab_rows)
            if self.cache is not None:
                patches.set(ocr_service, "ocr_image", self.cache.wrap(ocr_service.ocr_image))
            yield
        finally:
            patches.restore()

    # -- extraction
    def extract(self, job: EvalJob) -> ExtractionResult:
        if self._closed:
            raise RuntimeError("PipelineV0Adapter is closed")
        if len(job.files) != 1:
            raise ValueError(f"{job.job_id}: a v0 job has exactly one input file, got {len(job.files)}")
        self._environ.pop("GROQ_API_KEY", None)  # stays hermetic even if something re-inserted it
        cap = _Capture()
        h0, m0 = (self.cache.hits, self.cache.misses) if self.cache is not None else (0, 0)
        t0 = time.perf_counter()
        with self._job_spies(cap):
            response = self._pipeline.run_report_pipeline(
                job.files[0], report_type="lab", gender=job.sex, age=job.age,
            )
        elapsed = time.perf_counter() - t0
        result = self._build_result(job, response, cap)
        result.timing = {"pipeline_s": round(elapsed, 4)}
        if self.cache is not None:
            result.timing["ocr_cache"] = {"hits": self.cache.hits - h0, "misses": self.cache.misses - m0}
        return result

    def _build_result(self, job: EvalJob, response: Any, cap: _Capture) -> ExtractionResult:
        if len(cap.analyses) > 1:
            raise AdapterFidelityError(f"analyze_lab_rows ran {len(cap.analyses)} times in one job")
        analysis = cap.analyses[0] if cap.analyses else None
        completed = str(getattr(response, "status", "")) == "completed"
        lab_values = list(getattr(response, "lab_values", None) or []) if completed else []
        engine_dicts = [d for d in lab_values if isinstance(d, dict) and _is_engine_dict(d)]
        legacy_dicts = [d for d in lab_values if isinstance(d, dict) and _is_legacy_dict(d)]
        if len(engine_dicts) + len(legacy_dicts) != len(lab_values):
            raise AdapterFidelityError("response.lab_values holds rows that are neither engine nor legacy urine dicts")
        if any(_is_engine_dict(d) for d in lab_values[len(engine_dicts):]):
            raise AdapterFidelityError("engine rows after legacy urine rows in response.lab_values")

        is_image = pathlib.Path(job.files[0]).suffix.lower() in (".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff")
        pages = cap.pages
        if is_image and len(job.pages) == 1:  # photo job: the image is page n of the report
            pages = [{**pg, "page": job.pages[0]} for pg in pages]
        warnings = list(getattr(analysis, "warnings", None) or [])

        rows: list[ExtractedRow] = []
        shown_engine_rows: list[Any] = []
        if engine_dicts:
            scored = list(getattr(analysis, "rows", None) or [])
            if len(scored) != len(engine_dicts):
                raise AdapterFidelityError(
                    f"captured {len(scored)} engine rows but the response shows {len(engine_dicts)}")
            for sr, d in zip(scored, engine_dicts):
                shown = sr.value if sr.value is not None else sr.text_value
                if (d.get("test"), d.get("result"), d.get("unit"), d.get("flag")) != (
                        sr.display_name, shown, sr.unit, sr.flag):
                    raise AdapterFidelityError("captured engine row differs from the response row")
                rows.append(self._engine_row(len(rows), sr, warnings, job, pages, is_image))
            shown_engine_rows = scored
        for d in legacy_dicts:
            rows.append(self._legacy_row(len(rows), d, pages))

        items = self._report_items(response, analysis, shown_engine_rows)
        explanation = " ".join(str(getattr(response, a, "") or "") for a in ("explanation", "plain_language_summary"))
        review_all = (not completed) or (not lab_values and bool(_INSUFFICIENT_RE.search(explanation)))
        status = "failed" if not completed else ("ok" if rows else "empty")
        branch = ("merged" if legacy_dicts else "engine") if engine_dicts else ("urine_only" if legacy_dicts else "none")
        meta = {
            "branch": branch,
            "ocr_methods": [pg["method"] for pg in pages] or (["fallback"] if cap.ocr_error else []),
            "ocr_confidence": getattr(response, "ocr_confidence", None),
            "panel_type": getattr(response, "panel_type", None),
            "unparsed_numeric_lines": int(getattr(analysis, "unparsed_numeric_lines", 0) or 0),
        }
        if not completed:
            meta["error"] = "pipeline_failed"
        return ExtractionResult(
            job_id=job.job_id, status=status, rows=rows, report_review_items=items, review_all=review_all,
            pre_review_headline=headline_from_label(getattr(response, "risk_label", None)),
            product_label=getattr(response, "risk_label", None), raw_text=cap.raw_text, adapter=self.name,
            adapter_version=self.version, meta=meta, payload={"response": response, "analysis": analysis},
        )

    def _engine_row(self, index: int, sr: Any, warnings: list[str], job: EvalJob, pages: list[dict],
                    is_image: bool) -> ExtractedRow:
        p = sr.parsed
        value_raw, value_num, text_value = _value_fields(p)
        lo, hi = printed_range(p, job.sex, job.age)
        reasons = v0_review_reasons(sr, attributed_warnings(sr, warnings))
        origin, page = _origin_and_page(p, pages, is_image)
        return ExtractedRow(
            row_index=index, kind="row", name_raw=sr.display_name, analyte_key=sr.analyte_key, value_raw=value_raw,
            value_num=value_num, qualifier=p.qualifier, text_value=text_value, unit_raw=(p.unit or "").strip(),
            ref_low=lo, ref_high=hi, ref_text=p.ref_text, printed_flag=p.printed_flag,
            specimen=specimen_of(p, sr.analyte_key), flag=sr.flag, needs_review=bool(reasons),
            review_reasons=reasons, confidence=None, origin=origin, source_page=page, source_text=p.line or "",
            payload=p,
        )

    @staticmethod
    def _legacy_row(index: int, d: dict, pages: list[dict]) -> ExtractedRow:
        test = str(d.get("test") or "")
        key = legacy_analyte_key(test)
        raw = d.get("result", d.get("value"))
        value_raw = "" if raw is None else str(raw).strip()
        value_num = float(value_raw) if _NUMBER_RE.match(value_raw) else None
        unit = str(d.get("unit") or "").strip()
        name = str(d.get("field") or test)
        ref = d.get("reference")
        return ExtractedRow(
            row_index=index, kind="row", name_raw=name, analyte_key=key, value_raw=value_raw, value_num=value_num,
            qualifier=None, text_value=None if value_num is not None else (value_raw or None), unit_raw=unit,
            ref_low=None, ref_high=None, ref_text=None if ref in (None, "", "--") else str(ref), printed_flag=None,
            specimen="urine", flag=legacy_flag(d.get("flag"), key, value_num), needs_review=False, review_reasons=[],
            confidence=None, origin="legacy_urine", source_page=int(pages[0]["page"]) if len(pages) == 1 else None,
            source_text=_squash(f"{name}: {value_raw} {unit}"), payload=d,
        )

    @staticmethod
    def _report_items(response: Any, analysis: Any, shown_engine_rows: list[Any]) -> list[Reason]:
        """Report-level review items: the warnings the product shows that no displayed row owns, and the unread
        numeric line count. The "Patient sex/age not provided" note is not a review item (plan 6.3.2 step 11)."""
        shown_warnings = [str(w) for w in (getattr(response, "warnings", None) or [])]
        engine_warnings = set(getattr(analysis, "warnings", None) or [])
        unread = int(getattr(analysis, "unparsed_numeric_lines", 0) or 0)
        unread_msg = f"{unread} numeric line(s) could not be read" if unread else None
        prefixes = tuple(f"{r.display_name}:" for r in shown_engine_rows)
        items: list[Reason] = []
        for w in shown_warnings:
            if w not in engine_warnings and _DEMOGRAPHICS_NOTE_RE.match(w):
                continue
            if prefixes and w.startswith(prefixes):
                continue
            if unread_msg is not None and w == unread_msg:
                continue  # stated once, as NR0-UNREAD below
            items.append(Reason(NR0_REPORT_WARNING, w))
        if unread_msg is not None and unread_msg in shown_warnings:
            items.append(Reason(NR0_UNREAD, f"{unread} unread numeric line(s)"))
        return items


# ---------------------------------------------------------------- v0 scorer (6.4)

_SPECIMEN_SECTION = {"urine": "urine examination", "csf": "csf analysis", "fluid": "body fluid analysis"}
_RANGE_TEXT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*-\s*(\d+(?:\.\d+)?)\s*$")


class EngineFloorScorerV0:
    """Scores confirmed rows with the engine floor: ``analyze_lab_rows`` then ``apply_risk_floor(analysis, None,
    None)``, the critical ceiling, and the shared label mapping (plan 6.4).

    Accepted rows reuse their ``ParsedRow`` payload (a copy). Edited and added rows, and accepted legacy urine rows
    (whose payload is a dict, not a ``ParsedRow``), get a fresh ``ParsedRow`` from the confirmed values with
    ``printed_flag=None`` and a specimen section heading. Rows are duck-typed (any object with the ConfirmedRow
    fields), so judge-side code never needs to import the adapters."""

    name = SCORER_NAME
    version = SCORER_VERSION

    def __init__(self) -> None:
        from app.services.lab_engine import ParsedRow, analyze_lab_rows, apply_risk_floor, canon_tokens

        self._ParsedRow = ParsedRow
        self._analyze = analyze_lab_rows
        self._floor = apply_risk_floor
        self._canon_tokens = canon_tokens

    def parsed_row(self, row: Any) -> Any:
        payload = getattr(row, "payload", None)
        if getattr(row, "origin", "accepted") == "accepted" and isinstance(payload, self._ParsedRow):
            return copy.deepcopy(payload)
        value = getattr(row, "value_num", None)
        text = getattr(row, "text_value", None)
        result_range = None
        if value is None and text is not None:
            m = _RANGE_TEXT_RE.match(str(text))
            if m:  # a microscopy range result ("2-3 /hpf"): read as the product does, on the upper end
                lo, hi = sorted((float(m.group(1)), float(m.group(2))))
                value, result_range, text = hi, (lo, hi), None
        name = str(getattr(row, "name_raw", "") or "")
        unit = str(getattr(row, "unit_raw", "") or "").strip() or None
        return self._ParsedRow(
            name=name, value=value, qualifier=getattr(row, "qualifier", None) or None,
            text_value=None if value is not None else text, unit=unit, ref_low=getattr(row, "ref_low", None),
            ref_high=getattr(row, "ref_high", None), printed_flag=None, source="text",
            line=_squash(f"{name} {getattr(row, 'value_raw', '') or ''} {unit or ''}"),
            section=_SPECIMEN_SECTION.get(str(getattr(row, "specimen", "") or ""), ""), result_range=result_range,
        )

    def score(self, rows: Sequence[Any], sex: str | None = None, age: int | None = None) -> ScoreResult:
        parsed = [self.parsed_row(r) for r in rows]
        analysis = self._analyze(parsed, sex, age)
        label, _score, _uncertain = self._floor(analysis, None, None)
        if str(label).lower() == "critical" and not any(getattr(r, "critical", None) for r in analysis.rows):
            label = "high"  # ceiling: "critical" needs a critical value (as in run_report_pipeline)
        return ScoreResult(headline_from_label(label), self._row_flags(parsed, analysis, sex, age), str(label))

    def _ident(self, r: Any) -> tuple:
        value = round(r.value, 6) if r.value is not None else None
        return (r.analyte_key or tuple(sorted(self._canon_tokens(r.display_name))), value, r.qualifier,
                (r.text_value or "").lower())

    def _row_flags(self, parsed: list[Any], analysis: Any, sex: str | None, age: int | None) -> list[str]:
        """``flags[i]`` is the engine flag of input row i. A row the engine merged into an identical duplicate
        takes the survivor's flag; a row it could not score at all (no value, no text) is UNKNOWN."""
        by_parsed = {id(r.parsed): r for r in analysis.rows}
        flags: list[str] = []
        for p in parsed:
            r = by_parsed.get(id(p))
            if r is None:
                alone = self._analyze([p], sex, age).rows
                if alone:
                    ident = self._ident(alone[0])
                    r = next((s for s in analysis.rows if self._ident(s) == ident), None)
            flags.append(r.flag if r is not None else "UNKNOWN")
        return flags


__all__ = [
    "ADAPTER_NAME", "ADAPTER_VERSION", "AdapterFidelityError", "CRITICAL_TEXT", "EngineFloorScorerV0",
    "HERMETIC_POP", "HERMETIC_SET", "HarnessConfigError", "LEGACY_KEY_MAP", "NR0_CODES", "NR0_CRITICAL",
    "NR0_FLAGCONFLICT", "NR0_NOANALYTE", "NR0_REPORT_WARNING", "NR0_UNKNOWN", "NR0_UNREAD", "NR0_VALUETYPE",
    "NR0_WARNING", "PipelineV0Adapter", "REQUIRED_SETTINGS", "SCORER_NAME", "SCORER_VERSION", "attributed_warnings",
    "legacy_analyte_key", "legacy_flag", "prepare_hermetic_env", "printed_range", "restore_env", "specimen_of",
    "v0_review_reasons",
]
