"""P2 review flow: report_extractions, extracted_rows, extraction_edits; one sign-off per job

plans/P2.md §3.4 (PRD E1 FR-1.2, FR-9.1, S9). Single head; reversible; lossless for existing data.

Upgrade:
  1. create report_extractions, extracted_rows, extraction_edits (constraint names follow
     app/db/base_class.py's naming convention, spelled out with op.f);
  2. create sign_off_duplicates_archive;
  3. archive duplicate sign-offs, keeping the EARLIEST per job (ties: smallest id), then delete the archived rows
     from sign_offs. sign_offs is locked against writes first, so no duplicate can slip in before step 4;
  4. replace ix_sign_offs_job_id with the unique constraint uq_sign_offs_job_id;
  5. no backfill: pre-P2 report jobs have no report_extractions row (legacy-unconfirmed, plan §5.4).

Downgrade:
  1. refuse (nothing changes) while any review table holds a row, unless VAIDYA_ALLOW_LOSSY_DOWNGRADE=1;
  2. drop uq_sign_offs_job_id, recreate ix_sign_offs_job_id, re-insert the archived sign-offs unchanged and drop
     the archive, so the pre-upgrade sign_offs rows are back exactly;
  3. drop the three review tables.

The vocabularies below are deliberately literal (a migration never imports app code); tests/test_review_migration.py
(MG-05, MG-06) proves they agree with app/models/extraction.py.

Revision ID: c4f1e2d3b5a6
Revises: a1b2c3d4e5f6
Create Date: 2026-11-22 00:00:00.000000

"""
import os

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "c4f1e2d3b5a6"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None

LOSSY_DOWNGRADE_ENV = "VAIDYA_ALLOW_LOSSY_DOWNGRADE"
REVIEW_TABLES = ("report_extractions", "extracted_rows", "extraction_edits")

_EMPTY_JSON_ARRAY = sa.text("'[]'::json")


def _in(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _timestamps():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    ]


