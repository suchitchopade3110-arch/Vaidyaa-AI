"""Migration tests MG-01…MG-06 for ``c4f1e2d3b5a6_p2_review_flow`` (plans/P2.md §3.4, §12.3; PRD FR-1.2, FR-9.1).

Every database test runs on its own SCRATCH database (``CREATE DATABASE … TEMPLATE template0`` on the configured
server, dropped afterwards); the dev database's data is never read or written. Without a reachable server or the
CREATEDB privilege the tests skip, unless ``REQUIRE_DB_TESTS=1`` (then they fail).

Alembic runs in-process from a ``Config`` with no ini file, so ``alembic/env.py`` leaves the test process's logging
alone, and always with ``-x db_url=<scratch>`` so it can never fall back to the dev database.

Expected values are hand-derived from the plan (named in each test), never read from code output.
"""
from __future__ import annotations

import argparse
import io
import math
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import CheckConstraint, create_engine, text
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.config import settings
from app.db.base import Base
from app.models.extraction import ExtractedRowRecord, ReportExtraction

ROOT = Path(__file__).resolve().parents[1]
P2 = "c4f1e2d3b5a6"
PRE_P2 = "a1b2c3d4e5f6"
LOSSY_ENV = "VAIDYA_ALLOW_LOSSY_DOWNGRADE"
REVIEW_TABLES = ("report_extractions", "extracted_rows", "extraction_edits")
P2_TABLES = (*REVIEW_TABLES, "sign_off_duplicates_archive")
# Offline (--sql) runs never connect; the URL only selects the dialect.
NEVER_CONNECTED = make_url("postgresql+psycopg2://scratch@localhost/never_connected")


# ── scratch database + alembic plumbing ─────────────────────────────────────────────────────────────


def _unavailable(reason: str):
    if os.getenv("REQUIRE_DB_TESTS") == "1":
        pytest.fail(f"REQUIRE_DB_TESTS=1 but a scratch database cannot be created: {reason}")
    pytest.skip(f"scratch database unavailable: {reason}")


