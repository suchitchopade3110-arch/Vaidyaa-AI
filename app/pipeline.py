"""
pipeline.py - VaidyaAI Master Orchestrator
==========================================
Connects the PRD layers into unified image, report, claim, and smart-router
entry points.
"""

import json
import logging
import re
import os
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from pydantic import BaseModel, Field
except Exception:  # pragma: no cover - only used in minimal self-test envs
    class BaseModel:
        def __init__(self, **kwargs):
            annotations = getattr(self.__class__, "__annotations__", {})
            for key, value in self.__class__.__dict__.items():
                if key.startswith("_") or callable(value) or isinstance(value, property):
                    continue
                if key not in kwargs and key in annotations:
                    setattr(self, key, value)
            for key, value in kwargs.items():
                setattr(self, key, value)

        def model_dump(self):
            return dict(self.__dict__)

    def Field(default_factory=None, default=None):
        if default_factory is not None:
            return default_factory()
        return default

from app.image_pipeline.classifier_v2 import classify_image
from app.ml.ml_prediction_engine import (
    ensemble_predict,
    generate_gradcam_overlay,
    predict_tabular,
    to_json_safe,
)
from clinical_reference import HIGH_MEANINGS, LOW_MEANINGS, analyze_blood_report, clean_test_name
from app.core.config import settings
from app.services.lab_engine import (
    ANALYTE_TO_LEGACY_KEY,
    LabAnalysis,
    ParsedRow,
    ScoredRow,
    analyze_lab_rows,
    apply_risk_floor,
    decorate_explanation,
    normalize_age,
    normalize_sex,
    parse_report_text,
)
from app.services.lab_engine.units import is_unit_token

logger = logging.getLogger("vaidya.pipeline")

try:
    from app.services.modality_classifier import CHEST_MODALITIES, classify_by_modality

    MODALITY_ROUTER_AVAILABLE = True
except Exception as exc:  # pragma: no cover - optional service fallback
    CHEST_MODALITIES = {"xray", "x-ray", "x_ray", "chest", "cxr"}
    MODALITY_ROUTER_AVAILABLE = False
    logger.warning("Modality router unavailable: %s", exc)


# ============================================================================
# CONFIGURATION
# ============================================================================

DISCLAIMER = (
    "AI-assisted analysis only. NOT A MEDICAL DIAGNOSIS. "
    "Consult a qualified healthcare professional for clinical decisions."
)

ARTIFACTS_DIR = Path(os.getenv("VAIDYA_ARTIFACTS_DIR", "/tmp/vaidya/artifacts"))
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# UNIFIED OUTPUT CONTRACT
# ============================================================================

class PipelineResponse(BaseModel):
    """Unified response from any pipeline. Backend serializes to API JSON."""

    job_id: str
    patient_id: Optional[str] = None
    pipeline_type: str

    status: str
    error: Optional[str] = None

    tabular_prediction: Optional[Dict[str, Any]] = None
    ensemble_details: Optional[Dict[str, Any]] = None
    image_classification: Optional[Dict[str, Any]] = None
    yolo_detections: List[Dict[str, Any]] = Field(default_factory=list)
    yolo_annotated_path: Optional[str] = None
    yolo_model_used: Optional[str] = None
    yolo_roi_bbox: List[int] = Field(default_factory=list)
    label_metadata: Optional[Dict[str, Any]] = None
    radiology_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    who_structured_report: Optional[Dict[str, Any]] = None
    ensemble: Optional[Dict[str, Any]] = None

    segmentation: Optional[Dict[str, Any]] = None

    extracted_entities: Optional[Dict[str, Any]] = None
    lab_values: List[Dict[str, Any]] = Field(default_factory=list)
    anomalies: List[Dict[str, Any]] = Field(default_factory=list)
    panel_type: Optional[str] = None

    verdict: Optional[str] = None
    risk_score: Optional[float] = None
    risk_label: Optional[str] = None
    differential_diagnosis: Optional[Dict[str, Any]] = None
    explanation_mode: str = "brief"
    explanation_brief: Optional[str] = None
    explanation_full: Optional[str] = None
    rag_explanation: Optional[str] = None
    confidence_score: Optional[float] = None
    confidence_label: Optional[str] = None
    explanation: Optional[str] = None
    plain_language_summary: Optional[str] = None
    sources: List[Dict[str, Any]] = Field(default_factory=list)
    uncertainty_flag: bool = False
    warnings: List[str] = Field(default_factory=list)
    hallucination_flagged: bool = False

    shap_top_factors: List[Dict[str, Any]] = Field(default_factory=list)
    gradcam_path: Optional[str] = None
    segmentation_overlay_path: Optional[str] = None
    patient_history: List[Dict[str, Any]] = Field(default_factory=list)
    history_comparison: Optional[Dict[str, Any]] = None
    critical_alerts: List[Dict[str, Any]] = Field(default_factory=list)   # the engine's alerts (P2)
    confirmed: bool = False                                                # scored from clinician-confirmed rows
    ocr_engine: Optional[str] = None
    ocr_confidence: Optional[float] = None
    ner_engine: Optional[str] = None
    qr_token: Optional[str] = None
    qr_available: bool = False

    disclaimer: str = DISCLAIMER

    processing_time_ms: float = 0.0
    completed_at: str = ""


# ============================================================================
# CONTEXT + FALLBACK DATA SHAPES
# ============================================================================

@dataclass
class PipelineContext:
    """Shared context across all pipelines for tracing."""

    job_id: str
    patient_id: Optional[str]
    pipeline_type: str
    start_time: float

    @property
    def elapsed_ms(self) -> float:
        return round((time.time() - self.start_time) * 1000, 2)


@dataclass
class _ValidationResult:
    valid: bool = True
    reason: str = ""


@dataclass
class _NerResult:
    conditions: List[str]
    medications: List[str]
    dates: List[str]
    lab_values: List[Any]


