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

Not done: validating job_id refers to a real, completed job before
accepting a sign-off (currently accepts any string).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import require_role
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.sign_off import SignOff

router = APIRouter()


class SignOffRequest(BaseModel):
    model_versions: str  # e.g. "chexnet:v1;xgb:v3" — see SignOff.model_versions


class SignOffResponse(BaseModel):
    job_id: str
    clinician_id: str
    signed_at: str
    model_versions: str


@router.post("/{job_id}/sign-off", response_model=SignOffResponse, status_code=201)
async def sign_off_job(
    job_id: str,
    body: SignOffRequest,
    user: dict = Depends(require_role("clinician", "admin")),
    db: AsyncSession = Depends(get_db),
):
    """Record a clinician sign-off for a job's result."""
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