@pytest.fixture
def scratch_url():
    """A fresh, empty database on the configured server; dropped after the test."""
    server = make_url(settings.SYNC_DATABASE_URL).set(drivername="postgresql+psycopg2")
    name = f"vaidya_mg_{uuid.uuid4().hex[:12]}"
    admin: Engine | None = None
    last_error = "no maintenance database reachable"
    for maintenance_db in ("postgres", server.database):
        engine = create_engine(server.set(database=maintenance_db), isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            with engine.connect() as conn:
                conn.execute(text(f'CREATE DATABASE "{name}" TEMPLATE template0'))
        except ProgrammingError as exc:  # InsufficientPrivilege: the role cannot CREATE DATABASE
            engine.dispose()
            _unavailable(f"CREATE DATABASE refused ({type(exc.orig).__name__})")
        except OperationalError as exc:  # server down, or this maintenance database does not exist
            engine.dispose()
            last_error = type(exc.orig).__name__
            continue
        admin = engine
        break
    if admin is None:
        _unavailable(last_error)
    try:
        yield server.set(database=name)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _cfg(url: URL, buffer: io.StringIO | None = None) -> Config:
    cfg = Config(output_buffer=buffer) if buffer is not None else Config()
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.cmd_opts = argparse.Namespace(x=[f"db_url={url.render_as_string(hide_password=False)}"])
    return cfg


def _engine(url: URL) -> Engine:
    return create_engine(url, poolclass=NullPool)


def _version(engine: Engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()


def _tables(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        return set(conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")).scalars())


_SNAPSHOT_SQL = {
    "columns": (
        "SELECT table_name, column_name, data_type, character_maximum_length, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public'"
    ),
    "constraints": (
        "SELECT conrelid::regclass::text, conname, contype, pg_get_constraintdef(oid) "
        "FROM pg_constraint WHERE connamespace = 'public'::regnamespace"
    ),
    "indexes": "SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'",
}


def _snapshot(engine: Engine) -> dict[str, list[tuple[str, ...]]]:
    """Every public table's columns, constraints and indexes, as sortable text."""
    with engine.connect() as conn:
        return {
            key: sorted(tuple("" if v is None else str(v) for v in row) for row in conn.execute(text(sql)))
            for key, sql in _SNAPSHOT_SQL.items()
        }


def _index(engine: Engine, name: str) -> tuple[str, bool] | None:
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT t.relname, ix.indisunique FROM pg_index ix JOIN pg_class i ON i.oid = ix.indexrelid "
                "JOIN pg_class t ON t.oid = ix.indrelid WHERE i.relname = :n"
            ),
            {"n": name},
        ).first()
    return None if row is None else (row[0], row[1])


# ── seed data (written at a1b2c3d4e5f6, i.e. before P2) ─────────────────────────────────────────────

CLINICIAN = uuid.UUID("11111111-1111-1111-1111-111111111111")
T1 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)
T2 = T1 + timedelta(hours=1)
T3 = T1 + timedelta(hours=2)


def _sid(suffix: str) -> uuid.UUID:
    return uuid.UUID(f"00000000-0000-0000-0000-{suffix:0>12}")


# (id, job_id, clinician_id, model_versions, signed_at), in insertion order. The ids and the insertion order are
# chosen so that "keep the smallest id" and "keep the first inserted" both keep the WRONG J1 row, and
# "keep the first inserted" also keeps the wrong J3 row.
S_J1_SECOND = (_sid("a1"), "J1", CLINICIAN, "mv-a", T2)
S_J1_EARLIEST = (_sid("b2"), "J1", CLINICIAN, "mv-b", T1)
S_J1_LATEST = (_sid("c3"), "J1", CLINICIAN, "mv-c", T3)
S_J2_ONLY = (_sid("d4"), "J2", CLINICIAN, "mv-d", T1)
S_J3_TIE_LARGER_ID = (_sid("0f"), "J3", CLINICIAN, "mv-f", T1)
S_J3_TIE_SMALLER_ID = (_sid("0e"), "J3", CLINICIAN, "mv-e", T1)
SEED = [S_J1_SECOND, S_J1_EARLIEST, S_J1_LATEST, S_J2_ONLY, S_J3_TIE_LARGER_ID, S_J3_TIE_SMALLER_ID]
# PRD FR-1.2 / plan §3.4: keep the earliest per job; equal signed_at → the smaller id (ORDER BY signed_at, id).
KEPT = {S_J1_EARLIEST, S_J2_ONLY, S_J3_TIE_SMALLER_ID}
ARCHIVED = {S_J1_SECOND, S_J1_LATEST, S_J3_TIE_LARGER_ID}


def _insert_user(conn, user_id: uuid.UUID = CLINICIAN, username: str = "mg-clinician") -> None:
    conn.execute(
        text("INSERT INTO users (id, username, hashed_password, is_active) VALUES (:id, :u, 'not-a-hash', true)"),
        {"id": user_id, "u": username},
    )


def _insert_sign_off(conn, row: tuple) -> None:
    conn.execute(
        text(
            "INSERT INTO sign_offs (id, job_id, clinician_id, model_versions, signed_at) "
            "VALUES (:id, :job, :clin, :mv, :at)"
        ),
        dict(zip(("id", "job", "clin", "mv", "at"), row)),
    )


def _seed_pre_p2(engine: Engine) -> None:
    with engine.begin() as conn:
        _insert_user(conn)
        for row in SEED:
            _insert_sign_off(conn, row)


def _rows(engine: Engine, table: str) -> set[tuple]:
    with engine.connect() as conn:
        return {
            tuple(r)
            for r in conn.execute(text(f"SELECT id, job_id, clinician_id, model_versions, signed_at FROM {table}"))
        }


def _insert_job(conn, job_id: str, owner: uuid.UUID = CLINICIAN) -> None:
    conn.execute(
        text("INSERT INTO async_jobs (id, user_id, org_id, pipeline, status) VALUES (:id, :u, :u, 'report', 'queued')"),
        {"id": job_id, "u": owner},
    )


def _insert_extraction(conn, job_id: str) -> None:
    conn.execute(
        text(
            "INSERT INTO report_extractions (job_id, file_path, file_kind, extractor_version) "
            "VALUES (:j, '/srv/uploads/scratch.pdf', 'pdf', 'p2.1')"
        ),
        {"j": job_id},
    )


def _insert_row(conn, job_id: str, row_index: int = 0) -> uuid.UUID:
    """A certain, valid row: Serum Potassium 4.2 mmol/L 3.5 - 5.1 (RR-01: needs_review false)."""
    row_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO extracted_rows (id, job_id, row_index, kind, name_raw, value_raw, value_num, unit_raw, "
            "ref_low, ref_high, ref_text, specimen, confidence, needs_review, origin, parsed_payload) "
            "VALUES (:id, :j, :i, 'row', 'Serum Potassium', '4.2', 4.2, 'mmol/L', 3.5, 5.1, '3.5 - 5.1', 'serum', "
            "1.0, false, 'text', CAST('{}' AS json))"
        ),
        {"id": row_id, "j": job_id, "i": row_index},
    )
    return row_id


def _insert_edit(conn, row_id: uuid.UUID, job_id: str, user_id: uuid.UUID = CLINICIAN) -> uuid.UUID:
    edit_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO extraction_edits (id, row_id, job_id, user_id, field, old_value, new_value) "
            "VALUES (:id, :r, :j, :u, 'value_raw', '\"4.2\"', '\"6.9\"')"
        ),
        {"id": edit_id, "r": row_id, "j": job_id, "u": user_id},
    )
    return edit_id


def _count(conn, table: str) -> int:
    return conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()


# ── MG-01 ───────────────────────────────────────────────────────────────────────────────────────────


