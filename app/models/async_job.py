"""SEC-01 — persisted job ownership.

Replaces the old in-memory `AsyncJob` dataclass (never wired to anything —
grep confirmed zero references outside this module) with a real table so job
ownership survives past a single Celery result-backend TTL and can actually
be checked. See app/core/ownership.py for the dependency that reads this.

NOT YET WIRED: nothing writes to this table at job-submission time, and no
route enforces ownership through it yet — both are TODO, called out at each
site (app/api/v1/routes/{reports,images,claims,jobs}.py,
app/routes/pdf_reports.py). The model and migration exist first so the
wiring is a small, mechanical change rather than a schema change.
"""
import uuid

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class AsyncJobRecord(Base):
    """One row per submitted pipeline job (report / image / claim)."""

    __tablename__ = "async_jobs"

    # Primary key is the Celery task_id itself — routes already key every
    # lookup off that string, so this avoids a second id to keep in sync.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    pipeline: Mapped[str] = mapped_column(String(20), nullable=False)  # report | image | claim
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued")
    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
