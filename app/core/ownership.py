"""SEC-01 — per-user job ownership.

`require_job_owner` is real: it looks up the AsyncJobRecord for a task_id
and 404s (not 403 — see rationale below) on a missing record or an owner
mismatch. `record_job_ownership` is real too: call it once at job
submission to persist who owns a task.

What's NOT done, and is the actual remaining work:
  1. No submission route calls `record_job_ownership` yet — see the
     `# TODO(SEC-01)` markers in app/api/v1/routes/{reports,images,claims}.py.
  2. No status/result/pdf route depends on `require_job_owner` yet — same
     markers in app/api/v1/routes/jobs.py and app/routes/pdf_reports.py.

Both are deliberately left unwired: applying (1) without (2), or vice versa,
either does nothing or 404s every existing job (nothing has an owner row
yet). Wire both together in the same change, then delete this paragraph.
"""
import uuid

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.async_job import AsyncJobRecord


async def require_job_owner(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> AsyncJobRecord:
    """Resolve `task_id` to its AsyncJobRecord and enforce ownership.

    Raises 404 — not 403 — on both "no such job" and "not your job". A 403
    confirms the job ID exists for someone else, which is itself a leak;
    404 makes the two cases indistinguishable from the caller's side.

    TODO(SEC-01): admin/org-auditor bypass (PLT-01 roles) once those roles
    exist — an org admin should be able to read jobs submitted by their own
    org's clinicians, not just the submitting user.
    """
    result = await db.execute(select(AsyncJobRecord).where(AsyncJobRecord.id == task_id))
    record = result.scalar_one_or_none()

    if record is None or str(record.user_id) != user.get("sub"):
        raise HTTPException(status_code=404, detail="Job not found")

    return record


async def record_job_ownership(
    db: AsyncSession,
    task_id: str,
    user: dict,
    pipeline: str,
) -> AsyncJobRecord:
    """Persist ownership for a newly-submitted job. Call this once, right
    after dispatching the Celery task, from each submission route.

    TODO(SEC-01): `org_id` currently falls back to the user's own id when
    they have no org yet (see User.org_id nullability in PLT-01) so this
    doesn't NULL-violate the column before org onboarding exists. Replace
    with `user["org_id"]` once JWTs carry a real claim for it.
    """
    record = AsyncJobRecord(
        id=task_id,
        user_id=uuid.UUID(user["sub"]),
        org_id=uuid.UUID(user.get("org_id", user["sub"])),
        pipeline=pipeline,
        status="queued",
    )
    db.add(record)
    await db.flush()
    return record