def test_mg01_one_alembic_head_and_p2_sits_on_the_single_line():
    script = ScriptDirectory.from_config(_cfg(NEVER_CONNECTED))
    assert len(script.get_heads()) == 1, script.get_heads()
    assert len(script.get_bases()) == 1, script.get_bases()
    assert script.get_revision(P2).down_revision == PRE_P2
    # walk_revisions goes head → base; P2 is on the one line and directly above the phase-1 skeleton.
    line = [r.revision for r in script.walk_revisions("base", "heads")]
    assert line.index(P2) + 1 == line.index(PRE_P2)


# ── MG-02 ───────────────────────────────────────────────────────────────────────────────────────────


def test_mg02_upgrade_keeps_the_earliest_sign_off_per_job_and_archives_the_rest(scratch_url):
    engine = _engine(scratch_url)
    command.upgrade(_cfg(scratch_url), PRE_P2)
    _seed_pre_p2(engine)

    command.upgrade(_cfg(scratch_url), P2)

    # J1 (t2, t1, t3) keeps t1; J2 (once) is untouched; J3 (tie at t1) keeps the smaller id.
    assert _rows(engine, "sign_offs") == KEPT
    # The archive holds the other rows, every column unchanged.
    assert _rows(engine, "sign_off_duplicates_archive") == ARCHIVED
    with engine.connect() as conn:
        unstamped = "SELECT count(*) FROM sign_off_duplicates_archive WHERE archived_at IS NULL"
        assert conn.execute(text(unstamped)).scalar() == 0

    # One sign-off per job from now on (FR-9.1): a second sign-off for J2 is refused by uq_sign_offs_job_id ...
    with pytest.raises(IntegrityError) as err, engine.begin() as conn:
        _insert_sign_off(conn, (uuid.uuid4(), "J2", CLINICIAN, "mv-dup", T3))
    assert err.value.orig.pgcode == "23505"
    assert err.value.orig.diag.constraint_name == "uq_sign_offs_job_id"
    # ... while a first sign-off for a new job is accepted.
    with engine.begin() as conn:
        _insert_sign_off(conn, (_sid("04"), "J4", CLINICIAN, "mv-j4", T3))
    assert _rows(engine, "sign_offs") == KEPT | {(_sid("04"), "J4", CLINICIAN, "mv-j4", T3)}
    engine.dispose()


def test_mg02_upgrade_of_a_database_without_duplicates_archives_nothing(scratch_url):
    engine = _engine(scratch_url)
    command.upgrade(_cfg(scratch_url), PRE_P2)
    with engine.begin() as conn:
        _insert_user(conn)
        _insert_sign_off(conn, S_J2_ONLY)
    command.upgrade(_cfg(scratch_url), P2)
    assert _rows(engine, "sign_offs") == {S_J2_ONLY}
    assert _rows(engine, "sign_off_duplicates_archive") == set()
    engine.dispose()


# ── MG-03 ───────────────────────────────────────────────────────────────────────────────────────────


def test_mg03_downgrade_restores_sign_offs_and_schema_exactly_then_upgrade_again(scratch_url):
    engine = _engine(scratch_url)
    command.upgrade(_cfg(scratch_url), PRE_P2)
    schema_pre_p2 = _snapshot(engine)
    _seed_pre_p2(engine)
    command.upgrade(_cfg(scratch_url), P2)
    schema_p2 = _snapshot(engine)
    assert schema_p2 != schema_pre_p2  # the snapshot sees the migration at all
    # A sign-off written while P2 is live must survive the round trip as well.
    s_j4 = (_sid("04"), "J4", CLINICIAN, "mv-j4", T3)
    with engine.begin() as conn:
        _insert_sign_off(conn, s_j4)

    command.downgrade(_cfg(scratch_url), PRE_P2)

    assert _version(engine) == PRE_P2
    # All six seeded rows are back with their ids and values, plus the post-upgrade one.
    assert _rows(engine, "sign_offs") == set(SEED) | {s_j4}
    assert not _tables(engine) & set(P2_TABLES)
    assert _index(engine, "ix_sign_offs_job_id") == ("sign_offs", False)
    assert _index(engine, "uq_sign_offs_job_id") is None
    assert _snapshot(engine) == schema_pre_p2

    command.upgrade(_cfg(scratch_url), P2)

    assert _version(engine) == P2
    assert _snapshot(engine) == schema_p2
    assert _rows(engine, "sign_offs") == KEPT | {s_j4}
    assert _rows(engine, "sign_off_duplicates_archive") == ARCHIVED
    assert _index(engine, "uq_sign_offs_job_id") == ("sign_offs", True)
    assert _index(engine, "ix_sign_offs_job_id") is None
    engine.dispose()


