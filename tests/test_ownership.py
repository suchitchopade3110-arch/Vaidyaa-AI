"""Unit tests for app/core/ownership.py — SEC-01 per-user job ownership.

Same pattern as tests/test_auth.py: calls the dependency/helper functions
directly against a fake AsyncSession, bypassing FastAPI's Depends() wiring
and a real database — no live server, DB, or Redis needed. The fake session
is a stand-in for a single `select(AsyncJobRecord).where(...)` round trip,
which is all `require_job_owner` and `record_job_ownership` ever do.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.core.ownership import record_job_ownership, require_job_owner
from app.models.async_job import AsyncJobRecord


class _FakeResult:
    def __init__(self, record: AsyncJobRecord | None):
        self._record = record

    def scalar_one_or_none(self):
        return self._record


class _FakeSession:
    """Stands in for AsyncSession across one execute()/add()/flush() cycle."""

    def __init__(self, existing: AsyncJobRecord | None = None):
        self._existing = existing
        self.added: list[AsyncJobRecord] = []
        self.flushed = False

    async def execute(self, _query):
        return _FakeResult(self._existing)

    def add(self, record: AsyncJobRecord):
        self.added.append(record)

    async def flush(self):
        self.flushed = True


def _user(sub: str, org: str | None = None) -> dict:
    payload = {"sub": sub, "role": "clinician", "type": "access"}
    if org:
        payload["org_id"] = org
    return payload


# ── require_job_owner ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_can_read_their_own_job():
    user_a = uuid.uuid4()
    job_id = "task-123"
    record = AsyncJobRecord(id=job_id, user_id=user_a, org_id=user_a, pipeline="report")
    db = _FakeSession(existing=record)

    result = await require_job_owner(job_id, user=_user(str(user_a)), db=db)
    assert result is record


@pytest.mark.asyncio
async def test_other_user_gets_404_not_403():
    """The acceptance criteria's core case: user A submits a job, user B
    requests it by ID, and the response is 404 — not 403, which would leak
    that the job exists for someone else."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    job_id = "task-123"
    record = AsyncJobRecord(id=job_id, user_id=user_a, org_id=user_a, pipeline="report")
    db = _FakeSession(existing=record)

    with pytest.raises(HTTPException) as exc:
        await require_job_owner(job_id, user=_user(str(user_b)), db=db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_nonexistent_job_gets_404():
    db = _FakeSession(existing=None)

    with pytest.raises(HTTPException) as exc:
        await require_job_owner("no-such-job", user=_user(str(uuid.uuid4())), db=db)
    assert exc.value.status_code == 404


# ── record_job_ownership ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_job_ownership_persists_expected_fields():
    user_a = uuid.uuid4()
    db = _FakeSession()

    record = await record_job_ownership(db, "task-456", _user(str(user_a)), "image")

    assert db.added == [record]
    assert db.flushed is True
    assert record.id == "task-456"
    assert record.user_id == user_a
    assert record.pipeline == "image"
    assert record.status == "queued"


@pytest.mark.asyncio
async def test_record_job_ownership_falls_back_to_user_id_when_no_org():
    """User.org_id is nullable until PLT-01's onboarding flow exists (see
    app/core/ownership.py); org_id must still land non-null on the row."""
    user_a = uuid.uuid4()
    db = _FakeSession()

    record = await record_job_ownership(db, "task-789", _user(str(user_a)), "claim")

    assert record.org_id == user_a


@pytest.mark.asyncio
async def test_record_job_ownership_uses_real_org_id_when_present():
    user_a = uuid.uuid4()
    org = uuid.uuid4()
    db = _FakeSession()

    record = await record_job_ownership(db, "task-999", _user(str(user_a), org=str(org)), "report")

    assert record.org_id == org
