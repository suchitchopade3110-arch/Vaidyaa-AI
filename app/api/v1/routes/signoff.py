"""REG-02 — mandatory clinician sign-off.

POST /api/v1/jobs/{job_id}/sign-off

Writes a SignOff row, an audit_logs entry, and rejects a duplicate
sign-off for the same job_id with 409. Restricted to clinician/admin
roles. Enforcement on the export side lives elsewhere:
  - app/routes/pdf_reports.py: doesn't block the PDF download, but
    stamps it "DRAFT — NOT REVIEWED" when no SignOff row exists.
  - app/services/qr_service.py (require_signed_off): hard-blocks minting
    a QR share token — the un-authenticated, patient-facing path — until
    a SignOff row exists.

The job must exist, belong to the signing clinician (admins may sign any
job), and have finished running; anything else is a 404 (unknown / not
yours) or 409 (not finished). Previously any string was accepted, so a
clinician could "sign off" a job id that did not exist or belonged to
someone else — and a sign-off is what unlocks patient-facing QR sharing.

Cross-clinician review (a different clinician in the same org signing
off) waits on PLT-01 org scoping; until JWTs carry org_id, owner-or-admin
is the safe rule.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from celery.result import AsyncResult

from app.core.auth import require_role
from app.core.ownership import get_owned_job
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.sign_off import SignOff
from app.workers.celery_app import celery_app

router = APIRouter()


class SignOffRequest(BaseModel):
    model_versions: str  # e.g. "chexnet:v1;xgb:v3" — see SignOff.model_versions


class SignOffResponse(BaseModel):
    job_id: str
    clinician_id: str
    signed_at: str
    model_versions: str


def _job_outcome(job_id: str, record) -> str:
    """What the ANALYSIS produced, not just whether the Celery task returned.

    Pipelines return failures as ordinary results (status "failed" /
    "low_quality"), so Celery SUCCESS alone would let a failed analysis be
    signed off and shared. Once Celery has expired the result (PENDING for
    an id it no longer knows), fall back to the outcome the worker stored
    on async_jobs.status.
    """
    result = AsyncResult(job_id, app=celery_app)
    state = result.state
    if state == "SUCCESS":
        from app.workers.pipeline_tasks import pipeline_outcome

        return pipeline_outcome(result.result)
    if state == "PENDING" and record is not None and record.status not in (None, "", "queued"):
        return str(record.status).lower()
    return {"FAILURE": "failed", "REVOKED": "cancelled"}.get(state, state.lower())


@router.post("/{job_id}/sign-off", response_model=SignOffResponse, status_code=201)
async def sign_off_job(
    job_id: str,
    body: SignOffRequest,
    user: dict = Depends(require_role("clinician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Record a clinician sign-off for a job's result."""
    record = await get_owned_job(db, job_id, user, allow_admin=True)
    if record is None:
        raise HTTPException(status_code=404, detail="Job not found")

    outcome = _job_outcome(job_id, record)
    if outcome != "completed":
        raise HTTPException(
            status_code=409,
            detail={
                "code": "JOB_NOT_COMPLETE",
                "message": f"Only successfully completed analyses can be signed off (job status: {outcome}).",
            },
        )

    existing = await db.execute(select(SignOff).where(SignOff.job_id == job_id))
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "ALREADY_SIGNED_OFF",
                "message": f"job_id {job_id!r} already has a sign-off on record.",
            },
        )

    record = SignOff(
        id=uuid.uuid4(),
        job_id=job_id,
        clinician_id=uuid.UUID(user["sub"]),
        model_versions=body.model_versions,
    )
    db.add(record)

    db.add(AuditLog(
        user_id=uuid.UUID(user["sub"]),
        action="report.sign_off",
        resource_type="async_job",
        resource_id=job_id,
        status="success",
        details={"model_versions": body.model_versions},
    ))

    await db.flush()

    return SignOffResponse(
        job_id=record.job_id,
        clinician_id=str(record.clinician_id),
        signed_at=record.signed_at.isoformat() if record.signed_at else "",
        model_versions=record.model_versions,
    )