# ── MG-04 ───────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("holder", ["report_extractions", "extracted_rows"])
def test_mg04_downgrade_refuses_while_review_tables_hold_rows(scratch_url, monkeypatch, holder):
    engine = _engine(scratch_url)
    command.upgrade(_cfg(scratch_url), PRE_P2)
    _seed_pre_p2(engine)
    command.upgrade(_cfg(scratch_url), P2)
    with engine.begin() as conn:
        _insert_job(conn, "JR")
        if holder == "report_extractions":
            _insert_extraction(conn, "JR")
            expected_named = ["report_extractions"]
        else:  # rows (and their audit trail) without an extraction row
            _insert_edit(conn, _insert_row(conn, "JR"), "JR")
            expected_named = ["extracted_rows", "extraction_edits"]
    schema_before = _snapshot(engine)
    with engine.connect() as conn:
        counts_before = {t: _count(conn, t) for t in (*P2_TABLES, "sign_offs")}

    # Only exactly "1" accepts the loss.
    for value in (None, "", "0", "true", "yes", " 1"):
        if value is None:
            monkeypatch.delenv(LOSSY_ENV, raising=False)
        else:
            monkeypatch.setenv(LOSSY_ENV, value)
        with pytest.raises(RuntimeError, match=f"{LOSSY_ENV}=1") as err:
            command.downgrade(_cfg(scratch_url), PRE_P2)
        assert all(t in str(err.value) for t in expected_named)
        assert "Nothing was changed" in str(err.value)
        # Nothing changed: same revision, schema, and row counts.
        assert _version(engine) == P2
        assert _snapshot(engine) == schema_before
        with engine.connect() as conn:
            assert {t: _count(conn, t) for t in counts_before} == counts_before

    monkeypatch.setenv(LOSSY_ENV, "1")
    command.downgrade(_cfg(scratch_url), PRE_P2)

    assert _version(engine) == PRE_P2
    assert not _tables(engine) & set(P2_TABLES)
    assert _rows(engine, "sign_offs") == set(SEED)  # the archive is still restored on a lossy downgrade
    engine.dispose()


def _offline_sql(direction: str, revisions: str) -> str:
    buffer = io.StringIO()
    getattr(command, direction)(_cfg(NEVER_CONNECTED, buffer), revisions, sql=True)
    return buffer.getvalue()


def test_mg04_offline_sql_orders_the_guard_lock_archive_and_constraint(monkeypatch):
    monkeypatch.delenv(LOSSY_ENV, raising=False)
    down = _offline_sql("downgrade", f"{P2}:{PRE_P2}")
    guard = down.index("RAISE EXCEPTION")
    assert all(f"EXISTS (SELECT 1 FROM {t})" in down for t in REVIEW_TABLES)
    # Writers are locked out of the review tables before the emptiness check, so nothing arrives after it.
    lock = down.index("LOCK TABLE report_extractions, extracted_rows, extraction_edits IN SHARE ROW EXCLUSIVE MODE")
    assert lock < guard
    # The guard runs before the first destructive statement.
    assert guard < down.index("ALTER TABLE sign_offs DROP CONSTRAINT uq_sign_offs_job_id")
    assert guard < down.index("DROP TABLE")
    assert down.index("INSERT INTO sign_offs") < down.index("DROP TABLE sign_off_duplicates_archive")

    monkeypatch.setenv(LOSSY_ENV, "1")
    assert "RAISE EXCEPTION" not in _offline_sql("downgrade", f"{P2}:{PRE_P2}")

    up = _offline_sql("upgrade", f"{PRE_P2}:{P2}")
    # Writers are locked out before the archive step, and the unique constraint comes after the delete.
    order = [
        up.index("LOCK TABLE sign_offs IN SHARE ROW EXCLUSIVE MODE"),
        up.index("INSERT INTO sign_off_duplicates_archive"),
        up.index("DELETE FROM sign_offs"),
        up.index("DROP INDEX ix_sign_offs_job_id"),
        up.index("ADD CONSTRAINT uq_sign_offs_job_id UNIQUE (job_id)"),
    ]
    assert order == sorted(order)


def test_mg04_offline_guard_statement_raises_only_when_review_rows_exist(scratch_url, monkeypatch):
    monkeypatch.delenv(LOSSY_ENV, raising=False)
    down = _offline_sql("downgrade", f"{P2}:{PRE_P2}")
    guard_sql = next(line for line in down.splitlines() if line.startswith("DO $$")).rstrip(";")
    engine = _engine(scratch_url)
    command.upgrade(_cfg(scratch_url), P2)
    with engine.begin() as conn:
        conn.execute(text(guard_sql))  # empty review tables: passes
    with engine.begin() as conn:
        _insert_user(conn)
        _insert_job(conn, "JR")
        _insert_extraction(conn, "JR")
    with pytest.raises(DBAPIError, match=f"{LOSSY_ENV}=1"), engine.begin() as conn:
        conn.execute(text(guard_sql))
    engine.dispose()


# ── MG-05 ───────────────────────────────────────────────────────────────────────────────────────────

COMPARED_TABLES = (*P2_TABLES, "sign_offs")

