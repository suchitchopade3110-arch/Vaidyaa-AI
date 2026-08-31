from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timezone
from uuid import UUID
from celery.result import AsyncResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.ownership import require_job_owner
from app.core.tenancy import scope_to_org
from app.db.session import get_db
from app.models.async_job import AsyncJobRecord
from app.workers.job_status import revoke_task
from app.workers.celery_app import celery_app

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


def build_recent_jobs_query(user: dict, limit: int, org: bool):
    """SEC-03 + PLT-01 — construct (but don't execute) the recent-jobs
    query for `user`. Split out from the route so it can be unit-tested
    without a database: the property that matters (org_id always comes
    from the caller's own token, never a client-supplied parameter) is
    verifiable by inspecting the returned Select object directly.

    Default: only the caller's own jobs (`user_id == sub`), newest first.
    `org=True` (admin role only): every job whose owner is in the
    caller's own org — via scope_to_org, so the org_id filter can only
    ever be the caller's own, by construction. Non-admins passing
    org=True get 403, not a silent fallback to self-only (a client
    should not be able to think it asked for org-wide data and get a
    quietly narrower answer back).
    """
    query = select(AsyncJobRecord).order_by(AsyncJobRecord.created_at.desc()).limit(limit)

    if org:
        if user.get("role") != "admin":
            raise HTTPException(status_code=403, detail="org-wide job listing requires the admin role")
        org_id = user.get("org_id")
        if org_id is None:
            raise HTTPException(
                status_code=422,
                detail="caller has no org_id on their token — nothing to scope org=true by",
            )
        return scope_to_org(query, UUID(org_id), AsyncJobRecord.org_id)

    return query.where(AsyncJobRecord.user_id == UUID(user["sub"]))


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
    org: bool = False,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List jobs the caller can see: their own by default, or (admin role
    only) every job submitted within their organisation with `org=true`.
    """
    if limit < 1 or limit > 100:
        raise HTTPException(status_code=400, detail={"code": "INVALID_LIMIT", "message": "limit must be 1-100"})

    query = build_recent_jobs_query(user, limit, org)
    result = await db.execute(query)
    rows = result.scalars().all()

    return RecentJobsResponse(
        jobs=[
            RecentJobItem(
                job_id=row.id,
                pipeline=row.pipeline,
                celery_task_id=row.id,
                status=row.status,
                created_at=row.created_at,
            )
            for row in rows
        ],
        total=len(rows),
    )
