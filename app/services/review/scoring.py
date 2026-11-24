"""Scoring of clinician-confirmed rows: the ONLY producer of results (S1, plans/P2.md section 7).

``score_confirmed_rows`` is the scoring half of the pre-P2 report pipeline, fed exclusively by rows a clinician
confirmed. Nothing here reads raw report text: the model text, RAG query, entities and urine patterns are rebuilt
from confirmed rows (section 7.4). In production it is called only by the scoring task; the composition
``app.pipeline.run_report_pipeline`` (tests and eval) calls it with the rows accepted as shown.

Pipeline and engine helpers are reached through module attributes (``_pl.predict_tabular`` ...) so the existing
``monkeypatch.setattr(pipeline, ...)`` tests and the eval's spies and stubs still intercept them.
"""

from __future__ import annotations

import copy
import logging
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from app.services.lab_engine.evaluate import NON_BLOOD_SPECIMENS
from app.services.lab_engine.models import ParsedRow
from app.services.review.rows import deserialize_parsed, value_raw_from_parsed
from app.services.review.urine import urine_entities_from_rows

logger = logging.getLogger(__name__)

HistoryReader = Callable[[str, Optional[str]], List[Dict[str, Any]]]


@dataclass
class ConfirmedRow:
    """A confirmed row as the scorer sees it: the engine input plus the provenance carried into the result."""

    parsed: ParsedRow
    row_id: Optional[str] = None
    row_index: int = 0
    review_status: str = "accepted"            # accepted | edited | added
    origin: str = "text"
    specimen: Optional[str] = None
    source_page: Optional[int] = None
    source_bbox: Optional[list] = None
    source_text: Optional[str] = None
    confidence: float = 1.0


@dataclass
class ScoringOutput:
    response: Any                              # app.pipeline.PipelineResponse
    row_flags: List[str] = field(default_factory=list)   # aligned with the input rows


def to_confirmed(row: Any) -> ConfirmedRow:
    """Accept a ConfirmedRow, or any object with ``parsed_payload`` (RowData, a database record, an eval row)."""
    if isinstance(row, ConfirmedRow):
        return row
    parsed = getattr(row, "parsed", None)
    if parsed is None:
        parsed = deserialize_parsed(row.parsed_payload)
    else:
        parsed = copy.deepcopy(parsed)
    if getattr(row, "specimen", None) and not parsed.specimen:
        parsed.specimen = row.specimen
    rid = getattr(row, "row_id", None) or getattr(row, "id", None)
    bbox = getattr(row, "source_bbox", None)
    return ConfirmedRow(
        parsed=parsed, row_id=str(rid) if rid is not None else None, row_index=int(getattr(row, "row_index", 0) or 0),
        review_status=str(getattr(row, "status", None) or getattr(row, "review_status", None) or "accepted"),
        origin=str(getattr(row, "origin", "text") or "text"), specimen=parsed.specimen,
        source_page=getattr(row, "source_page", None), source_bbox=list(bbox) if bbox else None,
        source_text=getattr(row, "source_text", None), confidence=float(getattr(row, "confidence", 1.0) or 1.0))


def accept_as_shown(rows: Sequence[Any]) -> List[ConfirmedRow]:
    """Every row of kind ``row`` as it was extracted (TEST/EVAL composition: what Confirm would score unedited)."""
    out = []
    for r in rows:
        if getattr(r, "kind", "row") != "row" or getattr(r, "parsed_payload", None) is None:
            continue
        c = to_confirmed(r)
        c.review_status = "accepted"
        out.append(c)
    return out


def confirmed_text(parsed_rows: Sequence[ParsedRow]) -> str:
    """One line per confirmed row: the only text any model sees."""
    lines = []
    for p in parsed_rows:
        value = value_raw_from_parsed(p) or ""
        rng = f" ({p.ref_text})" if p.ref_text else ""
        lines.append(f"{p.name} {value} {p.unit or ''}{rng}".replace("  ", " ").strip())
    return "\n".join(lines)