# Hand-derived from plan §3.1–3.3 (FR-1.2 vocabularies) plus the S2 / S1 consistency checks (DECISIONS, T03).
EXPECTED_CHECKS = {
    "report_extractions": {
        "ck_report_extractions_file_kind", "ck_report_extractions_sex", "ck_report_extractions_age",
        "ck_report_extractions_confirmation_stamp",
    },
    "extracted_rows": {
        "ck_extracted_rows_kind", "ck_extracted_rows_qualifier", "ck_extracted_rows_printed_flag",
        "ck_extracted_rows_specimen", "ck_extracted_rows_confidence", "ck_extracted_rows_origin",
        "ck_extracted_rows_status", "ck_extracted_rows_review_reason", "ck_extracted_rows_review_codes",
        "ck_extracted_rows_unread_line_review", "ck_extracted_rows_parsed_payload",
    },
    "extraction_edits": {"ck_extraction_edits_field"},
    "sign_off_duplicates_archive": set(),
    "sign_offs": set(),
}

# Plan §3.1–3.3 defaults, in PostgreSQL's reflected spelling.
EXPECTED_DEFAULTS = {
    ("report_extractions", "page_count"): "0",
    ("report_extractions", "pages"): "'[]'::json",
    ("report_extractions", "warnings"): "'[]'::json",
    ("report_extractions", "notes"): "'[]'::json",
    ("report_extractions", "explanation_mode"): "'brief'::character varying",
    ("report_extractions", "revision"): "0",
    ("report_extractions", "created_at"): "now()",
    ("report_extractions", "updated_at"): "now()",
    ("extracted_rows", "name_raw"): "''::text",
    ("extracted_rows", "specimen"): "'unknown'::character varying",
    ("extracted_rows", "review_codes"): "'[]'::json",
    ("extracted_rows", "status"): "'pending'::character varying",
    ("extracted_rows", "version"): "1",
    ("extracted_rows", "created_at"): "now()",
    ("extracted_rows", "updated_at"): "now()",
    ("extraction_edits", "at"): "now()",
    ("sign_off_duplicates_archive", "archived_at"): "now()",
    ("sign_offs", "signed_at"): "now()",
}

# Plan §3.1–3.3: (table, column) -> (referenced table, ON DELETE rule). 'a' = NO ACTION, 'c' = CASCADE.
EXPECTED_FKS = {
    ("report_extractions", "job_id"): ("async_jobs", "c"),
    ("report_extractions", "confirmed_by"): ("users", "a"),
    ("extracted_rows", "job_id"): ("async_jobs", "c"),
    ("extraction_edits", "row_id"): ("extracted_rows", "c"),
    ("extraction_edits", "job_id"): ("async_jobs", "a"),
    ("extraction_edits", "user_id"): ("users", "a"),
    ("sign_offs", "clinician_id"): ("users", "a"),
}


def test_mg05_autogenerate_finds_no_difference_for_p2_tables_and_sign_offs(scratch_url):
    command.upgrade(_cfg(scratch_url), "head")
    engine = _engine(scratch_url)

    def include_name(name, type_, parent_names):
        return name in COMPARED_TABLES if type_ == "table" else True

    def include_object(obj, name, type_, reflected, compare_to):
        if type_ == "table":
            return name in COMPARED_TABLES
        table = getattr(obj, "table", None)
        return table is None or table.name in COMPARED_TABLES

    with engine.connect() as conn:
        context = MigrationContext.configure(
            conn, opts={"compare_type": True, "include_name": include_name, "include_object": include_object}
        )
        assert compare_metadata(context, Base.metadata) == []
    engine.dispose()


def test_mg05_constraints_defaults_and_delete_rules_are_the_planned_ones(scratch_url):
    command.upgrade(_cfg(scratch_url), "head")
    engine = _engine(scratch_url)
    with engine.connect() as conn:
        db_checks = {t: set() for t in COMPARED_TABLES}
        for table, name in conn.execute(
            text("SELECT conrelid::regclass::text, conname FROM pg_constraint WHERE contype = 'c'")
        ):
            if table in db_checks:
                db_checks[table].add(name)
        db_defaults = {
            (t, c): d
            for t, c, d in conn.execute(
                text(
                    "SELECT table_name, column_name, column_default FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND column_default IS NOT NULL"
                )
            )
            if t in COMPARED_TABLES
        }
        db_fks = {
            (t, c): (rt, rule)
            for t, c, rt, rule in conn.execute(
                text(
                    "SELECT con.conrelid::regclass::text, a.attname, con.confrelid::regclass::text, con.confdeltype "
                    "FROM pg_constraint con JOIN pg_attribute a "
                    "ON a.attrelid = con.conrelid AND a.attnum = con.conkey[1] WHERE con.contype = 'f'"
                )
            )
            if t in COMPARED_TABLES
        }
    engine.dispose()

    assert db_checks == EXPECTED_CHECKS
    orm_checks = {
        t: {c.name for c in Base.metadata.tables[t].constraints if isinstance(c, CheckConstraint)}
        for t in COMPARED_TABLES
    }
    assert orm_checks == EXPECTED_CHECKS
    assert db_defaults == EXPECTED_DEFAULTS
    assert db_fks == EXPECTED_FKS


