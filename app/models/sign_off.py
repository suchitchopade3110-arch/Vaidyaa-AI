"""REG-02 — mandatory clinician sign-off before a report can leave the system.

Structural skeleton: table + route shape exist (app/api/v1/routes/signoff.py);
the enforcement (blocking PDF export / QR share / "complete" status until a
row exists here) is TODO at each of those call sites.
"""
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class SignOff(Base):
    """One clinician sign-off event for one job's result."""

    __tablename__ = "sign_offs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    job_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    clinician_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    # Freeform version string(s) of whatever produced the result being signed
    # off — e.g. "chexnet:v1;xgb:v3". TODO(REG-04): once the model registry
    # exists, source this from the job result instead of the caller.
    model_versions: Mapped[str] = mapped_column(String(200), nullable=False)
    signed_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
