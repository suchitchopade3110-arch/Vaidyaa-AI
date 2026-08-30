"""PLT-01 — Organisation hierarchy: Organisation -> Department -> User.

Structural skeleton. Real fields, real table shape — the pieces that don't
exist yet are the service-layer scoping (see app/core/tenancy.py) and the
admin routes to create/manage these records, both left as TODOs there.
"""
import uuid

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class Organisation(Base):
    """A paying customer: one hospital, lab, or TPA."""

    __tablename__ = "organisations"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # TODO(PLT-01): slug for subdomain/branding, billing plan, seat quota —
    # add once PLT-02 (usage metering) and PLT-03 (org branding) land.
    is_active: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    departments: Mapped[list["Department"]] = relationship(
        back_populates="organisation", cascade="all, delete-orphan"
    )


class Department(Base):
    """A sub-unit within an Organisation (e.g. Radiology, Billing)."""

    __tablename__ = "departments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organisations.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    organisation: Mapped["Organisation"] = relationship(back_populates="departments")
