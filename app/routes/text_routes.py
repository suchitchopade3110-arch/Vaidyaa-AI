from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

from app.core.auth import get_current_user
from app.models.request_models import QueryRequest
from app.services.pipeline_controller import run_text_pipeline

router = APIRouter()


@router.post("/text", summary="Run the direct synchronous text pipeline")
@limiter.limit("20/minute")
async def text_pipeline(request: Request, data: QueryRequest, user: dict = Depends(get_current_user)):
    """Run the text/lab-value pipeline synchronously (no Celery job/polling).

    Returns a finding-review summary, retrieval/extraction confidence,
    evidence, SHAP values, entities, risk factors, and anomalies directly
    in the response.
    """
    try:
        result = run_text_pipeline(data.query)
        return {
            "type": "text_pipeline",
            "risk_label": result["risk_label"],
            "confidence": result["confidence"],
            "evidence": result["evidence"],
            "explanation": result["explanation"],
            "shap_values": result["shap"],
            "entities": result["entities"],
            "risk_factors": result["risk_factors"],
            "anomalies": result["anomalies"],
            "status": "completed",
        }
    except Exception as exc:
        return {
            "error": {
                "code": "PIPELINE_ERROR",
                "message": str(exc),
                "retryable": False,
            }
        }