def _ident(_pl: Any, r: Any) -> tuple:
    from app.services.lab_engine.analytes import canon_tokens

    value = round(r.value, 6) if r.value is not None else None
    return (r.analyte_key or tuple(sorted(canon_tokens(r.display_name))), value, r.qualifier, (r.text_value or "").lower())


def _row_mapping(_pl: Any, confirmed: List[ConfirmedRow], analysis: Any, sex: Any, age: Any):
    """(flags aligned with the input rows, survivor index per input row). A row merged into an identical duplicate
    takes the survivor's flag; a row the engine could not score at all is UNKNOWN."""
    by_parsed = {id(r.parsed): i for i, r in enumerate(analysis.rows)}
    flags: List[str] = []
    survivor: List[Optional[int]] = []
    for c in confirmed:
        idx = by_parsed.get(id(c.parsed))
        if idx is None:
            alone = _pl.analyze_lab_rows([copy.deepcopy(c.parsed)], sex, age).rows
            if alone:
                ident = _ident(_pl, alone[0])
                idx = next((i for i, s in enumerate(analysis.rows) if _ident(_pl, s) == ident), None)
        survivor.append(idx)
        flags.append(analysis.rows[idx].flag if idx is not None else "UNKNOWN")
    return flags, survivor


def _panel_type(scored_rows: Sequence[Any]) -> str:
    """The panel the confirmed (non-urine) rows belong to, by the column parser's own section/name mapping."""
    from app.services.lab_report_column_parser import detect_panel_type

    items = [{"name": r.display_name, "section": (r.parsed.section or "").strip().lower().replace(" ", "_")}
             for r in scored_rows if r.analyte_key is None or not str(r.analyte_key).startswith("urine_")]
    return detect_panel_type(items) if items else "general"


def _error(_pl: Any, ctx: Any, status: str = "failed") -> Any:
    logger.error("[%s] report scoring failed", ctx.job_id)
    return _pl.PipelineResponse(job_id=ctx.job_id, patient_id=ctx.patient_id, pipeline_type="report", status=status,
                                error="The analysis failed.", processing_time_ms=ctx.elapsed_ms,
                                completed_at=datetime.now(timezone.utc).isoformat())


def score_confirmed_rows(rows: Sequence[Any], *, sex: Any = None, age: Any = None, patient_id: Optional[str] = None,
                         job_id: Optional[str] = None, explanation_mode: str = "brief", unread_line_count: int = 0,
                         history_reader: Optional[HistoryReader] = None, report_type: str = "lab",
                         ocr_confidence: Optional[float] = None, ocr_engine: Optional[str] = None,
                         warnings_in: Sequence[str] = ()) -> ScoringOutput:
    """Score confirmed rows into a PipelineResponse. ``row_flags`` aligns with ``rows``."""
    from app import pipeline as _pl

    ctx = _pl._make_context("report", patient_id, job_id)
    try:
        return _score(_pl, ctx, rows, sex, age, patient_id, explanation_mode, unread_line_count, history_reader,
                      report_type, ocr_confidence, ocr_engine, list(warnings_in))
    except Exception:
        logger.error("[%s] confirmed-row scoring crashed: %s", ctx.job_id, traceback.format_exc())
        return ScoringOutput(response=_error(_pl, ctx), row_flags=["UNKNOWN"] * len(rows))


