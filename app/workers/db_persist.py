"""
VaidyaAI — DB Persistence Helper for Celery Workers
Saves task results to PostgreSQL so data outlives Redis cache (1hr expiry).

Called at the END of every Celery task after all processing is done.
Uses synchronous psycopg2 — Celery workers are sync, not async.
"""

import json
import logging
from datetime import timezone, datetime
UTC = timezone.utc
from uuid import UUID

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _get_conn():
    """Sync PostgreSQL connection for Celery workers."""
    import psycopg2
    from app.core.config import settings
    
    return psycopg2.connect(settings.SYNC_DATABASE_URL)


def persist_claim(result: dict) -> bool:
    """
    Upsert claim result into claims table.
    Called by: claim_tasks.verify_claim() on success.
    """
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        cur.execute("""
            UPDATE claims SET
                status                 = %s,
                extracted_entities     = %s,
                retrieved_sources      = %s,
                source_count           = %s,
                verdict                = %s,
                explanation            = %s,
                confidence             = %s,
                uncertainty_flag       = %s,
                hallucination_detected = %s,
                hallucination_details  = %s,
                shap_values            = %s,
                disclaimer             = %s,
                completed_at           = %s
            WHERE id = %s
        """, (
            # Map finished status to the verdict for claims
            result.get("verdict", "UNCERTAIN").upper(),
            json.dumps(result.get("extracted_entities", {})),
            json.dumps(result.get("sources", [])),
            result.get("source_count", 0),
            result.get("verdict"),
            result.get("explanation"),
            result.get("confidence_score"),
            result.get("uncertainty_flag", False),
            result.get("hallucination_detected", False),
            json.dumps(result.get("hallucination_details", {})),
            json.dumps(result.get("shap_values", {})),
            result.get("medical_disclaimer"),
            _utc_now(),
            result["claim_id"],
        ))

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Claim {result['claim_id']} persisted to DB")
        return True

    except Exception as e:
        logger.error(f"DB persist failed for claim {result.get('claim_id')}: {e}")
        return False   # Non-fatal — Redis result still available


def persist_report(result: dict) -> bool:
    """
    Upsert report result into reports table.
    Called by: report_tasks.analyze_report() on success.
    """
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        cur.execute("""
            UPDATE reports SET
                status             = %s,
                raw_text           = %s,
                parsed_data        = %s,
                risk_score         = %s,
                risk_factors       = %s,
                shap_values        = %s,
                anomalies          = %s,
                explanation        = %s,
                retrieved_sources  = %s,
                confidence_score   = %s,
                uncertainty_flag   = %s,
                medical_disclaimer = %s,
                completed_at       = %s
            WHERE id = %s
        """, (
            "COMPLETE",
            result.get("raw_text_preview"),
            json.dumps(result.get("extracted_entities", {})),
            result.get("risk_score"),
            json.dumps(result.get("risk_factors", [])),
            json.dumps(result.get("shap_values", {})),
            json.dumps(result.get("anomalies", [])),
            result.get("explanation"),
            json.dumps(result.get("sources", [])),
            result.get("confidence_score"),
            result.get("uncertainty_flag", False),
            result.get("medical_disclaimer"),
            _utc_now(),
            result["report_id"],
        ))

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"Report {result['report_id']} persisted to DB")
        return True

    except Exception as e:
        logger.error(f"DB persist failed for report {result.get('report_id')}: {e}")
        return False


def persist_image_analysis(result: dict) -> bool:
    """
    Upsert image analysis result into image_analyses table.
    Called by: image_tasks.analyze_image() on success.
    """
    try:
        conn = _get_conn()
        cur  = conn.cursor()

        seg = result.get("segmentation", {})
        cls = result.get("classification", {})
        gcam = result.get("gradcam", {})

        cur.execute("""
            UPDATE image_analyses SET
                status                   = %s,
                dicom_metadata           = %s,
                segmentation_mask_path   = %s,
                segmentation_overlay     = %s,
                roi_metadata             = %s,
                segmentation_confidence  = %s,
                classification           = %s,
                classification_probs     = %s,
                gradcam_path             = %s,
                gradcam_regions          = %s,
                explanation              = %s,
                retrieved_sources        = %s,
                confidence               = %s,
                uncertainty_flag         = %s,
                anomaly_detected         = %s,
                medical_disclaimer       = %s,
                completed_at             = %s
            WHERE id = %s
        """, (
            "COMPLETE",
            json.dumps(result.get("dicom_metadata", {})),
            seg.get("mask_path"),
            seg.get("overlay_path"),
            json.dumps(seg.get("roi_bounding_box", {})),
            seg.get("confidence"),
            cls.get("top_class"),
            json.dumps(cls.get("probabilities", {})),
            gcam.get("heatmap_path"),
            json.dumps(gcam.get("top_regions", [])),
            result.get("explanation"),
            json.dumps(result.get("sources", [])),
            result.get("confidence_score"),
            result.get("uncertainty_flag", True),
            result.get("anomaly_detected", False),
            result.get("medical_disclaimer"),
            _utc_now(),
            result["analysis_id"],
        ))

        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"ImageAnalysis {result['analysis_id']} persisted to DB")
        return True

    except Exception as e:
        logger.error(f"DB persist failed for image {result.get('analysis_id')}: {e}")
        return False


_MARK_FAILED_TABLES = {"claims", "reports", "image_analyses"}