def upgrade() -> None:
    # ── 1. Review tables ────────────────────────────────────────────────
    op.create_table(
        "report_extractions",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_kind", sa.String(length=8), nullable=False),
        sa.Column("page_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pages", sa.JSON(), server_default=_EMPTY_JSON_ARRAY, nullable=False),
        sa.Column("warnings", sa.JSON(), server_default=_EMPTY_JSON_ARRAY, nullable=False),
        sa.Column("notes", sa.JSON(), server_default=_EMPTY_JSON_ARRAY, nullable=False),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("patient_id", sa.String(length=64), nullable=True),
        sa.Column("sex", sa.String(length=8), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("explanation_mode", sa.String(length=8), server_default=sa.text("'brief'"), nullable=False),
        sa.Column("extractor_version", sa.String(length=32), nullable=False),
        sa.Column("revision", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("confirmed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(_in("file_kind", ("pdf", "image", "csv")), name=op.f("ck_report_extractions_file_kind")),
        sa.CheckConstraint(_in("sex", ("male", "female")), name=op.f("ck_report_extractions_sex")),
        sa.CheckConstraint("age >= 0 AND age <= 130", name=op.f("ck_report_extractions_age")),
        sa.CheckConstraint(
            "(confirmed_at IS NULL) = (confirmed_by IS NULL)", name=op.f("ck_report_extractions_confirmation_stamp")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["async_jobs.id"], name=op.f("fk_report_extractions_job_id_async_jobs"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["confirmed_by"], ["users.id"], name=op.f("fk_report_extractions_confirmed_by_users")),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_report_extractions")),
    )

    op.create_table(
        "extracted_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("name_raw", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("analyte_key", sa.String(length=64), nullable=True),
        sa.Column("value_raw", sa.Text(), nullable=True),
        sa.Column("value_num", sa.Float(), nullable=True),
        sa.Column("qualifier", sa.String(length=4), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("unit_raw", sa.String(length=64), nullable=True),
        sa.Column("ref_low", sa.Float(), nullable=True),
        sa.Column("ref_high", sa.Float(), nullable=True),
        sa.Column("ref_text", sa.Text(), nullable=True),
        sa.Column("printed_flag", sa.String(length=8), nullable=True),
        sa.Column("specimen", sa.String(length=8), server_default=sa.text("'unknown'"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("needs_review", sa.Boolean(), nullable=False),
        sa.Column("review_reason", sa.Text(), nullable=True),
        sa.Column("review_codes", sa.JSON(), server_default=_EMPTY_JSON_ARRAY, nullable=False),
        sa.Column("prelim_flag", sa.String(length=16), nullable=True),
        sa.Column("source_page", sa.Integer(), nullable=True),
        sa.Column("source_bbox", sa.JSON(), nullable=True),
        sa.Column("source_text", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=12), nullable=False),
        sa.Column("status", sa.String(length=10), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("parsed_payload", sa.JSON(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(_in("kind", ("row", "unread_line")), name=op.f("ck_extracted_rows_kind")),
        sa.CheckConstraint(_in("qualifier", ("<", ">", "<=", ">=")), name=op.f("ck_extracted_rows_qualifier")),
        sa.CheckConstraint(
            _in("printed_flag", ("H", "L", "HH", "LL", "ABN")), name=op.f("ck_extracted_rows_printed_flag")
        ),
        sa.CheckConstraint(
            _in("specimen", ("serum", "urine", "csf", "fluid", "other", "unknown")),
            name=op.f("ck_extracted_rows_specimen"),
        ),
        sa.CheckConstraint("confidence >= 0 AND confidence <= 1", name=op.f("ck_extracted_rows_confidence")),
        sa.CheckConstraint(
            _in("origin", ("text", "ocr", "column", "csv", "fhir", "hl7", "manual", "legacy_urine")),
            name=op.f("ck_extracted_rows_origin"),
        ),
        sa.CheckConstraint(
            _in("status", ("pending", "accepted", "edited", "deleted", "added")), name=op.f("ck_extracted_rows_status")
        ),
        sa.CheckConstraint(
            "CASE WHEN needs_review THEN coalesce(btrim(review_reason), '') <> '' "
            "ELSE review_reason IS NULL END",
            name=op.f("ck_extracted_rows_review_reason"),
        ),
        sa.CheckConstraint(
            "CASE WHEN json_typeof(review_codes) = 'array' "
            "THEN (json_array_length(review_codes) > 0) = needs_review ELSE false END",
            name=op.f("ck_extracted_rows_review_codes"),
        ),
        sa.CheckConstraint("kind <> 'unread_line' OR needs_review", name=op.f("ck_extracted_rows_unread_line_review")),
        sa.CheckConstraint(
            "kind = 'unread_line' OR parsed_payload IS NOT NULL", name=op.f("ck_extracted_rows_parsed_payload")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["async_jobs.id"], name=op.f("fk_extracted_rows_job_id_async_jobs"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extracted_rows")),
        sa.UniqueConstraint("job_id", "row_index", name=op.f("uq_extracted_rows_job_id_row_index")),
    )
    op.create_index(op.f("ix_extracted_rows_job_id"), "extracted_rows", ["job_id"], unique=False)

    op.create_table(
        "extraction_edits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("row_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("field", sa.String(length=32), nullable=False),
        sa.Column("old_value", sa.Text(), nullable=True),
        sa.Column("new_value", sa.Text(), nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            _in(
                "field",
                (
                    "name_raw", "value_raw", "unit_raw", "ref_low", "ref_high", "ref_text", "printed_flag",
                    "specimen", "status", "kind",
                ),
            ),
            name=op.f("ck_extraction_edits_field"),
        ),
        sa.ForeignKeyConstraint(
            ["row_id"], ["extracted_rows.id"], name=op.f("fk_extraction_edits_row_id_extracted_rows"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["async_jobs.id"], name=op.f("fk_extraction_edits_job_id_async_jobs")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_extraction_edits_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction_edits")),
    )
    op.create_index(op.f("ix_extraction_edits_row_id"), "extraction_edits", ["row_id"], unique=False)
    op.create_index(op.f("ix_extraction_edits_job_id"), "extraction_edits", ["job_id"], unique=False)

    # ── 2. Archive for duplicate sign-offs (columns mirror sign_offs) ───
    op.create_table(
        "sign_off_duplicates_archive",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("clinician_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_versions", sa.String(length=200), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sign_off_duplicates_archive")),
    )

    # ── 3. De-duplicate sign_offs, keeping the earliest per job ─────────
    # Writers wait until this transaction commits, so no duplicate can be inserted between the archive step and
    # the unique constraint (readers are not blocked).
    op.execute("LOCK TABLE sign_offs IN SHARE ROW EXCLUSIVE MODE")
    op.execute(
        "INSERT INTO sign_off_duplicates_archive (id, job_id, clinician_id, model_versions, signed_at) "
        "SELECT id, job_id, clinician_id, model_versions, signed_at FROM sign_offs "
        "WHERE id NOT IN ("
        "SELECT DISTINCT ON (job_id) id FROM sign_offs ORDER BY job_id, signed_at ASC NULLS LAST, id ASC)"
    )
    op.execute("DELETE FROM sign_offs WHERE id IN (SELECT id FROM sign_off_duplicates_archive)")

    # ── 4. One sign-off per job ─────────────────────────────────────────
    op.drop_index(op.f("ix_sign_offs_job_id"), table_name="sign_offs")
    op.create_unique_constraint(op.f("uq_sign_offs_job_id"), "sign_offs", ["job_id"])


def _refuse_lossy_downgrade() -> None:
    """Refuse to drop review data unless VAIDYA_ALLOW_LOSSY_DOWNGRADE=1 (plans/P2.md §0 rule 6, §3.4)."""
    if os.environ.get(LOSSY_DOWNGRADE_ENV) == "1":
        return
    message = (
        "Refusing to downgrade c4f1e2d3b5a6 (P2 review flow): review data would be lost (not empty: {tables}). "
        "The downgrade drops the review tables and the S9 edit audit trail with them. Back up the database, then "
        f"re-run with {LOSSY_DOWNGRADE_ENV}=1 to accept the loss. Nothing was changed."
    )
    # Writers wait from here until the transaction ends, so no row can arrive between the check and the drop.
    op.execute(f"LOCK TABLE {', '.join(REVIEW_TABLES)} IN SHARE ROW EXCLUSIVE MODE")
    if context.is_offline_mode():
        # `alembic downgrade --sql`: there is no connection to look at, so the generated script checks for itself.
        condition = " OR ".join(f"EXISTS (SELECT 1 FROM {t})" for t in REVIEW_TABLES)
        sql_message = message.format(tables="(" + ", ".join(REVIEW_TABLES) + ")").replace("'", "''")
        op.execute(f"DO $$ BEGIN IF {condition} THEN RAISE EXCEPTION '{sql_message}'; END IF; END $$")
        return
    bind = op.get_bind()
    holding = [t for t in REVIEW_TABLES if bind.execute(sa.text(f"SELECT EXISTS (SELECT 1 FROM {t})")).scalar()]
    if holding:
        raise RuntimeError(message.format(tables=", ".join(holding)))


def downgrade() -> None:
    # ── 1. Guard (first, so a refusal changes nothing) ──────────────────
    _refuse_lossy_downgrade()

    # ── 2. Restore sign_offs exactly ────────────────────────────────────
    op.drop_constraint(op.f("uq_sign_offs_job_id"), "sign_offs", type_="unique")
    op.create_index(op.f("ix_sign_offs_job_id"), "sign_offs", ["job_id"], unique=False)
    op.execute(
        "INSERT INTO sign_offs (id, job_id, clinician_id, model_versions, signed_at) "
        "SELECT id, job_id, clinician_id, model_versions, signed_at FROM sign_off_duplicates_archive"
    )
    op.drop_table("sign_off_duplicates_archive")

    # ── 3. Drop the review tables ───────────────────────────────────────
    op.drop_index(op.f("ix_extraction_edits_job_id"), table_name="extraction_edits")
    op.drop_index(op.f("ix_extraction_edits_row_id"), table_name="extraction_edits")
    op.drop_table("extraction_edits")
    op.drop_index(op.f("ix_extracted_rows_job_id"), table_name="extracted_rows")
    op.drop_table("extracted_rows")
    op.drop_table("report_extractions")
