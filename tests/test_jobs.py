import pytest
import uuid
from datetime import datetime, timezone

from app.api.v1.routes.jobs import list_recent_jobs, RecentJobsResponse, RecentJobItem
from app.models.async_job import AsyncJobRecord

# We can reuse the _FakeSession and _FakeResult pattern used in other test files.
class _FakeResult:
    def __init__(self, data=None, is_scalar=False):
        self._data = data or []
        self._is_scalar = is_scalar

    def scalar_one_or_none(self):
        return self._data[0] if self._data else None

    def scalar(self):
        return self._data[0] if self._data else None

    def scalars(self):
        return _FakeResult(self._data, is_scalar=True)

    def all(self):
        return self._data

class _FakeSession:
    def __init__(self, items=None, total_count=0):
        self.items = items or []
        self.total_count = total_count

    async def execute(self, stmt):
        # basic routing: if statement is a count (has select(func.count())), return total_count
        # else return items
        stmt_str = str(stmt).lower()
        if "count" in stmt_str:
            return _FakeResult([self.total_count], is_scalar=True)
        return _FakeResult(self.items)

@pytest.mark.asyncio
async def test_list_recent_jobs():
    user_id = str(uuid.uuid4())
    org_id = str(uuid.uuid4())
    job_id = "task-123"

    mock_record = AsyncJobRecord(
        id=job_id,
        user_id=uuid.UUID(user_id),
        org_id=uuid.UUID(org_id),
        pipeline="report",
        status="completed",
        created_at=datetime.now(timezone.utc)
    )

    fake_session = _FakeSession(items=[mock_record], total_count=1)

    user = {"sub": user_id}

    response = await list_recent_jobs(limit=10, user=user, db=fake_session)

    assert isinstance(response, RecentJobsResponse)
    assert response.total == 1
    assert len(response.jobs) == 1

    job_item = response.jobs[0]
    assert job_item.job_id == job_id
    assert job_item.pipeline == "report"
    assert job_item.celery_task_id == job_id
    assert job_item.status == "completed"