def test_mg05_orm_ddl_and_migration_ddl_are_identical(scratch_url):
    """CHECK expressions and defaults are spelled twice (model, migration); build both and compare PostgreSQL's
    normalised definitions, so a vocabulary edited in one place only cannot pass."""
    command.upgrade(_cfg(scratch_url), "head")
    engine = _engine(scratch_url)
    deps = ("organisations", "departments", "users", "async_jobs")
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA orm"))
        Base.metadata.create_all(
            conn.execution_options(schema_translate_map={None: "orm"}),
            tables=[Base.metadata.tables[t] for t in (*deps, *COMPARED_TABLES)],
        )

    def definitions(conn, schema: str) -> dict:
        constraints = conn.execute(
            text(
                "SELECT c.relname, con.conname, con.contype, pg_get_constraintdef(con.oid) FROM pg_constraint con "
                "JOIN pg_class c ON c.oid = con.conrelid WHERE con.connamespace = CAST(:s AS regnamespace)"
            ),
            {"s": schema},
        )
        indexes = conn.execute(
            text("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = :s"), {"s": schema}
        )
        columns = conn.execute(
            text(
                "SELECT table_name, column_name, data_type, character_maximum_length, "
                "is_nullable, column_default FROM information_schema.columns WHERE table_schema = :s"
            ),
            {"s": schema},
        )

        def norm(row):
            return tuple(re.sub(rf"\b{schema}\.", "", str(v)) for v in row)

        return {
            "constraints": sorted(norm(r) for r in constraints if r[0] in COMPARED_TABLES),
            "indexes": sorted(norm(r) for r in indexes if r[0] in COMPARED_TABLES),
            "columns": sorted(norm(r) for r in columns if r[0] in COMPARED_TABLES),
        }

    with engine.connect() as conn:
        migrated, orm = definitions(conn, "public"), definitions(conn, "orm")
    engine.dispose()
    assert migrated["constraints"]  # non-vacuous
    assert migrated == orm


# ── MG-06 ───────────────────────────────────────────────────────────────────────────────────────────


def _violation(conn, sql: str, **params) -> str | None:
    """Run one statement in a savepoint; return the violated constraint's name (None if accepted)."""
    savepoint = conn.begin_nested()
    try:
        conn.execute(text(sql), params)
    except IntegrityError as exc:
        savepoint.rollback()
        return exc.orig.diag.constraint_name or f"<{exc.orig.pgcode}>"
    savepoint.rollback()
    return None


@pytest.fixture
def migrated(scratch_url):
    """Scratch DB at head with one user, one report job, its extraction, one certain row and one edit."""
    command.upgrade(_cfg(scratch_url), "head")
    engine = _engine(scratch_url)
    with engine.begin() as conn:
        _insert_user(conn)
        _insert_job(conn, "JC")
        _insert_extraction(conn, "JC")
        row_id = _insert_row(conn, "JC")
        edit_id = _insert_edit(conn, row_id, "JC")
    yield engine, row_id, edit_id
    engine.dispose()


def test_mg06_extracted_rows_vocabulary_checks(migrated):
    engine, row_id, _ = migrated
    upd = "UPDATE extracted_rows SET {col} = :v WHERE id = :id"
    # FR-1.2 / plan §3.2 vocabularies: every member accepted (so no allowed value is locked out) ...
    accepted = {
        "specimen": ["serum", "urine", "csf", "fluid", "other", "unknown"],
        "status": ["pending", "accepted", "edited", "deleted", "added"],
        "origin": ["text", "ocr", "column", "csv", "fhir", "hl7", "manual", "legacy_urine"],
        "qualifier": ["<", ">", "<=", ">=", None],
        "printed_flag": ["H", "L", "HH", "LL", "ABN", None],
        "confidence": [0.0, 0.85, 1.0],
    }
    # ... and anything else rejected by the named constraint (case, near misses, unnormalised symbols).
    rejected = {
        "specimen": ["blood", "Serum", "", "plasma"],
        "status": ["confirmed", "dismissed", "PENDING"],
        "origin": ["pdf", "LEGACY_URINE", "image"],
        "qualifier": ["≤", "=<", "="],
        "printed_flag": ["N", "h", "CRIT"],
        "confidence": [-0.01, 1.01, math.nan, math.inf],
        "kind": ["line", "ROW", ""],
    }
    with engine.connect() as conn, conn.begin():
        for col, values in accepted.items():
            for v in values:
                assert _violation(conn, upd.format(col=col), v=v, id=row_id) is None, (col, v)
        for col, values in rejected.items():
            for v in values:
                assert _violation(conn, upd.format(col=col), v=v, id=row_id) == f"ck_extracted_rows_{col}", (col, v)
        # kind: both members (an unread line must also be a review item, see the S2 test below).
        assert _violation(conn, upd.format(col="kind"), v="row", id=row_id) is None
        assert _violation(
            conn,
            "UPDATE extracted_rows SET kind = 'unread_line', needs_review = true, review_reason = 'Line not read', "
            "review_codes = CAST('[\"NR-UNREAD\"]' AS json), parsed_payload = NULL WHERE id = :id",
            id=row_id,
        ) is None