def _score(_pl, ctx, rows, sex_in, age_in, patient_id, explanation_mode, unread_line_count, history_reader,
           report_type, ocr_confidence, ocr_engine, extra_warnings) -> ScoringOutput:
    sex = _pl.normalize_sex(sex_in)
    patient_age = _pl.normalize_age(age_in)
    confirmed = [to_confirmed(r) for r in rows]
    parsed_rows = [c.parsed for c in confirmed]
    text = confirmed_text(parsed_rows)

    analysis = _pl.analyze_lab_rows(parsed_rows, sex, patient_age, unparsed_numeric_lines=unread_line_count,
                                    extra_warnings=tuple(extra_warnings))
    row_flags, survivor = _row_mapping(_pl, confirmed, analysis, sex, patient_age)
    by_scored: Dict[int, List[ConfirmedRow]] = {}
    for c, idx in zip(confirmed, survivor):
        if idx is not None:
            by_scored.setdefault(idx, []).append(c)

    lab_rows = [_pl._scored_to_row(r) for r in analysis.rows]
    for i, (row, d) in enumerate(zip(analysis.rows, lab_rows)):
        group = by_scored.get(i, [])
        own = next((c for c in group if c.parsed is row.parsed), group[0] if group else None)
        if own is not None:
            d.update({
                "row_id": own.row_id, "row_index": own.row_index, "review_status": own.review_status,
                "origin": own.origin, "specimen": own.specimen, "source_page": own.source_page,
                "source_bbox": own.source_bbox, "source_text": own.source_text, "confidence": own.confidence,
                "merged_row_ids": [c.row_id for c in group if c is not own and c.row_id],
            })

    lab_dict = _pl._engine_lab_dict(analysis, sex, patient_age)
    clinical_analysis = _pl.analyze_blood_report(
        lab_dict, gender=sex, age=patient_age, anomalies=_pl._engine_legacy_anomalies(analysis),
        critical_alerts=analysis.critical_alerts)
    entities: Dict[str, Any] = {"conditions": list(clinical_analysis["active_conditions"]), "medications": [], "dates": []}

    # --- urine: patterns come from confirmed urine rows only ------------------------------------------------
    def is_blood(r: Any) -> bool:
        # a row the clinician (or the reader) put in a non-blood specimen is never a blood row (S5)
        return _pl._is_blood_row(r) and (r.parsed.specimen or "serum") not in NON_BLOOD_SPECIMENS

    urine_scored = [r for r in analysis.rows if not _pl._is_blood_row(r) or r.parsed.specimen == "urine"]
    has_blood_rows = any(is_blood(r) for r in analysis.rows)
    urine_result = None
    panel_type = _panel_type(analysis.rows)
    urine_anomalies: List[Dict[str, Any]] = []
    urine_risk = 0.0
    if len(urine_scored) >= 2:
        from app.services import clinical_ner_service as ner

        urine_entities = urine_entities_from_rows(urine_scored)
        patterns = ner.apply_urine_pattern_rules(urine_entities)
        urine_entities["detected_patterns"] = patterns
        evidence: List[Dict[str, Any]] = []
        if patterns:
            try:
                from app.services.ingest_urinalysis_kb import query_urinalysis_kb

                evidence = query_urinalysis_kb(" ".join(patterns), n_results=3)   # pattern names only (S1)
            except Exception as exc:
                logger.warning("[%s] Urinalysis knowledge base skipped: %s", ctx.job_id, type(exc).__name__)
        urine_result = {"entities": urine_entities, "rag_evidence": evidence, "features": ner.urine_features(urine_entities),
                        "model_used": "confirmed-rows"}
        urine_anomalies = _pl._urine_pattern_anomalies(patterns, evidence)
        urine_risk = _pl._urine_risk_from_patterns(patterns)

    tabular_prediction = None
    anomalies: List[Dict[str, Any]] = []
    risk_score: Optional[float] = 50.0
    risk_factors: List[Any] = []
    if urine_result:
        risk_factors = [{"feature": p, "shap": 0.0} for p in urine_result["entities"]["detected_patterns"]]
        if has_blood_rows:
            entities["urinalysis"] = urine_result["entities"]
        else:
            panel_type = "urinalysis"
            entities = urine_result["entities"]
            lab_dict = urine_result["features"]
            risk_score = urine_risk
            tabular_prediction = {"report_type": "urinalysis", "features": urine_result["features"],
                                  "rag_evidence": urine_result["rag_evidence"], "model_used": "confirmed-rows"}
    pure_urine = bool(urine_result) and not has_blood_rows

    if analysis.rows:
        anomalies = _pl._engine_anomaly_dicts(analysis)
        if not pure_urine:
            risk_map = {"normal": 0.0, "moderate": 30.0, "high": 70.0, "critical": 95.0}
            risk_score = risk_map.get(str(clinical_analysis["risk_level"]).lower(), 30.0 if anomalies else 0.0)
            risk_factors = clinical_analysis["conditions"]
            try:
                tabular = _pl.predict_tabular(lab_dict, text)
                tabular_prediction = _pl.to_json_safe(tabular)
                tabular_prediction["anomalies"] = {}
                if not risk_factors:
                    risk_factors = tabular_prediction.get("top_contributors", [])
            except Exception as exc:
                logger.warning("[%s] XGBoost skipped/failed: %s", ctx.job_id, type(exc).__name__)

    if anomalies and (risk_score is None or risk_score <= 0.05) and not pure_urine:
        risk_score = max(_pl._fallback_risk_from_anomalies(anomalies), 30.0)

    ensemble_details = None
    risk_label: Any = None
    if pure_urine:
        anomalies = _pl._deduplicate_anomalies(anomalies + urine_anomalies)
        risk_label = _pl._confidence_label(risk_score or 0.0)
        ensemble_details = {
            "ensemble_risk_score": risk_score, "risk_label": risk_label,
            "model_scores": {"urinalysis_rules": risk_score}, "model_weights": {"urinalysis_rules": 1.0},
            "model_agreement": "rules", "agreement_std_dev": 0.0, "shap_values": {}, "top_factors": risk_factors,
            "anomalies": anomalies, "uncertainty_flag": not bool(anomalies), "confidence": risk_score,
            "models_used": ["urinalysis_rules"]}
    else:
        try:
            from app.services.ensemble_predictor import ensemble_predict

            ensemble_details = ensemble_predict(
                ml_features=lab_dict, lab_values=_pl._ensemble_rows(lab_rows),
                clinical_risk_level=clinical_analysis.get("risk_level"))
            risk_score = float(ensemble_details.get("ensemble_risk_score", risk_score))
            risk_label = ensemble_details.get("risk_label")
            ensemble_details["anomalies"] = anomalies         # the engine's anomalies are authoritative
            if ensemble_details.get("top_factors"):
                risk_factors = ensemble_details["top_factors"]
        except Exception as exc:
            logger.warning("[%s] Ensemble predictor skipped: %s", ctx.job_id, type(exc).__name__)
            risk_label = _pl._confidence_label(risk_score or 0.0)

    # LAST word on risk: the engine's findings are a floor no model output can lower, and "critical" needs a
    # critical value (the ceiling).
    warnings: List[str] = list(analysis.warnings)
    risk_label, risk_score, floor_uncertain = _pl.apply_risk_floor(analysis, risk_label, risk_score)
    if str(risk_label).lower() == "critical" and not any(getattr(r, "critical", None) for r in analysis.rows):
        risk_label = "high"
        risk_score = min(float(risk_score), 89.0) if risk_score is not None else risk_score
    if sex is None or patient_age is None:
        missing = " and ".join(n for n, v in (("sex", sex), ("age", patient_age)) if v is None)
        warnings.append(f"Patient {missing} not provided: reference ranges that depend on it were not assumed, "
                        "and the statistical risk model ran without it (its score is approximate).")
    if urine_result and has_blood_rows:
        panel_type = f"{panel_type}+urinalysis"
        anomalies = _pl._deduplicate_anomalies(anomalies + urine_anomalies)
        if urine_risk and (risk_score is None or urine_risk > float(risk_score)):
            urine_label = _pl._confidence_label(urine_risk)
            if _pl._RISK_ORDER.get(str(urine_label).lower(), 0) > _pl._RISK_ORDER.get(str(risk_label).lower(), 0):
                risk_label = urine_label
            risk_score = urine_risk
    score_num = float(risk_score) if risk_score is not None else 0.0

    differential_diagnosis = None
    explanation_brief = explanation_full = None
    try:
        from app.services.differential_diagnosis import generate_differential, generate_explanation

        differential_diagnosis = generate_differential(entities=entities, anomalies=anomalies, risk_score=score_num,
                                                       report_type=report_type)
        explanation_mode = "full" if str(explanation_mode).lower() == "full" else "brief"
        generated = generate_explanation(entities=entities, anomalies=anomalies, risk_score=score_num,
                                         risk_label=risk_label, mode=explanation_mode)
        explanation_brief = (generated if explanation_mode == "brief"
                             else generate_explanation(entities, anomalies, score_num, risk_label, mode="brief"))
        explanation_full = generated if explanation_mode == "full" else None
    except Exception as exc:
        logger.warning("[%s] Differential diagnosis skipped: %s", ctx.job_id, type(exc).__name__)
        explanation_mode = "brief"

    rag_result = _pl._explain_report_safe(raw_text=text, entities=entities, risk_score=score_num,
                                          risk_factors=risk_factors, anomalies=anomalies, report_type=report_type,
                                          panel_type=panel_type)

    patient_history: List[Dict[str, Any]] = []
    history_comparison = None
    if patient_id and history_reader is not None:
        try:
            from app.services.patient_history_service import compare_with_latest_history

            patient_history = history_reader(patient_id, ctx.job_id)
            history_comparison = compare_with_latest_history(
                {"report_id": ctx.job_id, "risk_score": score_num, "lab_values": lab_rows,
                 "extracted_entities": entities, "completed_at": datetime.now(timezone.utc).isoformat()},
                patient_history)
        except Exception as exc:
            logger.warning("[%s] Patient history comparison skipped: %s", ctx.job_id, type(exc).__name__)

    confidence_label = (tabular_prediction.get("confidence", "MEDIUM") if tabular_prediction
                        else risk_label or _pl._confidence_label(float(rag_result.get("confidence_score", 50.0))))
    if risk_label == "unknown":
        confidence_label = "low"
    explanation_brief = _pl.decorate_explanation(explanation_brief, analysis)
    explanation_full = _pl.decorate_explanation(explanation_full, analysis) if explanation_full else None
    rag_result = dict(rag_result)
    rag_result["explanation"] = _pl.decorate_explanation(rag_result.get("explanation"), analysis)
    rag_result["plain_language_summary"] = _pl.decorate_explanation(rag_result.get("plain_language_summary"), analysis)

    common = dict(job_id=ctx.job_id, patient_id=ctx.patient_id, pipeline_type="report", status="completed",
                  critical_alerts=list(analysis.critical_alerts), confirmed=True,
                  ocr_engine=ocr_engine, ocr_confidence=ocr_confidence, processing_time_ms=ctx.elapsed_ms,
                  completed_at=datetime.now(timezone.utc).isoformat())
    if not lab_rows:
        message = ("Insufficient evidence: no lab values were confirmed for this report, so no risk assessment was "
                   "made. Check the file is a readable lab report and review it manually.")
        resp = _pl.PipelineResponse(
            extracted_entities=entities, lab_values=[], anomalies=[], warnings=warnings, panel_type=panel_type,
            verdict="Uncertain", risk_score=None, risk_label="unknown", confidence_score=0.0, confidence_label="low",
            explanation=message, plain_language_summary=message, sources=[], uncertainty_flag=True,
            hallucination_flagged=False, **common)
        return ScoringOutput(resp, row_flags)

    resp = _pl.PipelineResponse(
        tabular_prediction=tabular_prediction, ensemble_details=ensemble_details, extracted_entities=entities,
        lab_values=lab_rows, anomalies=anomalies, warnings=warnings, panel_type=panel_type,
        verdict=rag_result["verdict"], risk_score=risk_score, risk_label=risk_label or confidence_label,
        differential_diagnosis=differential_diagnosis, explanation_mode=explanation_mode,
        explanation_brief=explanation_brief, explanation_full=explanation_full,
        rag_explanation=rag_result["explanation"], confidence_score=rag_result["confidence_score"],
        confidence_label=confidence_label, explanation=explanation_brief or rag_result["explanation"],
        plain_language_summary=explanation_brief or rag_result["plain_language_summary"], sources=rag_result["sources"],
        uncertainty_flag=(rag_result["uncertainty_flag"] or bool((ensemble_details or {}).get("uncertainty_flag"))
                          or floor_uncertain),
        hallucination_flagged=rag_result["hallucination_flagged"], shap_top_factors=risk_factors,
        patient_history=patient_history[:3], history_comparison=history_comparison,
        ner_engine="confirmed-rows", **common)
    return ScoringOutput(resp, row_flags)
