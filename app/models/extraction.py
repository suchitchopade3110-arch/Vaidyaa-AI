"""ORM models for the P2 review flow (plans/P2.md §3; PRD E1 FR-1.2, E7, S1, S2, S9).

- ``ReportExtraction``: one row per report job; job metadata, review revision and the S1 confirmation stamp.
- ``ExtractedRowRecord``: one extracted row or unread line, with provenance and review state.
- ``ExtractionEdit``: the S9 audit trail; append-only (no code path updates or deletes an edit).
- ``SignOffDuplicateArchive``: duplicate ``sign_offs`` rows removed by migration ``c4f1e2d3b5a6`` so that
  ``sign_offs.job_id`` could become unique; its downgrade puts them back.

The schema is created by ``alembic/versions/c4f1e2d3b5a6_p2_review_flow.py``. That migration repeats the literal
vocabularies below on purpose (a migration is a frozen snapshot and never imports app code);
``tests/test_review_migration.py`` (MG-05, MG-06) proves the two agree.

JSON columns use ``none_as_null=True``: Python ``None`` is stored as SQL NULL, never as the JSON literal ``null``,
so ``IS NULL`` means what it says and a NOT NULL JSON column rejects ``None``.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

# ── Vocabularies (plans/P2.md §3.1–3.3) ─────────────────────────────────────────────────────────────
FILE_KINDS = ("pdf", "image", "csv")
SEXES = ("male", "female")
ROW_KINDS = ("row", "unread_line")
QUALIFIERS = ("<", ">", "<=", ">=")
PRINTED_FLAGS = ("H", "L", "HH", "LL", "ABN")
# "serum" means any blood specimen (P1 decision).
SPECIMENS = ("serum", "urine", "csf", "fluid", "other", "unknown")
# "legacy_urine" is the documented addition for the N12 legacy urine reader.
ORIGINS = ("text", "ocr", "column", "csv", "fhir", "hl7", "manual", "legacy_urine")
ROW_STATUSES = ("pending", "accepted", "edited", "deleted", "added")
EDIT_FIELDS = (
    "name_raw", "value_raw", "unit_raw", "ref_low", "ref_high", "ref_text", "printed_flag", "specimen", "status",
    "kind",
)

EXTRACTOR_VERSION = "p2.1"
AGE_MIN, AGE_MAX = 0, 130

_EMPTY_JSON_ARRAY = text("'[]'::json")


def _in(column: str, values: tuple[str, ...]) -> str:
    """SQL ``column IN ('a', 'b')`` for a fixed vocabulary (literals contain no quotes)."""
    assert all("'" not in v for v in values)
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ReportExtraction(Base):
    """One row per report job (plans/P2.md §3.1). Absent for pre-P2 (legacy) report jobs (§5.4)."""

    __tablename__ = "report_extractions"
    __table_args__ = (
        CheckConstraint(_in("file_kind", FILE_KINDS), name="file_kind"),
        CheckConstraint(_in("sex", SEXES), name="sex"),
        CheckConstraint(f"age >= {AGE_MIN} AND age <= {AGE_MAX}", name="age"),
        # The S1 stamp is all or nothing: confirm sets both, reopen and a failed dispatch clear both.
        CheckConstraint("(confirmed_at IS NULL) = (confirmed_by IS NULL)", name="confirmation_stamp"),
    )

    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("async_jobs.id", ondelete="CASCADE"), primary_key=True
    )
    # Server path of the upload; never returned by any API.
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_kind: Mapped[str] = mapped_column(String(8), nullable=False)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    # [{n, width, height, unit: "pt"|"px", method: "text"|"ocr", rotation}]
    pages: Mapped[list] = mapped_column(
        JSON(none_as_null=True), nullable=False, default=list, server_default=_EMPTY_JSON_ARRAY
    )
    warnings: Mapped[list] = mapped_column(
        JSON(none_as_null=True), nullable=False, default=list, server_default=_EMPTY_JSON_ARRAY
    )
    notes: Mapped[list] = mapped_column(
        JSON(none_as_null=True), nullable=False, default=list, server_default=_EMPTY_JSON_ARRAY
    )
    ocr_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    patient_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # As entered at upload; never assumed (S4).
    sex: Mapped[str | None] = mapped_column(String(8), nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    explanation_mode: Mapped[str] = mapped_column(
        String(8), nullable=False, default="brief", server_default=text("'brief'")
    )
    extractor_version: Mapped[str] = mapped_column(String(32), nullable=False, default=EXTRACTOR_VERSION)
    # +1 on every row change; confirm must quote it.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ExtractedRowRecord(Base):
    """One extracted row or unread line (plans/P2.md §3.2; FR-1.2 plus the marked additions)."""

    __tablename__ = "extracted_rows"
    __table_args__ = (
        UniqueConstraint("job_id", "row_index", name="uq_extracted_rows_job_id_row_index"),
        CheckConstraint(_in("kind", ROW_KINDS), name="kind"),
        CheckConstraint(_in("qualifier", QUALIFIERS), name="qualifier"),
        CheckConstraint(_in("printed_flag", PRINTED_FLAGS), name="printed_flag"),
        CheckConstraint(_in("specimen", SPECIMENS), name="specimen"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence"),
        CheckConstraint(_in("origin", ORIGINS), name="origin"),
        CheckConstraint(_in("status", ROW_STATUSES), name="status"),
        # S2: a review row always says why, in words; a certain row carries no reason (§3.2 "NULL iff").
        CheckConstraint(
            "CASE WHEN needs_review THEN coalesce(btrim(review_reason), '') <> '' "
            "ELSE review_reason IS NULL END",
            name="review_reason",
        ),
        # S2 / RR-25: needs_review == bool(review_codes); review_codes is a JSON array.
        CheckConstraint(
            "CASE WHEN json_typeof(review_codes) = 'array' "
            "THEN (json_array_length(review_codes) > 0) = needs_review ELSE false END",
            name="review_codes",
        ),
        # FR-1.3: an unread line is always a review item (NR-UNREAD); it is never accepted silently.
        CheckConstraint("kind <> 'unread_line' OR needs_review", name="unread_line_review"),
        # §3.2: parsed_payload is NULL only for unread lines; a scorable row always carries one.
        CheckConstraint("kind = 'unread_line' OR parsed_payload IS NOT NULL", name="parsed_payload"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("async_jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Document order; manual rows append (max + 1).
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(12), nullable=False)
    name_raw: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default=text("''"))
    # Engine identity after the S5 floor; recomputed server-side, never client-settable.
    analyte_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    qualifier: Mapped[str | None] = mapped_column(String(4), nullable=True)
    text_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_raw: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Effective printed range for this patient, in the result unit.
    ref_low: Mapped[float | None] = mapped_column(Float, nullable=True)
    ref_high: Mapped[float | None] = mapped_column(Float, nullable=True)
    ref_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    printed_flag: Mapped[str | None] = mapped_column(String(8), nullable=True)
    specimen: Mapped[str] = mapped_column(
        String(8), nullable=False, default="unknown", server_default=text("'unknown'")
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # Immutable after extraction: the engine's uncertainty record.
    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_codes: Mapped[list] = mapped_column(
        JSON(none_as_null=True), nullable=False, default=list, server_default=_EMPTY_JSON_ARRAY
    )
    # Internal only (S1): never returned by the review API (plans/P2.md §5.3).
    prelim_flag: Mapped[str | None] = mapped_column(String(16), nullable=True)
    source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # [x0, y0, x1, y1] in the units of report_extractions.pages[n].unit; top-left origin; displayed page.
    source_bbox: Mapped[list | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    origin: Mapped[str] = mapped_column(String(12), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default="pending", server_default=text("'pending'")
    )
    # Serialised ParsedRow (plans/P2.md §7.2).
    parsed_payload: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)
    # Optimistic lock; PATCH must quote it.
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ExtractionEdit(Base):
    """One audited field change (S9; plans/P2.md §3.3). Append-only: never updated or deleted by app code.

    Values are JSON-encoded scalars (``"6.9"``, ``null``).
    """

    __tablename__ = "extraction_edits"
    __table_args__ = (CheckConstraint(_in("field", EDIT_FIELDS), name="field"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    row_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("extracted_rows.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[str] = mapped_column(String(64), ForeignKey("async_jobs.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


class SignOffDuplicateArchive(Base):
    """A duplicate sign-off removed by migration c4f1e2d3b5a6 (the earliest per job was kept; PRD FR-1.2).

    Columns mirror ``sign_offs`` exactly (``id`` is the original sign-off id) so the downgrade restores the rows
    unchanged. No foreign keys: this is a passive copy.
    """

    __tablename__ = "sign_off_duplicates_archive"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), nullable=False)
    clinician_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    model_versions: Mapped[str] = mapped_column(String(200), nullable=False)
    signed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
