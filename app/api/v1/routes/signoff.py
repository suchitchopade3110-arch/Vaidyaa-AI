"""REG-02 — mandatory clinician sign-off.

POST /api/v1/jobs/{job_id}/sign-off

Route shape and persistence are real: this writes a SignOff row and returns
it. What's still TODO, and is the actual enforcement REG-02 needs:
  - An `audit_logs` entry alongside the SignOff row (actor, timestamp,
    job ID, model version) — the AuditLog model already exists
    (app/models/audit_log.py), this route just doesn't write to it yet.
  - Blocking PDF export / QR share / "complete" status until a SignOff row
    exists for that job_id — app/routes/pdf_reports.py and
    app/routes/qr_reports.py currently have no such check.
  - The "DRAFT — NOT REVIEWED" watermark on unsigned PDFs — stubbed in
    app/services/pdf_report.py as `apply_draft_watermark`, not called yet.
  - Restricting this endpoint to users with the clinician role once
    role-based checks are consistently applied (require_role exists in
    app/core/auth.py but isn't attached here yet).
"""
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.db.session import get_db
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
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Record a clinician sign-off for a job's result.

    TODO(REG-02): reject with 409 if a SignOff already exists for this
    job_id (currently allows duplicates — each POST just adds another row).
    TODO(REG-02): validate job_id refers to a completed job before
    accepting a sign-off (currently accepts any string).
    """
    record = SignOff(
        id=uuid.uuid4(),
        job_id=job_id,
        clinician_id=uuid.UUID(user["sub"]),
        model_versions=body.model_versions,
    )
    db.add(record)
    await db.flush()

    return SignOffResponse(
        job_id=record.job_id,
        clinician_id=str(record.clinician_id),
        signed_at=record.signed_at.isoformat() if record.signed_at else "",
        model_versions=record.model_versions,
    )
