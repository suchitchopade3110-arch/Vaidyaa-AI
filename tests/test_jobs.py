import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.v1.routes.jobs import list_recent_jobs, RecentJobsResponse
from app.models.async_job import AsyncJobRecord

class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows

class _FakeResult:
    def __init__(self, rows_or_scalar):
        self._val = rows_or_scalar

    def scalars(self):
        return _FakeScalars(self._val)

    def scalar(self):
        return self._val

class _FakeSession:
    def __init__(self, records: list[AsyncJobRecord]):
        self._records = records

    async def execute(self, query):
        # Very crude mock: if it's a count query, return the count
        query_str = str(query)
        if "count" in query_str.lower():
            return _FakeResult(len(self._records))
        # Otherwise return the records
        return _FakeResult(self._records)


@pytest.mark.asyncio
async def test_list_recent_jobs_returns_jobs():
    test_user_id = uuid.uuid4()

    records = [
        AsyncJobRecord(
            id=str(uuid.uuid4()),
            user_id=test_user_id,
            org_id=test_user_id,
            pipeline="report",
            status="queued",
            created_at=datetime.now(timezone.utc)
        ),
        AsyncJobRecord(
            id=str(uuid.uuid4()),
            user_id=test_user_id,
            org_id=test_user_id,
            pipeline="image",
            status="completed",
            created_at=datetime.now(timezone.utc)
        )
    ]

    db = _FakeSession(records)
    user = {"sub": str(test_user_id)}

    response = await list_recent_jobs(limit=10, user=user, db=db)

    assert isinstance(response, RecentJobsResponse)
    assert response.total == 2
    assert len(response.jobs) == 2
    assert response.jobs[0].pipeline == "report"
    assert response.jobs[1].pipeline == "image"


@pytest.mark.asyncio
async def test_list_recent_jobs_invalid_limit():
    test_user_id = uuid.uuid4()
    db = _FakeSession([])
    user = {"sub": str(test_user_id)}

    with pytest.raises(HTTPException) as exc:
        await list_recent_jobs(limit=101, user=user, db=db)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await list_recent_jobs(limit=0, user=user, db=db)
    assert exc.value.status_code == 400