def _make_context(
    pipeline_type: str,
    patient_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> PipelineContext:
    return PipelineContext(
        job_id=job_id or str(uuid.uuid4()),
        patient_id=patient_id,
        pipeline_type=pipeline_type,
        start_time=time.time(),
    )


def _build_error_response(
    ctx: PipelineContext,
    error: str,
    status: str = "failed",
) -> PipelineResponse:
    logger.error("[%s] %s pipeline %s: %s", ctx.job_id, ctx.pipeline_type, status, error)
    return PipelineResponse(
        job_id=ctx.job_id,
        patient_id=ctx.patient_id,
        pipeline_type=ctx.pipeline_type,
        status=status,
        error=error,
        processing_time_ms=ctx.elapsed_ms,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )


def _confidence_label(score: float) -> str:
    if score > 75:
        return "HIGH"
    if score > 50:
        return "MEDIUM"
    return "LOW"


def _normalize_lab_key(name: Any) -> str:
    return str(name or "").lower().strip()


def _fallback_risk_from_anomalies(anomalies: List[Dict[str, Any]]) -> float:
    if not anomalies:
        return 0.0
    critical = sum(1 for item in anomalies if str(item.get("severity", "")).upper() == "CRITICAL")
    high = sum(1 for item in anomalies if str(item.get("severity", "")).upper() == "HIGH")
    score = min(95.0, 15.0 + (critical * 25.0) + (high * 15.0))
    return round(score, 1)


# ============================================================================
# LAZY LAYER WRAPPERS
# ============================================================================

def _validate_file_safe(file_path: str) -> _ValidationResult:
    try:
        from app.services.preprocessor import validate_file

        return validate_file(file_path)
    except Exception as exc:
        if os.path.exists(file_path):
            logger.warning("Validation fallback for %s: %s", file_path, exc)
            return _ValidationResult(valid=True, reason="")
        return _ValidationResult(valid=False, reason=f"File not found: {file_path}")


def _run_ocr_safe(file_path: str):
    try:
        from app.services.preprocessor import run_ocr_with_layout

        out, layout = run_ocr_with_layout(file_path)
        return out.model_copy(update={"layout": layout})   # layout feeds provenance (E7); text is run_ocr's
    except Exception as exc:
        logger.warning("OCR unavailable for %s: %s", file_path, exc)
        return type("OcrFallback", (), {"raw_text": "", "confidence": 0.0, "pages": []})()


def _run_ner_safe(text: str) -> _NerResult:
    try:
        from app.services.preprocessor import run_ner

        return run_ner(text)
    except Exception as exc:
        logger.warning("NER fallback: %s", exc)
        return _NerResult(conditions=[], medications=[], dates=[], lab_values=[])


def _preprocess_image_safe(image_path: str):
    from app.services.preprocessor import preprocess_image_file

    return preprocess_image_file(image_path)


def _preprocess_dicom_safe(image_path: str):
    from app.services.preprocessor import preprocess_dicom_file

    return preprocess_dicom_file(image_path)


def _extract_lab_values_safe(raw_text: str, ner_result: Any, report_type: str) -> List[Any]:
    values = list(getattr(ner_result, "lab_values", []) or [])
    if report_type != "lab":
        return values
    try:
        from app.services.preprocessor import extract_all_lab_values, deduplicate_lab_values

        layered = extract_all_lab_values(raw_text)
        if values:
            return deduplicate_lab_values(values + layered)
        return layered
    except Exception as exc:
        logger.warning("Structured lab extraction fallback: %s", exc)
        return values


_COLUMN_PARSER_MIN_FRACTION = 0.6


def _column_parser_output_usable(parsed: Dict[str, Any]) -> bool:
    """Accept the x-band column parser's rows only when they look like a real
    table: >= 60% carry a numeric result AND >= 60% carry a printed range or a
    recognisable unit. On other layouts ("Hemoglobin: 9.2 g/dL (Ref: 13-17.5)
    LOW") it emits whole lines as test names with no numeric result and, when
    accepted, those rows replaced real extraction and every value scored
    NORMAL."""
    from app.services.lab_report_column_parser import column_rows_to_parsed

    rows = column_rows_to_parsed(parsed.get("lab_values") or [])
    if not rows:
        return False
    numeric = sum(1 for r in rows if r.value is not None)
    structured = sum(
        1
        for r in rows
        if r.ref_low is not None or r.ref_high is not None or r.sex_ranges or (r.unit and is_unit_token(r.unit))
    )
    return (
        numeric >= _COLUMN_PARSER_MIN_FRACTION * len(rows)
        and structured >= _COLUMN_PARSER_MIN_FRACTION * len(rows)
    )


def _parse_column_lab_report_safe(file_path: str, report_type: str) -> Dict[str, Any]:
    if report_type != "lab" or Path(file_path).suffix.lower() != ".pdf":
        return {}
    try:
        from app.services.lab_report_column_parser import parse_lab_report_for_pipeline

        parsed = parse_lab_report_for_pipeline(file_path)
        rows = parsed.get("lab_values") or []
        if rows and not _column_parser_output_usable(parsed):
            logger.info(
                "Column-aware lab parser output rejected (%s rows; too few numeric results or ranges/units); "
                "using text extraction",
                len(rows),
            )
            return {}
        if rows:
            logger.info(
                "Column-aware lab parser extracted %s rows; panel=%s",
                len(rows),
                parsed.get("panel_type"),
            )
            return parsed
    except Exception as exc:
        logger.warning("Column-aware lab parser skipped: %s", exc)
    return {}


def _run_segmentation_safe(image_array: Any, ctx: PipelineContext):
    try:
        from app.services.segmentation.pipeline_runner import pipeline as run_segmentation

        return run_segmentation(image_array, model_predictor=None, modality="X-Ray")
    except Exception as exc:
        logger.warning("[%s] Segmentation unavailable, using full image: %s", ctx.job_id, exc)
        return None


def _rag_service():
    try:
        from app.services.rag_pipeline import rag_pipeline

        return rag_pipeline
    except Exception as exc:
        logger.warning("RAG unavailable: %s", exc)
        return None


def _explain_report_safe(
    raw_text: str,
    entities: Dict[str, Any],
    risk_score: float,
    risk_factors: List[Dict[str, Any]],
    anomalies: List[Dict[str, Any]],
    report_type: str,
    panel_type: str = "general",
) -> Dict[str, Any]:
    rag = _rag_service()
    if rag is None:
        return {
            "verdict": "Uncertain",
            "confidence_score": 50.0,
            "explanation": "ML analysis completed. Evidence retrieval is currently unavailable.",
            "plain_language_summary": None,
            "sources": [],
            "uncertainty_flag": True,
            "hallucination_flagged": False,
        }
    try:
        if anomalies:
            query_terms = " ".join(
                str(item.get("field") or item.get("test") or item.get("name") or "")
                for item in anomalies[:8]
            )
            query = f"{panel_type} {report_type} lab findings {query_terms}".strip()
        else:
            query = f"{panel_type} {report_type} lab report {raw_text[:300]}".strip()
        sources = rag.retrieve_evidence(
            query,
            panel_type=panel_type,
            report_type=report_type,
            exclude_imaging=(report_type == "lab"),
        )
        explanation = rag.explain_report(
            entities=entities,
            risk_score=risk_score,
            risk_factors=risk_factors,
            anomalies=anomalies,
            sources=sources,
            report_type=report_type,
        )
        return {
            "verdict": "Uncertain",
            "confidence_score": risk_score if risk_score else 50.0,
            "explanation": explanation,
            "plain_language_summary": explanation,
            "sources": sources,
            "uncertainty_flag": not bool(sources),
            "hallucination_flagged": False,
        }
    except Exception as exc:
        logger.warning("Report RAG fallback: %s", exc)
        return {
            "verdict": "Uncertain",
            "confidence_score": risk_score if risk_score else 50.0,
            "explanation": "Report analysis available but evidence retrieval is pending.",
            "plain_language_summary": None,
            "sources": [],
            "uncertainty_flag": True,
            "hallucination_flagged": False,
        }


def _to_0_100(value: Any, default: float = 0.0) -> float:
    """Confidence is reported on 0-100 everywhere (README contract); LLM
    and fallback payloads use 0-1, so scale those up."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    number = number * 100.0 if 0.0 <= number <= 1.0 else number
    return round(max(0.0, min(100.0, number)), 1)


def relevant_sources(sources: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Keep only passages whose retrieval score clears MIN_SOURCE_RELEVANCE.

    Retrieval always returns top-k hits, including keyword-fallback matches
    on a single shared word ("diabetes"); counting those let a nonsense
    claim reach the >=2-sources bar. Unscored passages don't count.
    """
    floor = float(getattr(settings, "MIN_SOURCE_RELEVANCE", 0.40))
    kept = []
    for source in sources or []:
        try:
            score = float(source.get("score"))
        except (TypeError, ValueError, AttributeError):
            continue
        if score >= floor:
            kept.append(source)
    return kept


def apply_evidence_threshold(verdict: Dict[str, Any], sources: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Hallucination-mitigation layer 3 (README "Threshold flagging").

    Returns the claim payload with the verdict downgraded to Uncertain /
    "Insufficient evidence" whenever ANY of these hold:
      - fewer than MIN_SOURCES_REQUIRED supporting sources,
      - the reasoning step failed (the RAG fallback records an `error`),
      - the self-verification step flagged a hallucination,
      - confidence is below MIN_CONFIDENCE_THRESHOLD.
    The confidence comes from the reasoning step itself; it is never a
    hard-coded constant.
    """
    sources = relevant_sources(sources)
    min_sources = int(getattr(settings, "MIN_SOURCES_REQUIRED", 2))
    min_conf = _to_0_100(getattr(settings, "MIN_CONFIDENCE_THRESHOLD", 0.30))

    raw_verdict = str(verdict.get("verdict") or "uncertain").strip().lower()
    confidence = _to_0_100(verdict.get("confidence_score"), default=0.0)
    hallucinated = bool(verdict.get("hallucination_detected", False))
    details = verdict.get("hallucination_details") or {}
    reasoning_failed = isinstance(details, dict) and bool(details.get("error"))

    reasons = []
    if len(sources) < min_sources:
        reasons.append(f"only {len(sources)} supporting source(s); {min_sources} required")
    if reasoning_failed:
        reasons.append("the reasoning step could not be completed")
    if hallucinated:
        reasons.append("self-verification flagged unsupported statements")
    if confidence < min_conf:
        reasons.append(f"confidence {confidence:.0f} is below the {min_conf:.0f} threshold")

    if reasons:
        return {
            "verdict": "Uncertain",
            "confidence_score": min(confidence, min_conf),
            "explanation": "Insufficient evidence: " + "; ".join(reasons) + ".",
            "sources": sources,
            "uncertainty_flag": True,
            "hallucination_flagged": hallucinated,
        }

    label = {"verified": "Verified", "supported": "Verified", "refuted": "Refuted",
             "contradicted": "Refuted"}.get(raw_verdict, "Uncertain")
    return {
        "verdict": label,
        "confidence_score": confidence,
        "explanation": verdict.get("explanation"),
        "sources": sources,
        "uncertainty_flag": label == "Uncertain",
        "hallucination_flagged": False,
    }


def _verify_claim_safe(claim_text: str, entities: Dict[str, Any]) -> Dict[str, Any]:
    rag = _rag_service()
    if rag is None:
        return {
            "verdict": "Uncertain",
            "confidence_score": 50.0,
            "explanation": "Insufficient evidence for verification.",
            "sources": [],
            "uncertainty_flag": True,
            "hallucination_flagged": False,
        }
    try:
        # Only on-topic evidence reaches the LLM and the citation list.
        sources = relevant_sources(rag.retrieve_evidence(claim_text))
        verdict = rag.verify_claim(claim_text, entities, sources)
        return apply_evidence_threshold(verdict, sources)
    except Exception as exc:
        logger.warning("Claim RAG fallback: %s", exc)
        return {
            "verdict": "Uncertain",
            "confidence_score": 50.0,
            "explanation": "Insufficient evidence for verification.",
            "sources": [],
            "uncertainty_flag": True,
            "hallucination_flagged": False,
        }


def _ner_entities_dict(ner_result: Any) -> Dict[str, Any]:
    return {
        "conditions": list(getattr(ner_result, "conditions", []) or [])[:10],
        "medications": list(getattr(ner_result, "medications", []) or [])[:10],
        "dates": list(getattr(ner_result, "dates", []) or [])[:5],
    }


def _lab_value_to_row(lab_value: Any, gender: Optional[str] = None, age: Optional[int] = None) -> Dict[str, Any]:
    name = getattr(lab_value, "name", None)
    value = getattr(lab_value, "value", None)
    unit = getattr(lab_value, "unit", "")
    ref_low = getattr(lab_value, "ref_low", None)
    ref_high = getattr(lab_value, "ref_high", None)
    reference = getattr(lab_value, "reference", None)
    flag = getattr(lab_value, "flag", "NORMAL")
    if isinstance(lab_value, dict):
        name = lab_value.get("name") or lab_value.get("test")
        value = lab_value.get("value") if lab_value.get("value") is not None else lab_value.get("result")
        unit = lab_value.get("unit", "")
        ref_low = lab_value.get("ref_low")
        ref_high = lab_value.get("ref_high")
        reference = lab_value.get("reference") or lab_value.get("reference_range")
        flag = lab_value.get("flag", "NORMAL")

    if name:
        name = clean_test_name(str(name))

    if ref_low is not None and ref_high is not None:
        reference = f"{ref_low} - {ref_high}"
    elif not reference and name:
        try:
            from app.services.preprocessor import resolve_reference

            low, high, _ = resolve_reference(str(name), gender=gender, age=age, pdf_ref_low=ref_low, pdf_ref_high=ref_high)
            if low is not None and high is not None:
                reference = f"{low} - {high}"
        except Exception:
            reference = None

    value_type = lab_value.get("value_type") if isinstance(lab_value, dict) else getattr(lab_value, "value_type", None)
    note = lab_value.get("result_text") if isinstance(lab_value, dict) else getattr(lab_value, "result_text", "")
    return {
        "field": name,
        "test": name,
        "result": value,
        "unit": unit,
        "reference": reference or "--",
        "flag": flag or "NORMAL",
        "ref_low": ref_low,
        "ref_high": ref_high,
        "value_type": value_type or "numeric",
        "note": note or "",
    }


def _lab_values_to_dict(lab_values: List[Any], gender: Optional[str], age: Optional[int]) -> Dict[str, Any]:
    """Feature dict for the non-lab report types (NER-extracted values).

    Lab reports use `_engine_lab_dict` (canonical keys and units). Sex/age are
    only included when known."""
    lab_dict: Dict[str, Any] = {}
    for lab_value in lab_values:
        row = _lab_value_to_row(lab_value, gender=gender, age=age)
        if row["test"] is None or row["result"] is None:
            continue
        if row.get("value_type") == "unit_mismatch":
            continue
        key = _normalize_lab_key(row["test"]).replace(" ", "_")
        lab_dict[key] = row["result"]
    sex = normalize_sex(gender)
    if sex:
        lab_dict["gender"] = 1 if sex == "M" else 0
    if age is not None:
        lab_dict["age"] = age
    return lab_dict


# ── lab engine -> response shapes ────────────────────────────────────────────

_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "MILD": 3, "NONE": 4}


def _legacy_key(analyte_key: Optional[str]) -> Optional[str]:
    keys = ANALYTE_TO_LEGACY_KEY.get(analyte_key or "")
    return keys[0] if keys else None


def _row_meaning(row: ScoredRow) -> str:
    key = _legacy_key(row.analyte_key)
    if row.direction == "HIGH":
        return HIGH_MEANINGS.get(key, "Value above normal range") if key else "Value above normal range"
    if row.direction == "LOW":
        return LOW_MEANINGS.get(key, "Value below normal range") if key else "Value below normal range"
    if row.direction == "POSITIVE":
        return "Reported as positive / reactive"
    return ""


def _scored_to_row(row: ScoredRow) -> Dict[str, Any]:
    note = row.note or ""
    if row.value is None and row.text_value:
        note = "; ".join(part for part in (row.text_value, note) if part)
    if row.discrepancy:
        note = "; ".join(part for part in (note, row.discrepancy) if part)
    return {
        "field": row.display_name,
        "test": row.display_name,
        "result": row.value if row.value is not None else row.text_value,
        "unit": row.unit,
        "reference": row.reference_text,
        "flag": row.flag,
        "ref_low": row.ref_low,
        "ref_high": row.ref_high,
        "value_type": row.value_type,
        "note": note,
        "range_source": row.range_source,
        "analyte_key": row.analyte_key,
        "qualifier": row.qualifier,
        "severity": row.severity,
        "clinical_meaning": _row_meaning(row) if row.direction else "",
    }


def _engine_anomaly_dicts(analysis: LabAnalysis) -> List[Dict[str, Any]]:
    """Engine anomalies in the response shape, most severe first. Computed
    from the engine alone, outside any model try-block."""
    out: List[Dict[str, Any]] = []
    for row in sorted(analysis.anomalies, key=lambda r: _SEVERITY_ORDER.get(r.severity, 4)):
        severity = row.severity if row.severity != "NONE" else "MODERATE"
        arrow = {"HIGH": "⬆️", "LOW": "⬇️"}.get(row.direction, "⚠️")
        item = {
            "field": row.display_name,
            "test": row.display_name,
            "value": row.value if row.value is not None else row.text_value,
            "unit": row.unit,
            "reference": row.reference_text,
            "direction": row.direction or "ABNORMAL",
            "severity": severity,
            "flag": f"{arrow} {severity}",
            "clinical_meaning": _row_meaning(row),
            "status": "ABNORMAL",
            "analyte_key": row.analyte_key,
            "range_source": row.range_source,
        }
        if row.critical:
            item["critical_alert"] = row.critical.get("message")
        out.append(item)
    return out


def _engine_lab_dict(analysis: LabAnalysis, sex: Optional[str], age: Optional[int]) -> Dict[str, Any]:
    """Feature dict keyed by the names XGBoost / the clinical patterns use,
    holding canonical-unit values. Sex and age are included only when known."""
    lab_dict: Dict[str, Any] = {}
    for row in analysis.rows:
        if row.value is None or row.value_type not in ("numeric", "qualified") or not row.analyte_key:
            continue
        for key in ANALYTE_TO_LEGACY_KEY.get(row.analyte_key, ()):
            lab_dict.setdefault(key, row.value)
    if sex:
        lab_dict["gender"] = 1 if sex == "M" else 0
    if age is not None:
        lab_dict["age"] = age
    return lab_dict


def _engine_legacy_anomalies(analysis: LabAnalysis) -> Dict[str, Dict[str, Any]]:
    """Engine anomalies keyed like the clinical-pattern table expects
    (direction + canonical value), so conditions are derived from the engine's
    verdicts rather than re-flagging against the registry."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in analysis.anomalies:
        if row.value is None or not row.analyte_key or row.direction not in ("HIGH", "LOW"):
            continue
        for key in ANALYTE_TO_LEGACY_KEY.get(row.analyte_key, ()):
            out.setdefault(
                key,
                {
                    "status": "ABNORMAL",
                    "direction": row.direction,
                    "severity": row.severity,
                    "value": row.value,
                    "ref_low": row.ref_low,
                    "ref_high": row.ref_high,
                    "unit": row.unit,
                    "reference": row.reference_text,
                    "clinical_meaning": _row_meaning(row),
                },
            )
    return out


def _ensemble_rows(lab_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows handed to the ensemble's own anomaly model: only those with a full
    range resolved by the engine. The ensemble would otherwise fall back to a
    male/40 registry guess for rows the engine left UNKNOWN."""
    return [
        row
        for row in lab_rows
        if row.get("value_type") in ("numeric", "qualified")
        and row.get("ref_low") is not None
        and row.get("ref_high") is not None
    ]


def _lab_rows_from_report(raw_text: str, parsed_lab_report: Dict[str, Any]) -> tuple[List[ParsedRow], int, List[str]]:
    """(rows, unread numeric lines, warnings) from the column parser (when it
    produced a usable table) or from the report text/structured CSV."""
    if parsed_lab_report.get("lab_values"):
        from app.services.lab_report_column_parser import column_rows_to_parsed

        return column_rows_to_parsed(parsed_lab_report["lab_values"]), 0, []
    return parse_report_text(raw_text)


def _engine_anomalies_to_rows(anomalies: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for key, item in (anomalies or {}).items():
        if not isinstance(item, dict) or item.get("status") != "ABNORMAL":
            continue
        rows.append(
            {
                "field": item.get("field") or key,
                "test": item.get("field") or key,
                "value": item.get("value"),
                "unit": item.get("unit", ""),
                "reference": item.get("reference", "--"),
                "direction": item.get("direction", "ABNORMAL"),
                "severity": item.get("severity", "HIGH"),
                "flag": item.get("flag", "⚠️ ABNORMAL"),
                "clinical_meaning": item.get("clinical_meaning", ""),
            }
        )
    return rows


def _deduplicate_anomalies(anomalies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for item in anomalies or []:
        if not isinstance(item, dict):
            continue
        key = _normalize_lab_key(item.get("field") or item.get("test") or item.get("label"))
        if not key:
            continue
        existing = seen.get(key)
        if existing is None or len(str(item.get("clinical_meaning") or item.get("explanation") or "")) > len(
            str(existing.get("clinical_meaning") or existing.get("explanation") or "")
        ):
            seen[key] = item
    return list(seen.values())


_RISK_ORDER = {"unknown": 0, "normal": 1, "low": 1, "moderate": 2, "medium": 2, "high": 3, "critical": 4}


# Urine dipstick / physical analytes the engine also recognises. A report
# whose only engine rows are these (or qualitative +/- results) is a
# urinalysis report and keeps the richer urinalysis path.
_URINE_ONLY_ANALYTES = {"specific_gravity", "urine_ph"}


_URINE_DIPSTICK_NAMES = {
    "protein", "albumin", "glucose", "sugar", "ketone", "ketones", "ketonebodies", "acetone", "nitrite", "nitrites",
    "leukocyteesterase", "leucocyteesterase", "leukocytes", "blood", "occultblood", "haemoglobin", "hemoglobin",
    "bilirubin", "urobilinogen", "bilesalts", "bilepigments", "colour", "color", "appearance", "clarity",
    "transparency", "reaction", "deposit", "sediment", "crystals", "casts", "bacteria", "yeast", "mucus",
}


def _is_blood_row(row: Any) -> bool:
    """True for any row that isn't a urine dipstick/physical/microscopy item.

    Qualitative results count: a reactive HIV / HBsAg / malaria antigen next
    to a urine routine used to send the whole report down the urinalysis
    branch, where the serology vanished and the headline was "low".
    """
    key = getattr(row, "analyte_key", None) or ""
    if key in _URINE_ONLY_ANALYTES or key.startswith("urine_"):
        return False
    if getattr(row, "value_type", "") == "qualitative" and not key:
        name = _lab_name_key(str(getattr(row, "display_name", "") or ""))
        name = re.sub(r"^urine|urine$", "", name)
        return name not in _URINE_DIPSTICK_NAMES
    return True


def _lab_name_key(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _not_reported(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in ("", "--", "-", "n/a", "N/A"))


def _urine_entities_to_rows(entities: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    dipstick = entities.get("dipstick") or {}
    microscopy = entities.get("microscopy") or {}
    quantitative = entities.get("quantitative") or {}

    for section_name, section in [
        ("dipstick", dipstick),
        ("microscopy", microscopy),
        ("quantitative", quantitative),
    ]:
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if key == "casts" and isinstance(value, dict):
                for cast_name, cast_value in value.items():
                    if _not_reported(cast_value):
                        continue
                    rows.append(
                        {
                            "field": cast_name.replace("_", " ").title(),
                            "test": cast_name,
                            "result": cast_value,
                            "value": cast_value,
                            "unit": "",
                            "reference": "absent",
                            "flag": "ABNORMAL" if str(cast_value).lower() == "present" else "NORMAL",
                            "section": section_name,
                        }
                    )
                continue
            if not isinstance(value, dict) or _not_reported(value.get("value")):
                # The urinalysis extractor emits a slot for every parameter;
                # an empty one used to be shown as a NORMAL result for a test
                # that was never on the report.
                continue
            rows.append(
                {
                    "field": key.replace("_", " ").title(),
                    "test": key,
                    "result": value.get("value", ""),
                    "value": value.get("value", ""),
                    "unit": value.get("unit", ""),
                    "reference": value.get("reference", "--"),
                    "flag": str(value.get("flag", "normal")).upper(),
                    "section": section_name,
                }
            )
    return rows


def _urine_pattern_anomalies(patterns: List[str], evidence: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    evidence_by_pattern = {item.get("pattern"): item for item in evidence or []}
    urgency_to_severity = {
        "critical": "CRITICAL",
        "high": "HIGH",
        "medium": "MODERATE",
        "low": "LOW",
        "none": "NORMAL",
    }
    rows = []
    for pattern in patterns or []:
        matched = next(
            (item for item in evidence_by_pattern.values() if pattern.lower().replace("_", " ") in str(item.get("pattern", "")).lower()),
            {},
        )
        severity = urgency_to_severity.get(str(matched.get("urgency", "medium")).lower(), "MODERATE")
        rows.append(
            {
                "field": pattern.replace("_", " "),
                "test": pattern,
                "value": "detected",
                "unit": "",
                "reference": "not detected",
                "direction": "ABNORMAL",
                "severity": severity,
                "flag": f"⚠️ {severity}",
                "clinical_meaning": matched.get("text", ""),
                "status": "ABNORMAL",
            }
        )
    return rows


def _urine_risk_from_patterns(patterns: List[str]) -> float:
    score_map = {
        "DKA": 95.0,
        "Pre_eclampsia": 95.0,
        "Nephritic": 75.0,
        "Nephrotic": 75.0,
        "UTI": 65.0,
        "Renal_TB_suspected": 75.0,
        "Obstructive_jaundice": 70.0,
        "Hemolytic": 55.0,
    }
    return max([score_map.get(pattern, 35.0) for pattern in patterns] or [20.0])


# ============================================================================
# PIPELINE 1 - IMAGE ANALYSIS
# ============================================================================

def _whole_radiograph(image_path: str, fallback_array: Any):
    """The full, un-enhanced image as RGB PIL (DICOM: its decoded pixels)."""
    import numpy as np
    from PIL import Image

    if Path(image_path).suffix.lower() != ".dcm":
        try:
            return Image.open(image_path).convert("RGB")
        except Exception as exc:  # unreadable by PIL — fall back to the decoded array
            logger.warning("Could not reopen %s for classification: %s", image_path, exc)
    return Image.fromarray(np.asarray(fallback_array)).convert("RGB")


def radiograph_plausibility(image: Any) -> tuple[bool, str]:
    """Cheap in-distribution check before the chest X-ray model runs.

    The model returns findings for ANY input — random noise came back as
    "URGENT FINDING: Pneumothorax". Measured on public chest radiographs:
    colour deviation <= 1.5, neighbour roughness 0.016-0.019, contrast std
    0.11-0.26; noise, colour photos, flat frames and synthetic shapes fall
    outside. Bounds are deliberately generous so real films aren't rejected.
    """
    import numpy as np

    rgb = np.asarray(image.convert("RGB").resize((224, 224)), dtype=np.float32)
    colour = float(np.abs(rgb - rgb.mean(axis=2, keepdims=True)).mean())
    grey = rgb.mean(axis=2) / 255.0
    contrast = float(grey.std())
    roughness = float((np.abs(np.diff(grey, axis=0)).mean() + np.abs(np.diff(grey, axis=1)).mean()) / 2)
    if colour > 8.0:
        return False, "it is a colour image"
    if contrast < 0.04:
        return False, "it has almost no contrast"
    if roughness > 0.06:
        return False, "it looks like noise"
    if roughness < 0.004:
        return False, "it has no anatomical texture"
    grey_u8 = np.asarray(image.convert("L").resize((224, 224)), dtype=np.uint8)
    hist = np.bincount(grey_u8.ravel(), minlength=256) / grey_u8.size
    entropy = float(-(hist[hist > 0] * np.log2(hist[hist > 0])).sum())
    # Films span the grey scale (entropy 7.1-7.9 measured); documents,
    # drawings and shapes are a few flat levels (0.5-4.0).
    if entropy < 6.0:
        return False, "it has too few grey levels for a radiograph (document, drawing or shapes)"
    error = _radiograph_reconstruction_error(image)
    # torchxrayvision's chest-X-ray autoencoder: real films reconstruct with
    # mean abs error 42-54; documents, blurred blobs and noise 127-195.
    if error is not None and error > 100.0:
        return False, f"it is unlike the chest radiographs the model was trained on (score {error:.0f})"
    return True, ""


_XRV_AE = None


def _radiograph_reconstruction_error(image: Any) -> Optional[float]:
    """Mean abs reconstruction error from torchxrayvision's chest X-ray
    autoencoder, or None if it can't be loaded (the gate then relies on the
    cheaper checks above)."""
    global _XRV_AE
    try:
        import numpy as np
        import torch
        import torchxrayvision as xrv

        if _XRV_AE is None:
            _XRV_AE = xrv.autoencoders.ResNetAE(weights="101-elastic").eval()
        grey = np.asarray(image.convert("L"), dtype=np.float32)
        grey = xrv.datasets.normalize(grey, 255)[None]
        height, width = grey.shape[1:]
        side = min(height, width)
        top, left = (height - side) // 2, (width - side) // 2
        grey = xrv.datasets.XRayResizer(224)(grey[:, top:top + side, left:left + side])
        tensor = torch.from_numpy(grey)[None].float()
        with torch.no_grad():
            recon = _XRV_AE(tensor)["out"]
        return float(torch.mean(torch.abs(recon - tensor)))
    except Exception as exc:
        logger.warning("Radiograph autoencoder unavailable (%s); using heuristic gate only", exc)
        return None


def run_image_pipeline(
    image_path: str,
    image_type: str = "xray",
    patient_id: Optional[str] = None,
    job_id: Optional[str] = None,
    clinical_context: str = "",
) -> PipelineResponse:
    """DICOM/JPG/PNG -> MedSAM -> CheXNet -> GradCAM -> optional RAG explanation."""
    ctx = _make_context("image", patient_id, job_id)
    logger.info("[%s] Starting image pipeline: %s", ctx.job_id, image_path)

    try:
        validation = _validate_file_safe(image_path)
        if not validation.valid:
            return _build_error_response(ctx, f"Validation failed: {validation.reason}")

        ext = Path(image_path).suffix.lower()
        if ext == ".dcm":
            dicom_out = _preprocess_dicom_safe(image_path)
            primary_image_array = dicom_out.pixel_array
        else:
            norm_out = _preprocess_image_safe(image_path)
            if getattr(norm_out, "rejected", False):
                return _build_error_response(ctx, f"Quality gate: {norm_out.reject_reason}", "low_quality")
            primary_image_array = norm_out.normalized

        from PIL import Image

        yolo_result: Dict[str, Any] = {
            "detections": [],
            "annotated_path": None,
            "model_used": "none",
            "roi_bbox": [],
            "primary_finding": None,
            "yolo_confidence": 0.0,
        }
        yolo_regions: List[str] = []
        if ext != ".dcm":
            try:
                from app.services.yolo_detector import detect_lung_disease, get_yolo_gradcam_regions

                yolo_result = detect_lung_disease(image_path, job_id=ctx.job_id)
                height, width = primary_image_array.shape[:2]
                yolo_regions = get_yolo_gradcam_regions(yolo_result, width, height)
                logger.info(
                    "[%s] YOLO model=%s detections=%s",
                    ctx.job_id,
                    yolo_result.get("model_used"),
                    len(yolo_result.get("detections") or []),
                )
            except Exception as exc:
                logger.warning("[%s] YOLO detector skipped: %s", ctx.job_id, exc)

        segmentation = None
        roi_pil = None
        seg = _run_segmentation_safe(primary_image_array, ctx)
        if seg is not None:
            segmentation = {
                "bbox": list(getattr(seg, "bbox", []) or []),
                "confidence": float(getattr(seg, "confidence", 0.0) or 0.0),
                "mask_pixels": int(getattr(seg, "segmentation_mask", 0).sum())
                if hasattr(getattr(seg, "segmentation_mask", None), "sum")
                else 0,
                "num_contours": len(getattr(seg, "contours", []) or []),
            }
            roi_crop = getattr(seg, "roi_crop", None)
            if roi_crop is not None and getattr(roi_crop, "size", 0):
                roi_pil = Image.fromarray(roi_crop).convert("RGB")

        if roi_pil is None and yolo_result.get("roi_bbox"):
            try:
                x1, y1, x2, y2 = [int(value) for value in yolo_result["roi_bbox"][:4]]
                height, width = primary_image_array.shape[:2]
                x1, x2 = max(0, x1), min(width, x2)
                y1, y2 = max(0, y1), min(height, y2)
                if x2 > x1 and y2 > y1:
                    roi_pil = Image.fromarray(primary_image_array[y1:y2, x1:x2]).convert("RGB")
                    segmentation = segmentation or {}
                    segmentation["bbox"] = [x1, y1, x2, y2]
                    segmentation["source"] = "yolo_coco_fallback"
            except Exception as exc:
                logger.warning("[%s] YOLO ROI crop failed: %s", ctx.job_id, exc)

        if roi_pil is None:
            roi_pil = Image.fromarray(primary_image_array).convert("RGB")

        normalized_image_type = str(image_type or "xray").lower().strip()
        is_chest_modality = normalized_image_type in CHEST_MODALITIES

        # The chest X-ray model is trained on whole radiographs with its own
        # normalisation. It used to receive the MedSAM ROI crop after CLAHE /
        # denoise / contrast stretch, which shifted its outputs (e.g. a Mass
        # at 0.72 on the raw film was reported as "Pneumonia 50%").
        classifier_input = roi_pil
        if is_chest_modality:
            classifier_input = _whole_radiograph(image_path, primary_image_array)
            plausible, why = radiograph_plausibility(classifier_input)
            if not plausible:
                return _build_error_response(
                    ctx,
                    f"Quality gate: this does not look like a chest radiograph ({why}). "
                    "No findings were produced.",
                    "low_quality",
                )

        if MODALITY_ROUTER_AVAILABLE:
            classification = classify_by_modality(
                image_path=image_path,
                modality=normalized_image_type,
                fallback_classify_fn=classify_image,
                fallback_image=classifier_input,
            )
        else:
            classification = classify_image(classifier_input)

        if is_dataclass(classification):
            classification_json = to_json_safe(asdict(classification))
        else:
            classification_json = to_json_safe(classification)

        gradcam_path = None
        if is_chest_modality:
            try:
                gradcam_path = generate_gradcam_overlay(classifier_input, job_id=ctx.job_id)["gradcam_path"]
                if hasattr(classification, "gradcam_path"):
                    classification.gradcam_path = gradcam_path
                if isinstance(classification_json, dict):
                    classification_json["gradcam_path"] = gradcam_path
            except Exception as exc:
                logger.warning("[%s] GradCAM failed: %s", ctx.job_id, exc)

        rag = _rag_service()
        from app.services.fix_image_analysis import (
            filter_keyword_fallback_sources,
            get_severity,
            get_severity_color,
            retrieve_image_evidence_from_main_kb,
        )

        explanation = (
            f"Image classified as {classification.label} with "
            f"{classification.confidence:.2%} confidence."
        )
        sources = []
        uncertainty_flag = classification.confidence < 0.6
        gradcam_regions = []
        if segmentation and segmentation.get("bbox"):
            gradcam_regions.append(f"ROI bbox {segmentation['bbox']}")
        gradcam_regions.extend(yolo_regions)
        try:
            sources = retrieve_image_evidence_from_main_kb(
                classification.label,
                gradcam_regions=gradcam_regions,
                top_k=5,
            )
        except Exception as exc:
            logger.warning("[%s] Main WHO image retrieval fallback: %s", ctx.job_id, exc)
        if rag is not None:
            try:
                if len(sources) < 2:
                    fallback_sources = rag.retrieve_evidence(
                        f"{image_type} {classification.label} {clinical_context}".strip(),
                        report_type="image",
                    )
                    sources = filter_keyword_fallback_sources((sources or []) + (fallback_sources or []))
                explanation = rag.explain_image(
                    image_type=image_type,
                    classification=classification_json,
                    segmentation=segmentation or {},
                    sources=sources,
                )
                uncertainty_flag = uncertainty_flag or not bool(sources)
            except Exception as exc:
                logger.warning("[%s] Image RAG fallback: %s", ctx.job_id, exc)

        who_outputs = {}
        try:
            from app.services.image_analysis_service import build_who_image_outputs

            who_outputs = build_who_image_outputs(
                label=classification.label,
                confidence=classification.confidence,
                segmentation=segmentation or {},
                patient_id=ctx.patient_id,
                gradcam_regions=gradcam_regions,
            )
            structured = who_outputs.get("structured_explanation") or {}
            explanation = structured.get("clinical_interpretation") or explanation
            uncertainty_flag = uncertainty_flag or bool(structured.get("uncertainty_flag"))
        except Exception as exc:
            logger.warning("[%s] WHO image enrichment fallback: %s", ctx.job_id, exc)

        evidence_sources = filter_keyword_fallback_sources((who_outputs.get("radiology_evidence") or []) + (sources or []))
        confidence_value = float(getattr(classification, "confidence", 0.0) or 0.0)
        confidence_pct = round(confidence_value * 100 if confidence_value <= 1 else confidence_value, 2)
        classification_payload = dict(classification_json) if isinstance(classification_json, dict) else {}
        severity = classification_payload.get("severity") or get_severity(confidence_pct)
        classification_payload.update(
            {
                "classification_prob": confidence_pct,
                "detection_confidence": confidence_pct,
                "label_classification": "Class probability",
                "label_detection": "Detection confidence",
                "severity": severity,
                "severity_color": get_severity_color(severity),
            }
        )
        return PipelineResponse(
            job_id=ctx.job_id,
            patient_id=ctx.patient_id,
            pipeline_type="image",
            status="completed",
            image_classification=classification_payload,
            yolo_detections=to_json_safe(yolo_result.get("detections") or []),
            yolo_annotated_path=yolo_result.get("annotated_path"),
            yolo_model_used=yolo_result.get("model_used"),
            yolo_roi_bbox=list(yolo_result.get("roi_bbox") or []),
            label_metadata=who_outputs.get("label_metadata"),
            radiology_evidence=evidence_sources,
            who_structured_report=who_outputs.get("who_structured_report"),
            segmentation=segmentation,
            verdict=(who_outputs.get("structured_explanation") or {}).get("verdict", "Uncertain"),
            risk_score=confidence_pct,
            risk_label=severity,
            confidence_score=confidence_pct,
            confidence_label=severity,
            explanation=explanation,
            plain_language_summary=(who_outputs.get("structured_explanation") or {}).get("patient_friendly_summary", explanation),
            sources=evidence_sources or sources,
            uncertainty_flag=uncertainty_flag,
            gradcam_path=gradcam_path,
            processing_time_ms=ctx.elapsed_ms,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        logger.error("[%s] Image pipeline crashed: %s", ctx.job_id, traceback.format_exc())
        return _build_error_response(ctx, str(exc))


# ============================================================================
# PIPELINE 2 - REPORT ANALYSIS
# ============================================================================

_NO_LABS_MESSAGE = (
    "Insufficient evidence: no lab values could be extracted from this report, "
    "so no risk assessment was made. Check the file is a readable lab report "
    "and review it manually."
)


def _composition_history_reader(patient_id: str, job_id: Optional[str]) -> List[Dict[str, Any]]:
    from app.services import patient_history_service

    return patient_history_service.get_patient_history_sync(patient_id, limit=5, owner_job_id=job_id)


def run_report_pipeline(
    file_path: str,
    patient_id: Optional[str] = None,
    report_type: str = "lab",
    job_id: Optional[str] = None,
    gender: Optional[str] = None,
    age: Optional[int] = None,
    explanation_mode: str = "brief",
) -> PipelineResponse:
    """TEST/EVAL COMPOSITION - never called by a route or task (enforced by tests/test_s1_static.py).

    Extracts the report (``review.extract.extract_report``) and scores every extracted row exactly as shown
    (``review.scoring.score_confirmed_rows``): what a clinician's unedited Confirm would produce. In production
    the two halves run in separate tasks with the clinician's review between them (S1)."""
    from app.services.review.extract import extract_report
    from app.services.review.scoring import accept_as_shown, score_confirmed_rows

    ctx = _make_context("report", patient_id, job_id)
    logger.info("[%s] Starting report composition: type=%s", ctx.job_id, report_type)
    extraction = extract_report(file_path, report_type, gender, age)
    if extraction.status != "ok":
        return _build_error_response(ctx, "Validation failed" if extraction.error_code == "FILE_INVALID"
                                     else "The analysis failed.")
    accepted = accept_as_shown(extraction.rows)
    scored = score_confirmed_rows(
        accepted, sex=gender, age=age, patient_id=patient_id, job_id=ctx.job_id, explanation_mode=explanation_mode,
        unread_line_count=extraction.unread_count, history_reader=_composition_history_reader,
        report_type=report_type, ocr_confidence=extraction.ocr_confidence,
        ocr_engine="text" if extraction.file_kind == "csv" else "paddle")
    response = scored.response
    if not accepted and response.status == "completed":
        response = response.model_copy(update={"explanation": _NO_LABS_MESSAGE,
                                               "plain_language_summary": _NO_LABS_MESSAGE})
    return response


# ============================================================================
# PIPELINE 3 - CLAIM VERIFICATION
# ============================================================================

def run_claim_pipeline(
    claim_text: str,
    patient_id: Optional[str] = None,
    job_id: Optional[str] = None,
) -> PipelineResponse:
    """Claim text -> NER -> RAG verdict with graceful fallback."""
    ctx = _make_context("claim", patient_id, job_id)
    logger.info("[%s] Starting claim pipeline", ctx.job_id)

    try:
        if not claim_text or len(claim_text.strip()) < 10:
            return _build_error_response(ctx, "Claim text too short (min 10 chars)")

        ner_result = _run_ner_safe(claim_text)
        entities = {
            "conditions": list(getattr(ner_result, "conditions", []) or []),
            "medications": list(getattr(ner_result, "medications", []) or []),
        }
        verdict = _verify_claim_safe(claim_text, entities)

        return PipelineResponse(
            job_id=ctx.job_id,
            patient_id=ctx.patient_id,
            pipeline_type="claim",
            status="completed",
            extracted_entities=entities,
            verdict=verdict["verdict"],
            risk_score=verdict["confidence_score"],
            risk_label=_confidence_label(float(verdict["confidence_score"])),
            confidence_score=verdict["confidence_score"],
            confidence_label=_confidence_label(float(verdict["confidence_score"])),
            explanation=verdict["explanation"],
            sources=verdict["sources"],
            uncertainty_flag=verdict["uncertainty_flag"],
            hallucination_flagged=verdict["hallucination_flagged"],
            processing_time_ms=ctx.elapsed_ms,
            completed_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as exc:
        logger.error("[%s] Claim pipeline crashed: %s", ctx.job_id, traceback.format_exc())
        return _build_error_response(ctx, str(exc))


# ============================================================================
# UNIFIED ENTRY POINT
# ============================================================================

def run_pipeline(input_data: Dict[str, Any], job_id: Optional[str] = None) -> PipelineResponse:
    """Smart router. Dispatches to image, report, or claim pipeline."""
    pipeline_type = input_data.get("type")
    patient_id = input_data.get("patient_id")

    if pipeline_type == "image":
        return run_image_pipeline(
            image_path=input_data["file_path"],
            image_type=input_data.get("image_type", "xray"),
            patient_id=patient_id,
            job_id=job_id,
            clinical_context=input_data.get("clinical_context", ""),
        )
    if pipeline_type == "report":
        return run_report_pipeline(
            file_path=input_data["file_path"],
            patient_id=patient_id,
            report_type=input_data.get("report_type", "lab"),
            job_id=job_id,
            gender=input_data.get("gender"),
            age=input_data.get("age"),
        )
    if pipeline_type == "claim":
        return run_claim_pipeline(
            claim_text=input_data["claim_text"],
            patient_id=patient_id,
            job_id=job_id,
        )

    ctx = _make_context("unknown", patient_id, job_id)
    return _build_error_response(ctx, f"Unknown pipeline type: {pipeline_type}")


# ============================================================================
# STANDALONE TEST
# ============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("VaidyaAI Master Pipeline - Self Test")
    print("=" * 60)

    result = run_claim_pipeline(
        claim_text="Aspirin reduces the risk of myocardial infarction by 50%.",
        patient_id="test-001",
    )
    print(json.dumps(to_json_safe(result.model_dump()), indent=2))

    result2 = run_pipeline(
        {
            "type": "claim",
            "claim_text": "Vitamin D deficiency causes osteoporosis.",
            "patient_id": "test-002",
        }
    )
    print(f"Status: {result2.status}")
    print(f"Verdict: {result2.verdict}")
    print(f"Time: {result2.processing_time_ms}ms")

    print("\nPipeline orchestrator ready")
