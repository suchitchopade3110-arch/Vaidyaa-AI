from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from uuid import UUID
from celery.result import AsyncResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.core.auth import get_current_user
from app.core.ownership import require_job_owner
from app.models.async_job import AsyncJobRecord
from app.workers.job_status import revoke_task
from app.workers.celery_app import celery_app
from app.db.session import get_db

router = APIRouter()


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
    result = AsyncResult(task_id, app=celery_app)
    state = result.state
    status_map = {
        "PENDING": "queued",
        "STARTED": "running",
        "PROGRESS": "running",
        "PROCESSING": "running",
        "RETRY": "running",
        "SUCCESS": "completed",
        "FAILURE": "failed",
        "REVOKED": "failed",
    }
    api_status = status_map.get(state, "queued")
    progress = 1.0 if api_status == "completed" else 0.5 if api_status == "running" else 0.0
    response = JobStatusResponse(job_id=task_id, status=api_status, progress=progress)
    if api_status == "completed":
        response.result = result.result
        response.completed_at = datetime.now(timezone.utc).isoformat()
    elif api_status == "failed":
        response.error = str(result.info) if result.info else "Unknown error"
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
    summary="List recent jobs across all pipelines",
)
@router.get(
    "",
    response_model=RecentJobsResponse,
    summary="List recent jobs across all pipelines",
)
async def list_recent_jobs(
    limit: int = 20,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the user's most recent jobs from the async_jobs table.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail={"code": "INVALID_LIMIT", "message": "limit must be 1-100"})

    query = (
        select(AsyncJobRecord)
        .where(AsyncJobRecord.user_id == UUID(user["sub"]))
        .order_by(AsyncJobRecord.created_at.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    jobs = result.scalars().all()

    total_query = select(func.count()).select_from(AsyncJobRecord).where(AsyncJobRecord.user_id == UUID(user["sub"]))
    total_result = await db.execute(total_query)
    total = total_result.scalar() or 0

    recent_jobs = []
    for job in jobs:
        recent_jobs.append(
            RecentJobItem(
                job_id=job.id,
                pipeline=job.pipeline,
                celery_task_id=job.id,
                status=job.status,
                created_at=job.created_at,
            )
        )

    return RecentJobsResponse(jobs=recent_jobs, total=total)
