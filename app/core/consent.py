"""DPD-01 — consent capture and purpose binding.

`require_valid_consent` enforces: it raises 403 when there's no active
(granted, not withdrawn) ConsentRecord for the given principal + purpose.
Wired into every upload route (reports.py, images.py, claims.py) whenever
a `patient_id` is provided.

Known, deliberate gap: **submissions with no `patient_id` bypass this
check entirely** — there's nothing to bind consent to. All three upload
routes accept an optional patient_id today (`Form(None)` /
`payload.patient_id`), so anonymous/test uploads still work unconsented.
Whether anonymous uploads should be allowed at all under DPDP is a product
decision, not something to resolve unilaterally here — see
docs/PHASE1_SKELETON.md.
"""
import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consent import ConsentRecord

log = logging.getLogger(__name__)

# Purpose strings shared between the consent-grant endpoint
# (app/api/v1/routes/consent.py) and each upload route's enforcement call.
PURPOSE_REPORT_ANALYSIS = "report_analysis"
PURPOSE_IMAGE_ANALYSIS = "image_analysis"
PURPOSE_CLAIM_VERIFICATION = "claim_verification"


async def _active_consent(db: AsyncSession, data_principal_id: str, purpose: str) -> ConsentRecord | None:
    result = await db.execute(
        select(ConsentRecord).where(
            ConsentRecord.data_principal_id == data_principal_id,
            ConsentRecord.purpose == purpose,
            ConsentRecord.withdrawn_at.is_(None),
        )
    )
    return result.scalars().first()


async def require_valid_consent(
    db: AsyncSession,
    data_principal_id: str,
    purpose: str,
) -> ConsentRecord:
    """Raise 403 unless an active consent record exists for this principal
    and purpose. Returns the record on success."""
    record = await _active_consent(db, data_principal_id, purpose)

    if record is None:
        log.info(
            "DPD-01: blocked — no active consent for principal=%s purpose=%s",
            data_principal_id,
            purpose,
        )
        raise HTTPException(
            status_code=403,
            detail={
                "code": "CONSENT_REQUIRED",
                "message": (
                    f"No active consent on record for this patient and purpose "
                    f"({purpose!r}). Grant consent via POST /api/v1/consent/grant "
                    f"before submitting."
                ),
            },
        )

    return record


async def grant_consent(
    db: AsyncSession,
    data_principal_id: str,
    purpose: str,
) -> ConsentRecord:
    """Record a fresh consent grant. Always inserts a new row rather than
    reactivating a withdrawn one — withdrawal is meant to be a durable,
    auditable event (see withdraw_consent), not something a later grant
    quietly erases."""
    record = ConsentRecord(
        id=uuid.uuid4(),
        data_principal_id=data_principal_id,
        purpose=purpose,
        notice_version=new_notice_version(),
    )
    db.add(record)
    await db.flush()
    return record


async def withdraw_consent(
    db: AsyncSession,
    data_principal_id: str,
    purpose: str,
) -> list[ConsentRecord]:
    """Withdraw every currently-active consent record for this principal +
    purpose (there's normally at most one, but nothing stops re-granting
    while an active one already exists, so this is defensive). Returns the
    records withdrawn; an empty list means there was nothing active to
    withdraw.

    TODO(DPD-03): withdrawal is supposed to also trigger retention/erasure
    for already-processed data tied to this principal — not done here,
    this only stops *future* processing (via require_valid_consent).
    """
    result = await db.execute(
        select(ConsentRecord).where(
            ConsentRecord.data_principal_id == data_principal_id,
            ConsentRecord.purpose == purpose,
            ConsentRecord.withdrawn_at.is_(None),
        )
    )
    records = list(result.scalars().all())
    now = datetime.now(timezone.utc)
    for record in records:
        record.withdrawn_at = now

    return records


def new_notice_version() -> str:
    """Placeholder version stamp for DPD-02 notice text.

    TODO(DPD-02): replace with the actual versioned notice_texts lookup
    once that table/content exists; for now this is a fixed literal so
    ConsentRecord.notice_version has something non-null to write.
    """
    return "v0-unset"