def test_mg06_report_extractions_and_edits_checks(migrated):
    engine, _, edit_id = migrated
    upd = "UPDATE report_extractions SET {col} = :v WHERE job_id = 'JC'"
    with engine.connect() as conn, conn.begin():
        for v in ("pdf", "image", "csv"):
            assert _violation(conn, upd.format(col="file_kind"), v=v) is None
        for v in ("docx", "PDF", "png"):
            assert _violation(conn, upd.format(col="file_kind"), v=v) == "ck_report_extractions_file_kind"
        for v in ("male", "female", None):  # S4: sex may be unknown, never assumed
            assert _violation(conn, upd.format(col="sex"), v=v) is None
        for v in ("M", "unknown", "Male"):
            assert _violation(conn, upd.format(col="sex"), v=v) == "ck_report_extractions_sex"
        for v in (0, 45, 130, None):
            assert _violation(conn, upd.format(col="age"), v=v) is None
        for v in (-1, 131):
            assert _violation(conn, upd.format(col="age"), v=v) == "ck_report_extractions_age"
        # The S1 stamp is all or nothing.
        stamp = "UPDATE report_extractions SET confirmed_by = :by, confirmed_at = :at WHERE job_id = 'JC'"
        assert _violation(conn, stamp, by=CLINICIAN, at=T1) is None
        assert _violation(conn, stamp, by=None, at=None) is None
        assert _violation(conn, stamp, by=None, at=T1) == "ck_report_extractions_confirmation_stamp"
        assert _violation(conn, stamp, by=CLINICIAN, at=None) == "ck_report_extractions_confirmation_stamp"

        # Plan §3.3: the audited fields, and nothing a client cannot set.
        edit = "UPDATE extraction_edits SET field = :v WHERE id = :id"
        for v in ("name_raw", "value_raw", "unit_raw", "ref_low", "ref_high", "ref_text", "printed_flag",
                  "specimen", "status", "kind"):
            assert _violation(conn, edit, v=v, id=edit_id) is None
        for v in ("analyte_key", "prelim_flag", "confidence", "needs_review", ""):
            assert _violation(conn, edit, v=v, id=edit_id) == "ck_extraction_edits_field"


def test_mg06_review_flag_reason_codes_and_payload_stay_consistent(migrated):
    """S2: needs_review ⇔ a non-blank reason ⇔ at least one code; unread lines always need review; a scorable row
    always carries its payload (plan §3.2, RR-25)."""
    engine, row_id, _ = migrated
    review = (
        "UPDATE extracted_rows SET needs_review = :nr, review_reason = :rr, review_codes = CAST(:rc AS json) "
        "WHERE id = :id"
    )
    with engine.connect() as conn, conn.begin():
        ok = dict(nr=True, rr="Unit not printed", rc='["NR-UNIT"]', id=row_id)
        assert _violation(conn, review, **ok) is None
        assert _violation(conn, review, **{**ok, "nr": False, "rr": None, "rc": "[]"}) is None
        assert _violation(conn, review, **{**ok, "rr": None}) == "ck_extracted_rows_review_reason"
        assert _violation(conn, review, **{**ok, "rr": "   "}) == "ck_extracted_rows_review_reason"
        assert _violation(conn, review, **{**ok, "nr": False, "rc": "[]"}) == "ck_extracted_rows_review_reason"
        assert _violation(conn, review, **{**ok, "rc": "[]"}) == "ck_extracted_rows_review_codes"
        assert _violation(conn, review, **{**ok, "nr": False, "rr": None}) == "ck_extracted_rows_review_codes"
        assert _violation(conn, review, **{**ok, "rc": '"NR-UNIT"'}) == "ck_extracted_rows_review_codes"
        assert _violation(conn, review, **{**ok, "rc": '{"NR-UNIT": true}'}) == "ck_extracted_rows_review_codes"
        # An unread line that is not a review item would be accepted silently at confirm.
        assert _violation(
            conn, "UPDATE extracted_rows SET kind = 'unread_line', parsed_payload = NULL WHERE id = :id", id=row_id
        ) == "ck_extracted_rows_unread_line_review"
        # A scorable row without its ParsedRow payload could not be scored as confirmed.
        assert _violation(
            conn, "UPDATE extracted_rows SET parsed_payload = NULL WHERE id = :id", id=row_id
        ) == "ck_extracted_rows_parsed_payload"
        # NOT NULL JSON columns reject SQL NULL.
        assert _violation(conn, "UPDATE extracted_rows SET review_codes = NULL WHERE id = :id", id=row_id) == "<23502>"


