"""DPD-01 — consent capture and purpose binding (DPDP Act readiness).

Structural skeleton: the table exists so the shape is settled, but nothing
writes to it yet and app.core.consent.require_valid_consent is a
permissive stub. Wiring both in is a Phase-1-continuation task, not done
here — see the TODOs in app/core/consent.py.
"""
import uuid

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ConsentRecord(Base):
    """One consent grant (or withdrawal) for a data principal."""

    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # The patient/data principal, not the submitting clinician — identified
    # by patient_id today; TODO(INT-02) swap to ABHA number once INT-02 lands.
    data_principal_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(200), nullable=False)
    # TODO(DPD-02): FK to a versioned notice_texts table once that exists;
    # for now this just records which version string was shown.
    notice_version: Mapped[str] = mapped_column(String(20), nullable=False)
    granted_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    withdrawn_at: Mapped[DateTime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
