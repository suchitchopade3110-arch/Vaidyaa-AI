from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from uuid import UUID
from celery.result import AsyncResult
from sqlalchemy import func, select
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.ownership import require_job_owner
from app.db.session import get_db
from app.models.async_job import AsyncJobRecord
from app.workers.job_status import revoke_task
from app.workers.celery_app import celery_app

router = APIRouter()

_STATUS_MAP = {
    "PENDING": "queued",
    "STARTED": "running",
    "PROGRESS": "running",
    "PROCESSING": "running",
    "RETRY": "running",
    "SUCCESS": "completed",
    "FAILURE": "failed",
    "REVOKED": "failed",
}


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    progress: float = 0.0
    result: Optional[dict] = None
    error: Optional[str] = None
    completed_at: Optional[str] = None


class RecentJobItem(BaseModel):
    job_id: str
    pipeline: str          # "report" | "image" | "claim"
    celery_task_id: Optional[str]
    status: str
    created_at: Optional[datetime]

    model_config = {"from_attributes": True}


class RecentJobsResponse(BaseModel):
    jobs: List[RecentJobItem]
    total: int


@router.get(
    "/{task_id}",
    response_model=JobStatusResponse,
    summary="Poll live Celery task status",
)
async def job_status(task_id: str, _owner: AsyncJobRecord = Depends(require_job_owner)):
    """
    Real-time task state from Redis backend.
    States: PENDING → STARTED → SUCCESS | FAILURE | RETRY

    Use this for polling after submitting any /verify or /analyze request.
    """
    from app.workers.pipeline_tasks import pipeline_outcome

    result = await run_in_threadpool(AsyncResult, task_id, app=celery_app)
    state = await run_in_threadpool(lambda: result.state)
    if state == "SUCCESS":
        payload = await run_in_threadpool(lambda: result.result)
        # Pipelines report their own failures inside the result.
        api_status = pipeline_outcome(payload)
    elif state == "PENDING" and _owner.status not in (None, "", "queued"):
        payload = None
        api_status = _owner.status  # result expired from the backend; stored outcome
    else:
        payload = None
        api_status = _STATUS_MAP.get(state, "queued")

    finished = api_status not in ("queued", "running")
    progress = 1.0 if finished else 0.5 if api_status == "running" else 0.0
    response = JobStatusResponse(job_id=task_id, status=api_status, progress=progress)
    if payload is not None:
        response.result = payload
        response.completed_at = datetime.now(timezone.utc).isoformat()
    if api_status in ("failed", "low_quality"):
        message = (payload or {}).get("error") if isinstance(payload, dict) else None
        # Pipeline errors are user-facing messages; raw worker exceptions are not.
        response.error = message or "The analysis failed. See server logs for details."
    return response


@router.delete(
    "/{task_id}",
    summary="Cancel a queued or running task",
)
async def cancel_job(
    task_id: str,
    terminate: bool = False,
    _owner: AsyncJobRecord = Depends(require_job_owner),
):
    """
    Revoke a Celery task. Set terminate=true to SIGTERM a running worker.
    Use with caution on GPU image tasks.
    """
    revoke_task(task_id, terminate=terminate)
    return {"task_id": task_id, "cancelled": True}


@router.get(
    "/",
    response_model=RecentJobsResponse,
    summary="List the caller's recent jobs across all pipelines",
)
@router.get(
    "",
    response_model=RecentJobsResponse,
    summary="List the caller's recent jobs across all pipelines",
)
async def list_recent_jobs(
    limit: int = 20,
    offset: int = 0,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """SEC-03 — the caller's own jobs, newest first (from async_jobs).

    `status` is the live Celery state where the result backend still has
    it, falling back to the stored status once the result has expired.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail={"code": "INVALID_LIMIT", "message": "limit must be 1-100"})
    if offset < 0:
        raise HTTPException(status_code=400, detail={"code": "INVALID_OFFSET", "message": "offset must be >= 0"})

    owner = AsyncJobRecord.user_id == UUID(str(user["sub"]))
    total = (await db.execute(select(func.count()).select_from(AsyncJobRecord).where(owner))).scalar_one()
    rows = (
        await db.execute(
            select(AsyncJobRecord).where(owner)
            .order_by(AsyncJobRecord.created_at.desc())
            .limit(limit).offset(offset)
        )
    ).scalars().all()

    # Finished jobs carry their outcome on async_jobs.status (written by the
    # worker); only still-queued rows need a live Celery lookup, and those
    # blocking Redis calls run off the event loop.
    pending_ids = [row.id for row in rows if row.status in (None, "", "queued")]
    live = await run_in_threadpool(_live_states, pending_ids) if pending_ids else {}

    jobs = [
        RecentJobItem(
            job_id=row.id,
            pipeline=row.pipeline,
            celery_task_id=row.id,
            status=live.get(row.id) or row.status,
            created_at=row.created_at,
        )
        for row in rows
    ]
    return RecentJobsResponse(jobs=jobs, total=total)


def _live_states(task_ids: list[str]) -> dict[str, str]:
    states = {}
    for task_id in task_ids:
        state = AsyncResult(task_id, app=celery_app).state
        if state == "SUCCESS":
            from app.workers.pipeline_tasks import pipeline_outcome

            states[task_id] = pipeline_outcome(AsyncResult(task_id, app=celery_app).result)
        elif state != "PENDING":
            states[task_id] = _STATUS_MAP.get(state, "queued")
    return states
