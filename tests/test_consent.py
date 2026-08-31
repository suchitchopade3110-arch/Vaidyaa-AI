"""Unit tests for DPD-01 — consent capture and purpose binding.

Same fake-session pattern as tests/test_ownership.py and
tests/test_signoff.py: exercises app/core/consent.py directly against a
fake AsyncSession, no live DB/server needed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.core.consent import (
    PURPOSE_REPORT_ANALYSIS,
    grant_consent,
    require_valid_consent,
    withdraw_consent,
)
from app.models.consent import ConsentRecord


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None

    def all(self):
        return list(self._rows)


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, existing: list | None = None):
        self._existing = existing or []
        self.added: list = []
        self.flushed = False

    async def execute(self, _query):
        return _FakeResult(self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True


# ── require_valid_consent ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_blocks_when_no_consent_record():
    db = _FakeSession(existing=[])

    with pytest.raises(HTTPException) as exc:
        await require_valid_consent(db, "patient-1", PURPOSE_REPORT_ANALYSIS)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "CONSENT_REQUIRED"


@pytest.mark.asyncio
async def test_allows_when_active_consent_exists():
    record = ConsentRecord(
        id=uuid.uuid4(),
        data_principal_id="patient-1",
        purpose=PURPOSE_REPORT_ANALYSIS,
        notice_version="v0-unset",
    )
    db = _FakeSession(existing=[record])

    result = await require_valid_consent(db, "patient-1", PURPOSE_REPORT_ANALYSIS)
    assert result is record


@pytest.mark.asyncio
async def test_blocks_when_only_withdrawn_consent_exists():
    """The fake session's query already filters withdrawn_at IS NULL (it
    just returns whatever `existing` was given), so this test represents
    the real query correctly returning nothing once withdrawn — i.e. an
    empty `existing` list, same as test_blocks_when_no_consent_record.
    Kept separate because it documents *why* the list would be empty."""
    db = _FakeSession(existing=[])
    with pytest.raises(HTTPException) as exc:
        await require_valid_consent(db, "patient-1", PURPOSE_REPORT_ANALYSIS)
    assert exc.value.status_code == 403


# ── grant_consent / withdraw_consent ────────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_consent_persists_expected_fields():
    db = _FakeSession()

    record = await grant_consent(db, "patient-1", PURPOSE_REPORT_ANALYSIS)

    assert db.added == [record]
    assert db.flushed is True
    assert record.data_principal_id == "patient-1"
    assert record.purpose == PURPOSE_REPORT_ANALYSIS
    assert record.notice_version  # non-empty placeholder


@pytest.mark.asyncio
async def test_withdraw_consent_sets_withdrawn_at_on_active_records():
    active = ConsentRecord(
        id=uuid.uuid4(),
        data_principal_id="patient-1",
        purpose=PURPOSE_REPORT_ANALYSIS,
        notice_version="v0-unset",
    )
    db = _FakeSession(existing=[active])

    withdrawn = await withdraw_consent(db, "patient-1", PURPOSE_REPORT_ANALYSIS)

    assert withdrawn == [active]
    assert active.withdrawn_at is not None
    assert active.withdrawn_at.tzinfo is not None


@pytest.mark.asyncio
async def test_withdraw_consent_returns_empty_list_when_nothing_active():
    db = _FakeSession(existing=[])

    withdrawn = await withdraw_consent(db, "patient-1", PURPOSE_REPORT_ANALYSIS)

    assert withdrawn == []


@pytest.mark.asyncio
async def test_withdrawn_consent_then_blocks_require_valid_consent():
    """End-to-end within the fake session: grant, withdraw, then confirm a
    fresh require_valid_consent call (which would now see an empty active
    set from a real query) is blocked."""
    active = ConsentRecord(
        id=uuid.uuid4(),
        data_principal_id="patient-1",
        purpose=PURPOSE_REPORT_ANALYSIS,
        notice_version="v0-unset",
        granted_at=datetime.now(timezone.utc),
    )
    withdraw_db = _FakeSession(existing=[active])
    await withdraw_consent(withdraw_db, "patient-1", PURPOSE_REPORT_ANALYSIS)
    assert active.withdrawn_at is not None

    # A real query filtering withdrawn_at IS NULL would now return nothing.
    post_withdrawal_db = _FakeSession(existing=[])
    with pytest.raises(HTTPException) as exc:
        await require_valid_consent(post_withdrawal_db, "patient-1", PURPOSE_REPORT_ANALYSIS)
    assert exc.value.status_code == 403
