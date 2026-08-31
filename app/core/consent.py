"""DPD-01 — consent capture and purpose binding.

`require_valid_consent` is a **permissive stub**: it currently logs a
warning and lets every request through rather than enforcing anything. That
is deliberate and temporary, not a design choice — flipping it to actually
block unconsented processing needs:
  1. A consent-capture UI/endpoint that writes ConsentRecord rows (none
     exists yet; the model does — see app/models/consent.py).
  2. Every upload route (reports.py, images.py, claims.py submit handlers)
     to pass a `data_principal_id` and `purpose` through to this dependency.
  3. Deciding what happens today, before (1) exists, to jobs submitted
     with no consent record — this stub currently chooses "allow and log",
     which is itself the thing DPD-01 exists to close. Do not ship this to
     a pilot without turning it into a hard failure.
"""
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consent import ConsentRecord

log = logging.getLogger(__name__)


async def require_valid_consent(
    db: AsyncSession,
    data_principal_id: str,
    purpose: str,
) -> ConsentRecord | None:
    """Look up an active (granted, not withdrawn) consent record.

    TODO(DPD-01): raise HTTPException(403) when no valid record is found,
    once callers actually pass real data_principal_id/purpose values and
    there's a way for a patient to have granted consent in the first place.
    Currently returns None and logs instead of blocking, so existing demo
    flows keep working until the write side (see module docstring) exists.
    """
    result = await db.execute(
        select(ConsentRecord).where(
            ConsentRecord.data_principal_id == data_principal_id,
            ConsentRecord.purpose == purpose,
            ConsentRecord.withdrawn_at.is_(None),
        )
    )
    record = result.scalars().first()

    if record is None:
        log.warning(
            "DPD-01 NOT ENFORCED: no consent record for principal=%s purpose=%s "
            "— processing anyway (stub). See app/core/consent.py.",
            data_principal_id,
            purpose,
        )

    return record


def new_notice_version() -> str:
    """Placeholder version stamp for DPD-02 notice text.

    TODO(DPD-02): replace with the actual versioned notice_texts lookup
    once that table/content exists; for now this is a fixed literal so
    ConsentRecord.notice_version has something non-null to write.
    """
    return "v0-unset"
