import uuid
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes.jobs import router, RecentJobsResponse
from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.async_job import AsyncJobRecord

app = FastAPI()
app.include_router(router, prefix="/api/v1/jobs")

client = TestClient(app)

class _FakeResult:
    def __init__(self, records, total=0):
        self._records = records
        self._total = total

    def scalars(self):
        return self

    def all(self):
        return self._records

    def scalar(self):
        return self._total

class _FakeSession:
    def __init__(self, user_id):
        self.user_id = user_id
        # We'll create some fake records
        self.records = [
            AsyncJobRecord(
                id=f"job-{i}",
                user_id=self.user_id,
                org_id=uuid.uuid4(),
                pipeline="report",
                status="completed",
                created_at=datetime.now(timezone.utc)
            ) for i in range(3)
        ]

    async def execute(self, stmt):
        stmt_str = str(stmt).lower()
        if "count" in stmt_str:
            return _FakeResult([], total=len(self.records))
        else:
            return _FakeResult(self.records)


@pytest.fixture
def fake_user():
    return {"sub": str(uuid.uuid4())}

@pytest.fixture
def override_deps(fake_user):
    fake_session = _FakeSession(uuid.UUID(fake_user["sub"]))

    async def override_get_current_user():
        return fake_user

    async def override_get_db():
        yield fake_session

    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()


def test_list_recent_jobs(override_deps):
    response = client.get("/api/v1/jobs")
    assert response.status_code == 200

    data = response.json()
    assert data["total"] == 3
    assert len(data["jobs"]) == 3
    assert data["jobs"][0]["job_id"] == "job-0"
    assert data["jobs"][0]["pipeline"] == "report"

def test_list_recent_jobs_invalid_limit(override_deps):
    response = client.get("/api/v1/jobs?limit=200")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "INVALID_LIMIT"
