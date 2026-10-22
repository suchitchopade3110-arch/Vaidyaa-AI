import pytest
from fastapi import HTTPException
import uuid
from datetime import datetime, timezone

from app.api.v1.routes.jobs import list_recent_jobs
from app.models.async_job import AsyncJobRecord


class _FakeResult:
    def __init__(self, records):
        self._records = records
        self._index = 0

    def scalars(self):
        return self

    def all(self):
        return self._records

    def scalar(self):
        return len(self._records) if self._records else 0


class _FakeSession:
    def __init__(self, existing: list[AsyncJobRecord] | None = None):
        self._existing = existing or []
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        # We can loosely simulate the query logic by filtering
        query_str = str(query)
        if "count" in query_str.lower():
            # Return count result
            return _FakeResult(self._existing)
        else:
            return _FakeResult(self._existing)


def _user(sub: str) -> dict:
    return {"sub": sub, "role": "clinician", "type": "access"}


@pytest.mark.asyncio
async def test_list_recent_jobs_success():
    user_id = uuid.uuid4()
    job1 = AsyncJobRecord(
        id="task-1",
        user_id=user_id,
        org_id=user_id,
        pipeline="report",
        status="completed",
        created_at=datetime.now(timezone.utc)
    )
    job2 = AsyncJobRecord(
        id="task-2",
        user_id=user_id,
        org_id=user_id,
        pipeline="image",
        status="queued",
        created_at=datetime.now(timezone.utc)
    )
    db = _FakeSession(existing=[job1, job2])

    response = await list_recent_jobs(limit=10, user=_user(str(user_id)), db=db)

    assert response.total == 2
    assert len(response.jobs) == 2
    assert response.jobs[0].job_id == "task-1"
    assert response.jobs[0].pipeline == "report"
    assert response.jobs[1].job_id == "task-2"


@pytest.mark.asyncio
async def test_list_recent_jobs_empty():
    user_id = uuid.uuid4()
    db = _FakeSession(existing=[])

    response = await list_recent_jobs(limit=10, user=_user(str(user_id)), db=db)

    assert response.total == 0
    assert len(response.jobs) == 0


@pytest.mark.asyncio
async def test_list_recent_jobs_invalid_limit():
    user_id = uuid.uuid4()
    db = _FakeSession()

    with pytest.raises(HTTPException) as exc:
        await list_recent_jobs(limit=101, user=_user(str(user_id)), db=db)
    assert exc.value.status_code == 400

    with pytest.raises(HTTPException) as exc:
        await list_recent_jobs(limit=0, user=_user(str(user_id)), db=db)
    assert exc.value.status_code == 400