def test_mg06_keys_unique_row_index_and_cascades(migrated):
    engine, row_id, edit_id = migrated
    with engine.connect() as conn, conn.begin():
        # (job_id, row_index) is unique; the same index on another job is fine.
        with pytest.raises(IntegrityError) as err, conn.begin_nested():
            _insert_row(conn, "JC", row_index=0)
        assert err.value.orig.diag.constraint_name == "uq_extracted_rows_job_id_row_index"
        _insert_job(conn, "JD")
        _insert_row(conn, "JD", row_index=0)
        # Foreign keys: unknown job, unknown user.
        with pytest.raises(IntegrityError) as err, conn.begin_nested():
            _insert_extraction(conn, "no-such-job")
        assert err.value.orig.diag.constraint_name == "fk_report_extractions_job_id_async_jobs"
        with pytest.raises(IntegrityError) as err, conn.begin_nested():
            _insert_edit(conn, row_id, "JC", user_id=uuid.uuid4())
        assert err.value.orig.diag.constraint_name == "fk_extraction_edits_user_id_users"

        # Deleting a row takes its audit trail with it (row_id ON DELETE CASCADE) ...
        second = _insert_row(conn, "JC", row_index=1)
        _insert_edit(conn, second, "JC")
        conn.execute(text("DELETE FROM extracted_rows WHERE id = :id"), {"id": second})
        edits_of = text("SELECT count(*) FROM extraction_edits WHERE row_id = :id")
        assert conn.execute(edits_of, {"id": second}).scalar() == 0
        assert conn.execute(text("SELECT count(*) FROM extraction_edits WHERE id = :id"), {"id": edit_id}).scalar() == 1
        # ... but a job whose audit trail is non-empty cannot be deleted: extraction_edits.job_id has no ON DELETE
        # rule (plan §3.3), and PostgreSQL checks it before the row cascade reaches the edits. S9 keeps the trail.
        def job_counts(job: str) -> tuple[int, int, int, int]:
            """(job, extraction, rows, edits) row counts for one job."""
            return tuple(
                conn.execute(text(f"SELECT count(*) FROM {table} WHERE {key} = :j"), {"j": job}).scalar()
                for table, key in (
                    ("async_jobs", "id"), ("report_extractions", "job_id"), ("extracted_rows", "job_id"),
                    ("extraction_edits", "job_id"),
                )
            )

        assert job_counts("JC") == (1, 1, 1, 1)
        assert _violation(conn, "DELETE FROM async_jobs WHERE id = 'JC'") == "fk_extraction_edits_job_id_async_jobs"
        assert job_counts("JC") == (1, 1, 1, 1)
        # A job without edits cascades its extraction and rows (job_id ON DELETE CASCADE), other jobs untouched.
        _insert_extraction(conn, "JD")
        assert job_counts("JD") == (1, 1, 1, 0)
        conn.execute(text("DELETE FROM async_jobs WHERE id = 'JD'"))
        assert job_counts("JD") == (0, 0, 0, 0)
        assert job_counts("JC") == (1, 1, 1, 1)


def test_mg06_orm_round_trip_uses_sql_null_and_the_planned_defaults(scratch_url):
    command.upgrade(_cfg(scratch_url), "head")
    engine = _engine(scratch_url)
    with engine.begin() as conn:
        _insert_user(conn)
        _insert_job(conn, "JO")
    with Session(engine) as session:
        session.add(ReportExtraction(job_id="JO", file_path="/srv/uploads/o.csv", file_kind="csv"))
        row = ExtractedRowRecord(
            job_id="JO", row_index=0, kind="unread_line", needs_review=True, review_reason="Line not read",
            review_codes=["NR-UNREAD"], confidence=1.0, origin="csv", source_text="Na,,138", source_bbox=None,
            parsed_payload=None,
        )
        session.add(row)
        session.commit()
        row_id = row.id
    with engine.connect() as conn:
        ext = conn.execute(
            text(
                "SELECT page_count, pages::text, warnings::text, notes::text, explanation_mode, extractor_version, "
                "revision, confirmed_at, created_at IS NOT NULL, updated_at IS NOT NULL "
                "FROM report_extractions WHERE job_id = 'JO'"
            )
        ).one()
        r = conn.execute(
            text(
                "SELECT name_raw, specimen, status, version, source_bbox IS NULL, parsed_payload IS NULL, "
                "review_codes::text FROM extracted_rows WHERE id = :id"
            ),
            {"id": row_id},
        ).one()
    engine.dispose()
    # Plan §3.1 defaults; extractor_version "p2.1".
    assert tuple(ext) == (0, "[]", "[]", "[]", "brief", "p2.1", 0, None, True, True)
    # Plan §3.2 defaults; Python None is SQL NULL (not the JSON literal null).
    assert tuple(r) == ("", "unknown", "pending", 1, True, True, '["NR-UNREAD"]')