def mark_failed(table: str, record_id: str, error: str) -> bool:
    """
    Mark a record as failed in DB when task crashes.
    table: 'claims' | 'reports' | 'image_analyses'
    """
    if table not in _MARK_FAILED_TABLES:
        logger.error(f"mark_failed refused: unknown table '{table}'")
        return False
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        # `table` is validated against an allow-list above, so this is not
        # string-interpolated user input despite the f-string.
        cur.execute(
            f"UPDATE {table} SET status = 'FAILED', completed_at = %s WHERE id = %s",
            (_utc_now(), record_id)
        )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"mark_failed error for {table}/{record_id}: {e}")
        return False


def set_job_status(task_id: str, status: str) -> bool:
    """Record a job's outcome on its async_jobs row (SEC-01 table).

    The Celery result backend forgets results after `result_expires`; this
    is the durable copy used by the job list and by sign-off once the
    result has expired. `status` is the pipeline's own outcome
    ("completed", "failed", "low_quality", ...), truncated to the column.
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("UPDATE async_jobs SET status = %s WHERE id = %s", (str(status)[:20], str(task_id)))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"set_job_status failed for {task_id}: {e}")
        return False


def upsert_report_result(task_id: str, result: dict) -> bool:
    """Persist a finished report job to `reports` (keyed by celery_task_id).

    The live pipeline only kept results in the Celery backend, which expires
    them after 24 h — after that the QR preview / PDF returned 404 and
    patient history was always empty. Re-running for the same task updates
    the row instead of duplicating it.
    """
    import uuid as _uuid

    try:
        patient_uuid = None
        try:
            if result.get("patient_id"):
                patient_uuid = str(_uuid.UUID(str(result["patient_id"])))
        except (ValueError, TypeError):
            patient_uuid = None  # free-text patient refs aren't patient rows

        values = {
            "patient_id": patient_uuid,
            "report_type": "lab" if result.get("panel_type") or result.get("lab_values") else "report",
            "status": str(result.get("status") or "completed").upper(),
            "parsed_data": json.dumps({"lab_values": result.get("lab_values") or [],
                                       "panel_type": result.get("panel_type")}, default=str),
            "risk_score": result.get("risk_score"),
            "anomalies": json.dumps(result.get("anomalies") or [], default=str),
            "explanation": result.get("explanation"),
            "confidence_score": result.get("confidence_score"),
            "uncertainty_flag": bool(result.get("uncertainty_flag", False)),
            "analysis_result": json.dumps(result, default=str),
            "source_citations": json.dumps(result.get("sources") or [], default=str),
            "task_id": str(task_id),
        }
        conn = _get_conn()
        cur = conn.cursor()
        if patient_uuid:
            cur.execute(
                "INSERT INTO patients (id, patient_code) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (patient_uuid, "P-" + patient_uuid.replace("-", "")),
            )
        cur.execute(
            """
            UPDATE reports SET patient_id=%(patient_id)s, report_type=%(report_type)s, status=%(status)s,
                   parsed_data=%(parsed_data)s, risk_score=%(risk_score)s, anomalies=%(anomalies)s,
                   explanation=%(explanation)s, confidence_score=%(confidence_score)s,
                   uncertainty_flag=%(uncertainty_flag)s, analysis_result=%(analysis_result)s,
                   source_citations=%(source_citations)s, completed_at=NOW()
             WHERE celery_task_id=%(task_id)s
            """,
            values,
        )
        if cur.rowcount == 0:
            cur.execute(
                """
                INSERT INTO reports (id, patient_id, report_type, status, parsed_data, risk_score, anomalies,
                                     explanation, confidence_score, uncertainty_flag, analysis_result,
                                     source_citations, celery_task_id, created_at, completed_at)
                VALUES (%(id)s, %(patient_id)s, %(report_type)s, %(status)s, %(parsed_data)s, %(risk_score)s,
                        %(anomalies)s, %(explanation)s, %(confidence_score)s, %(uncertainty_flag)s,
                        %(analysis_result)s, %(source_citations)s, %(task_id)s, NOW(), NOW())
                """,
                {**values, "id": str(_uuid.uuid4())},
            )
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"upsert_report_result failed for {task_id}: {e}")
        return False


# ── INSERT on submission (routes call these) ──────────────────────────────────

def insert_claim(claim_id: str, claim_text: str, task_id: str, patient_id: str = None) -> bool:
    """Insert pending claim row when route receives submission."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO claims (id, claim_text, status, celery_task_id, created_at, uncertainty_flag, hallucination_detected)
            VALUES (%s, %s, 'PENDING', %s, %s, FALSE, FALSE)
            ON CONFLICT (id) DO NOTHING
        """, (claim_id, claim_text, task_id, _utc_now()))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"insert_claim failed: {e}")
        return False


def insert_report(report_id: str, report_type: str, file_path: str,
                  file_format: str, task_id: str, patient_id: str = None) -> bool:
    """Insert pending report row when route receives submission."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO reports (id, report_type, file_path, file_format, status, celery_task_id, created_at, uncertainty_flag)
            VALUES (%s, %s, %s, %s, 'PENDING', %s, %s, FALSE)
            ON CONFLICT (id) DO NOTHING
        """, (report_id, report_type, file_path, file_format, task_id, _utc_now()))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"insert_report failed: {e}")
        return False


def insert_image_analysis(analysis_id: str, image_type: str, file_path: str,
                          file_format: str, task_id: str, patient_id: str = None) -> bool:
    """Insert pending image_analysis row when route receives submission."""
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO image_analyses (id, image_type, file_path, file_format, status, celery_task_id, created_at, uncertainty_flag, anomaly_detected)
            VALUES (%s, %s, %s, %s, 'PENDING', %s, %s, FALSE, FALSE)
            ON CONFLICT (id) DO NOTHING
        """, (analysis_id, image_type, file_path, file_format, task_id, _utc_now()))
        conn.commit()
        cur.close()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"insert_image_analysis failed: {e}")
        return False
