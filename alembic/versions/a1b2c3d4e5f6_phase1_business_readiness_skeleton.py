"""Phase 1 business-readiness skeleton: organisations/departments,
async_jobs, consent_records, sign_offs

Structural migration for the Phase 1 items in the business-readiness
requirements doc (SEC-01, DPD-01, REG-02, PLT-01). Tables and columns only
— the application code that populates and enforces them is a mix of "wired"
(see app/core/ownership.py, app/core/tenancy.py, app/core/consent.py) and
still-TODO; see docs/PHASE1_SKELETON.md for the current state of each.

Revision ID: a1b2c3d4e5f6
Revises: 9c3d4e5f6a7b
Create Date: 2026-08-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "a1b2c3d4e5f6"
down_revision = "9c3d4e5f6a7b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── PLT-01: organisation hierarchy ──────────────────────────────────
    op.create_table(
        "organisations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_organisations")),
    )

    op.create_table(
        "departments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["org_id"], ["organisations.id"], name=op.f("fk_departments_org_id_organisations")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_departments")),
    )
    op.create_index(op.f("ix_departments_org_id"), "departments", ["org_id"], unique=False)

    op.add_column("users", sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("users", sa.Column("department_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_users_org_id_organisations"), "users", "organisations", ["org_id"], ["id"]
    )
    op.create_foreign_key(
        op.f("fk_users_department_id_departments"), "users", "departments", ["department_id"], ["id"]
    )
    op.create_index(op.f("ix_users_org_id"), "users", ["org_id"], unique=False)
    op.create_index(op.f("ix_users_department_id"), "users", ["department_id"], unique=False)

    # ── SEC-01: persisted job ownership ─────────────────────────────────
    op.create_table(
        "async_jobs",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pipeline", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="queued"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_async_jobs")),
    )
    op.create_index(op.f("ix_async_jobs_user_id"), "async_jobs", ["user_id"], unique=False)
    op.create_index(op.f("ix_async_jobs_org_id"), "async_jobs", ["org_id"], unique=False)
    op.create_index(op.f("ix_async_jobs_created_at"), "async_jobs", ["created_at"], unique=False)

    # ── DPD-01: consent capture ─────────────────────────────────────────
    op.create_table(
        "consent_records",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("data_principal_id", sa.String(length=100), nullable=False),
        sa.Column("purpose", sa.String(length=200), nullable=False),
        sa.Column("notice_version", sa.String(length=20), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("withdrawn_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consent_records")),
    )
    op.create_index(
        op.f("ix_consent_records_data_principal_id"), "consent_records", ["data_principal_id"], unique=False
    )

    # ── REG-02: clinician sign-off ──────────────────────────────────────
    op.create_table(
        "sign_offs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("clinician_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_versions", sa.String(length=200), nullable=False),
        sa.Column("signed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["clinician_id"], ["users.id"], name=op.f("fk_sign_offs_clinician_id_users")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sign_offs")),
    )
    op.create_index(op.f("ix_sign_offs_job_id"), "sign_offs", ["job_id"], unique=False)
    op.create_index(op.f("ix_sign_offs_clinician_id"), "sign_offs", ["clinician_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_sign_offs_clinician_id"), table_name="sign_offs")
    op.drop_index(op.f("ix_sign_offs_job_id"), table_name="sign_offs")
    op.drop_table("sign_offs")

    op.drop_index(op.f("ix_consent_records_data_principal_id"), table_name="consent_records")
    op.drop_table("consent_records")

    op.drop_index(op.f("ix_async_jobs_created_at"), table_name="async_jobs")
    op.drop_index(op.f("ix_async_jobs_org_id"), table_name="async_jobs")
    op.drop_index(op.f("ix_async_jobs_user_id"), table_name="async_jobs")
    op.drop_table("async_jobs")

    op.drop_index(op.f("ix_users_department_id"), table_name="users")
    op.drop_index(op.f("ix_users_org_id"), table_name="users")
    op.drop_constraint(op.f("fk_users_department_id_departments"), "users", type_="foreignkey")
    op.drop_constraint(op.f("fk_users_org_id_organisations"), "users", type_="foreignkey")
    op.drop_column("users", "department_id")
    op.drop_column("users", "org_id")

    op.drop_index(op.f("ix_departments_org_id"), table_name="departments")
    op.drop_table("departments")

    op.drop_table("organisations")
