"""Celery tasks that dispatch to the Phase 2 pipeline orchestrator."""

import logging
from typing import Optional

from celery import Task

from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _retry_delay(retries: int) -> int:
    """Exponential backoff: 10s, 40s, 160s."""
    return 10 * (4 ** retries)


def pipeline_outcome(result) -> str:
    """The pipeline's own verdict on a run. Pipelines report errors as a
    normal return value (status="failed" / "low_quality"), so Celery's
    SUCCESS only means "the task returned", not "the analysis worked"."""
    if isinstance(result, dict):
        return str(result.get("status") or "completed").lower()
    return "completed"


class PipelineTask(Task):
    abstract = True

    def on_success(self, retval, task_id, args, kwargs):
        from app.workers.db_persist import set_job_status, upsert_report_result

        outcome = pipeline_outcome(retval)
        set_job_status(task_id, outcome)
        if self.name.endswith("analyze_report_task") and outcome == "completed" and isinstance(retval, dict):
            upsert_report_result(task_id, retval)

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        logger.error("[%s] Pipeline task failed: %s", task_id, exc)
        from app.workers.db_persist import set_job_status

        set_job_status(task_id, "failed")


@celery_app.task(
    bind=True,
    base=PipelineTask,
    name="app.workers.pipeline_tasks.analyze_report_task",
    max_retries=3,
)
def analyze_report_task(
    self,
    file_path: str,
    patient_id: Optional[str],
    report_type: str,
    gender: Optional[str] = None,
    age: Optional[int] = None,
    explanation_mode: str = "brief",
):
    """Run report pipeline asynchronously."""
    job_id = self.request.id
    logger.info("[%s] Report task started: type=%s", job_id, report_type)
    try:
        from app.pipeline import run_report_pipeline, to_json_safe

        result = run_report_pipeline(
            file_path=file_path,
            patient_id=patient_id,
            report_type=report_type,
            job_id=job_id,
            gender=gender,
            age=age,
            explanation_mode=explanation_mode,
        )
        return to_json_safe(result.model_dump())
    except Exception as exc:
        logger.error("[%s] Report task failed: %s", job_id, exc)
        raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))


@celery_app.task(
    bind=True,
    base=PipelineTask,
    name="app.workers.pipeline_tasks.analyze_image_task",
    max_retries=3,
)
def analyze_image_task(
    self,
    file_path: str,
    image_type: str,
    patient_id: Optional[str],
    clinical_context: str = "",
):
    """Run image pipeline asynchronously."""
    job_id = self.request.id
    logger.info("[%s] Image task started: type=%s", job_id, image_type)
    try:
        from app.pipeline import run_image_pipeline, to_json_safe

        result = run_image_pipeline(
            image_path=file_path,
            image_type=image_type,
            patient_id=patient_id,
            job_id=job_id,
            clinical_context=clinical_context,
        )
        return to_json_safe(result.model_dump())
    except Exception as exc:
        logger.error("[%s] Image task failed: %s", job_id, exc)
        raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))


@celery_app.task(
    bind=True,
    base=PipelineTask,
    name="app.workers.pipeline_tasks.verify_claim_task",
    max_retries=3,
)
def verify_claim_task(self, claim_text: str, patient_id: Optional[str]):
    """Run claim verification pipeline asynchronously."""
    job_id = self.request.id
    logger.info("[%s] Claim task started", job_id)
    try:
        from app.pipeline import run_claim_pipeline, to_json_safe

        result = run_claim_pipeline(
            claim_text=claim_text,
            patient_id=patient_id,
            job_id=job_id,
        )
        return to_json_safe(result.model_dump())
    except Exception as exc:
        logger.error("[%s] Claim task failed: %s", job_id, exc)
        raise self.retry(exc=exc, countdown=_retry_delay(self.request.retries))
