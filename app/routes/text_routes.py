import logging

from fastapi import APIRouter, Depends, Request

from app.core.auth import get_current_user
from app.core.rate_limit import limiter
from app.models.request_models import QueryRequest
from app.services.pipeline_controller import run_text_pipeline

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/text", summary="Run the direct synchronous text pipeline")
@limiter.limit("20/minute")
async def text_pipeline(request: Request, data: QueryRequest, user: dict = Depends(get_current_user)):
    """Run the text/lab-value pipeline synchronously (no Celery job/polling).

    Returns diagnosis, confidence, evidence, SHAP values, entities, risk
    factors, and anomalies directly in the response.
    """
    try:
        result = run_text_pipeline(data.query)
        return {
            "type": "text_pipeline",
            "diagnosis": result["diagnosis"],
            "confidence": result["confidence"],
            "evidence": result["evidence"],
            "explanation": result["explanation"],
            "shap_values": result["shap"],
            "entities": result["entities"],
            "risk_factors": result["risk_factors"],
            "anomalies": result["anomalies"],
            "status": "completed",
        }
    except Exception:
        logger.exception("Direct text pipeline failed")
        return {
            "error": {
                "code": "PIPELINE_ERROR",
                "message": "The text pipeline failed. See server logs for details.",
                "retryable": False,
            }
        }

