from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.v1.routes.jobs import list_recent_jobs, RecentJobsResponse
from app.models.async_job import AsyncJobRecord

class _FakeResult:
    def __init__(self, records, total=0):
        self._records = records
        self._total = total

    def scalars(self):
        return self

    def all(self):
        return self._records

    def scalar_one(self):
        return self._total

class _FakeSession:
    """Stands in for AsyncSession across one execute() cycle for list_recent_jobs."""

    def __init__(self, records: list[AsyncJobRecord], total: int):
        self.records = records
        self.total = total

    async def execute(self, query):
        query_str = str(query).lower()
        if "count" in query_str:
            return _FakeResult([], self.total)
        return _FakeResult(self.records)

def _user(sub: str) -> dict:
    return {"sub": sub, "role": "clinician", "type": "access"}

@pytest.mark.asyncio
async def test_list_recent_jobs_returns_jobs():
    user_id = uuid.uuid4()
    job_id = "task-123"
    now = datetime.now(timezone.utc)

    record = AsyncJobRecord(
        id=job_id,
        user_id=user_id,
        org_id=user_id,
        pipeline="report",
        status="queued",
        created_at=now
    )

    db = _FakeSession(records=[record], total=1)

    response = await list_recent_jobs(limit=20, user=_user(str(user_id)), db=db)

    assert isinstance(response, RecentJobsResponse)
    assert response.total == 1
    assert len(response.jobs) == 1

    job = response.jobs[0]
    assert job.job_id == job_id
    assert job.pipeline == "report"
    assert job.status == "queued"
    assert job.created_at == now

@pytest.mark.asyncio
async def test_list_recent_jobs_empty():
    user_id = uuid.uuid4()
    db = _FakeSession(records=[], total=0)

    response = await list_recent_jobs(limit=10, user=_user(str(user_id)), db=db)

    assert isinstance(response, RecentJobsResponse)
    assert response.total == 0
    assert len(response.jobs) == 0

@pytest.mark.asyncio
async def test_list_recent_jobs_invalid_limit():
    user_id = uuid.uuid4()
    db = _FakeSession(records=[], total=0)

    with pytest.raises(HTTPException) as exc:
        await list_recent_jobs(limit=101, user=_user(str(user_id)), db=db)

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "INVALID_LIMIT"

    with pytest.raises(HTTPException) as exc2:
        await list_recent_jobs(limit=0, user=_user(str(user_id)), db=db)

    assert exc2.value.status_code == 400
    assert exc2.value.detail["code"] == "INVALID_LIMIT"
