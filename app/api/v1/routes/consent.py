"""DPD-01 — consent capture endpoints.

POST /api/v1/consent/grant     — record a consent grant for a data principal + purpose
POST /api/v1/consent/withdraw  — withdraw it (blocks future require_valid_consent calls)

There is no patient-facing auth in this system (see app/core/consent.py's
module docstring on the data-principal-vs-clinician distinction) — these
are called by an authenticated clinician/staff user recording that consent
was obtained from the patient at intake, not by the patient directly.
That's a real limitation for DPDP purposes (the Act expects the data
principal's own action to be the consent event, not a staff attestation
of it) — flagged here rather than presented as solved.
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.consent import grant_consent, withdraw_consent
from app.db.session import get_db

router = APIRouter()


class ConsentRequest(BaseModel):
    data_principal_id: str
    purpose: str


class ConsentRecordResponse(BaseModel):
    id: str
    data_principal_id: str
    purpose: str
    notice_version: str
    granted_at: str
    withdrawn_at: str | None = None


@router.post("/grant", response_model=ConsentRecordResponse, status_code=201)
async def grant(
    body: ConsentRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    record = await grant_consent(db, body.data_principal_id, body.purpose)
    return ConsentRecordResponse(
        id=str(record.id),
        data_principal_id=record.data_principal_id,
        purpose=record.purpose,
        notice_version=record.notice_version,
        granted_at=record.granted_at.isoformat() if record.granted_at else "",
        withdrawn_at=None,
    )


@router.post("/withdraw", response_model=list[ConsentRecordResponse])
async def withdraw(
    body: ConsentRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Withdraws every currently-active consent record for this principal +
    purpose. Returns an empty list if none were active — not an error."""
    records = await withdraw_consent(db, body.data_principal_id, body.purpose)
    return [
        ConsentRecordResponse(
            id=str(r.id),
            data_principal_id=r.data_principal_id,
            purpose=r.purpose,
            notice_version=r.notice_version,
            granted_at=r.granted_at.isoformat() if r.granted_at else "",
            withdrawn_at=r.withdrawn_at.isoformat() if r.withdrawn_at else None,
        )
        for r in records
    ]
