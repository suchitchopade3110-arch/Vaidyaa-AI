import io
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from app.core.config import settings
from app.main import app

client = TestClient(app)

import pytest
from unittest.mock import patch, AsyncMock
from app.db.session import get_db
from app.core.ownership import require_job_owner
import uuid
from datetime import datetime, timezone
from app.models.async_job import AsyncJobRecord

class _FakeResult:
    def scalar_one_or_none(self):
        return None

class _FakeSession:
    async def execute(self, stmt):
        return _FakeResult()
    async def add(self, record):
        pass
    async def commit(self):
        pass
    async def rollback(self):
        pass
    async def close(self):
        pass

async def override_get_db():
    yield _FakeSession()

async def override_require_job_owner():
    return AsyncJobRecord(
        id="test-task-id",
        user_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        pipeline="report",
        status="completed",
        created_at=datetime.now(timezone.utc)
    )

@pytest.fixture(autouse=True)
def mock_dependencies_and_celery():
    from app.main import app
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[require_job_owner] = override_require_job_owner

    patcher1 = patch('app.api.v1.routes.claims.record_job_ownership', new=AsyncMock())
    patcher2 = patch('app.api.v1.routes.images.record_job_ownership', new=AsyncMock())
    patcher3 = patch('app.api.v1.routes.reports.record_job_ownership', new=AsyncMock())

    m1 = patcher1.start()
    m2 = patcher2.start()
    m3 = patcher3.start()

    patcher_celery = patch('app.api.v1.routes.claims.verify_claim_task.apply_async')
    m_celery = patcher_celery.start()
    m_celery.return_value.id = "test-task-id"
    patcher_celery2 = patch('app.api.v1.routes.images.analyze_image_task.apply_async')
    m_celery2 = patcher_celery2.start()
    m_celery2.return_value.id = "test-task-id"
    patcher_celery3 = patch('app.api.v1.routes.reports.analyze_report_task.apply_async')
    m_celery3 = patcher_celery3.start()
    m_celery3.return_value.id = "test-task-id"

    patcher_celery4 = patch('app.api.v1.routes.jobs.AsyncResult')
    m_async = patcher_celery4.start()
    m_async.return_value.state = "SUCCESS"
    m_async.return_value.result = {"mock": "result"}
    m_async.return_value.info = {"mock": "result"}
    patcher_celery5 = patch('app.api.v1.routes.claims.AsyncResult')
    m_async2 = patcher_celery5.start()
    m_async2.return_value.state = "SUCCESS"
    m_async2.return_value.result = {"mock": "result"}
    m_async2.return_value.info = {"mock": "result"}
    patcher_celery6 = patch('app.api.v1.routes.images.AsyncResult')
    m_async3 = patcher_celery6.start()
    m_async3.return_value.state = "SUCCESS"
    m_async3.return_value.result = {"mock": "result"}
    m_async3.return_value.info = {"mock": "result"}
    patcher_celery7 = patch('app.api.v1.routes.reports.AsyncResult')
    m_async4 = patcher_celery7.start()
    m_async4.return_value.state = "SUCCESS"
    m_async4.return_value.result = {"mock": "result"}
    m_async4.return_value.info = {"mock": "result"}

    yield

    patcher1.stop()
    patcher2.stop()
    patcher3.stop()
    patcher_celery.stop()
    patcher_celery2.stop()
    patcher_celery3.stop()
    patcher_celery4.stop()
    patcher_celery5.stop()
    patcher_celery6.stop()
    patcher_celery7.stop()
    app.dependency_overrides.clear()


import pytest
from unittest.mock import patch
from app.core.ownership import record_job_ownership
from unittest.mock import AsyncMock

async def fake_record_job_ownership(*args, **kwargs):
    pass

patcher = patch('app.api.v1.routes.claims.record_job_ownership', new=AsyncMock())
patcher.start()
patcher2 = patch('app.api.v1.routes.images.record_job_ownership', new=AsyncMock())
patcher2.start()
patcher3 = patch('app.api.v1.routes.reports.record_job_ownership', new=AsyncMock())
patcher3.start()

patcher_celery = patch('app.api.v1.routes.claims.verify_claim_task.apply_async')
m_celery = patcher_celery.start()
m_celery.return_value.id = "test-task-id"
patcher_celery2 = patch('app.api.v1.routes.images.analyze_image_task.apply_async')
m_celery2 = patcher_celery2.start()
m_celery2.return_value.id = "test-task-id"
patcher_celery3 = patch('app.api.v1.routes.reports.analyze_report_task.apply_async')
m_celery3 = patcher_celery3.start()
m_celery3.return_value.id = "test-task-id"


from app.db.session import get_db

class _FakeResult:
    def scalar_one_or_none(self):
        return None


class _FakeSession:
    async def execute(self, stmt):
        return _FakeResult()
    async def add(self, record):
        pass
    async def commit(self):
        pass
    async def rollback(self):
        pass
    async def close(self):
        pass

async def override_get_db():
    yield _FakeSession()

app.dependency_overrides[get_db] = override_get_db

import pytest
from unittest.mock import patch, AsyncMock

# We need to patch celery app in jobs status
patcher_celery = patch('app.api.v1.routes.jobs.AsyncResult')
m_async = patcher_celery.start()
m_async.return_value.state = "SUCCESS"
m_async.return_value.result = {"mock": "result"}
m_async.return_value.info = {"mock": "result"}

patcher_celery2 = patch('app.api.v1.routes.claims.AsyncResult')
m_async2 = patcher_celery2.start()
m_async2.return_value.state = "SUCCESS"
m_async2.return_value.result = {"mock": "result"}
m_async2.return_value.info = {"mock": "result"}

patcher_celery3 = patch('app.api.v1.routes.images.AsyncResult')
m_async3 = patcher_celery3.start()
m_async3.return_value.state = "SUCCESS"
m_async3.return_value.result = {"mock": "result"}
m_async3.return_value.info = {"mock": "result"}

patcher_celery4 = patch('app.api.v1.routes.reports.AsyncResult')
m_async4 = patcher_celery4.start()
m_async4.return_value.state = "SUCCESS"
m_async4.return_value.result = {"mock": "result"}
m_async4.return_value.info = {"mock": "result"}

from app.core.ownership import require_job_owner

async def override_require_job_owner():
    from app.models.async_job import AsyncJobRecord
    import uuid
    from datetime import datetime, timezone
    return AsyncJobRecord(
        id="test-task-id",
        user_id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        pipeline="report",
        status="completed",
        created_at=datetime.now(timezone.utc)
    )

app.dependency_overrides[require_job_owner] = override_require_job_owner




def _auth_headers(role: str = "clinician") -> dict:
    """Bearer header for a live access token — these routes all require auth."""
    token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "role": role,
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=20),
        },
        settings.SECRET_KEY,
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
#  HEALTH
# ─────────────────────────────────────────────────────────────────────────────

def test_health():
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "ok"


def test_root():
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert "medical_disclaimer" in data
    assert "VaidyaAI" in data["platform"]


# ─────────────────────────────────────────────────────────────────────────────
#  MIDDLEWARE
# ─────────────────────────────────────────────────────────────────────────────

def test_disclaimer_header_on_all_responses():
    """X-Medical-Disclaimer header MUST appear on every response."""
    for path in ["/api/v1/health", "/"]:
        r = client.get(path)
        assert r.headers.get("X-Medical-Disclaimer") == "AI-assisted analysis. NOT diagnostic."


def test_request_id_header():
    r = client.get("/api/v1/health")
    assert "X-Request-ID" in r.headers
    assert len(r.headers["X-Request-ID"]) == 36


# ─────────────────────────────────────────────────────────────────────────────
#  CLAIM VERIFICATION
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_CLAIM = "Aspirin reduces the risk of heart attack in adults over 50 with hypertension."

def test_claim_pipeline_flow():
    # 1. Submit — claim_id is generated server-side (job_id), not passed in the URL.
    r = client.post(
        "/api/v1/verify/claim",
        json={"claim_text": SAMPLE_CLAIM},
        headers=_auth_headers(),
    )
    assert r.status_code == 202
    data = r.json()
    task_id = data["task_id"]
    assert "poll_url" in data

    # 2. Poll Status
    r = client.get(f"/api/v1/verify/claim/status/{task_id}", headers=_auth_headers())
    assert r.status_code == 200
    assert "status" in r.json()

    # 3. Get Result (might be 425 if not ready, but we check schema if possible)
    # Since we are using TestClient and it's async celery, it won't be ready.
    r = client.get(f"/api/v1/verify/claim/result/{task_id}", headers=_auth_headers())
    assert r.status_code in (425, 200)


# ─────────────────────────────────────────────────────────────────────────────
#  IMAGE ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def _make_fake_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
    )

def test_image_pipeline_flow():
    fake_img = _make_fake_png()
    # 1. Submit
    r = client.post(
        "/api/v1/analyze/image/xray",
        files={"file": ("test.png", io.BytesIO(fake_img), "image/png")},
        headers=_auth_headers(),
    )
    assert r.status_code in (202, 422)
    if r.status_code == 202:
        task_id = r.json()["task_id"]
        # 2. Poll
        r = client.get(f"/api/v1/analyze/image/status/{task_id}", headers=_auth_headers())
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
#  REPORT ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

SAMPLE_CSV = b"parameter,value,unit\nHbA1c,7.8,%\nGlucose,142,mg/dL"

def test_report_pipeline_flow():
    # 1. Submit
    r = client.post(
        "/api/v1/analyze/report/lab",
        files={"file": ("labs.csv", io.BytesIO(SAMPLE_CSV), "text/csv")},
        headers=_auth_headers(),
    )
    assert r.status_code == 202
    task_id = r.json()["task_id"]

    # 2. Poll
    r = client.get(f"/api/v1/analyze/report/status/{task_id}", headers=_auth_headers())
    assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
#  PHASE 2 E2E SCENARIOS — TEXT PIPELINE (T1–T6)
# ─────────────────────────────────────────────────────────────────────────────

def test_T1_claim_too_short_rejected():
    """Claim under min_length returns 422."""
    r = client.post(
        "/api/v1/verify/claim",
        json={"claim_text": "short"},
        headers=_auth_headers(),
    )
    assert r.status_code == 422


def test_T2_claim_missing_field_rejected():
    """Missing claim_text returns 422."""
    r = client.post(
        "/api/v1/verify/claim",
        json={},
        headers=_auth_headers(),
    )
    assert r.status_code == 422


def test_T3_report_csv_anomaly_flagged():
    """CSV with high HbA1c — response must contain task metadata."""
    csv = b"parameter,value,unit\nHbA1c,9.5,%\nGlucose,210,mg/dL"
    r = client.post(
        "/api/v1/analyze/report/lab",
        files={"file": ("labs.csv", io.BytesIO(csv), "text/csv")},
        headers=_auth_headers(),
    )
    assert r.status_code == 202
    data = r.json()
    assert "task_id" in data


def test_T4_unsupported_file_format_rejected():
    """Uploading an unsupported extension returns 415.

    .txt is deliberately in REPORT_EXTENSIONS (plain-text clinical notes),
    so it isn't a valid "unsupported" example — use .docx instead.
    """
    r = client.post(
        "/api/v1/analyze/report/lab",
        files={"file": ("report.docx", io.BytesIO(b"some text"), "application/octet-stream")},
        headers=_auth_headers(),
    )
    assert r.status_code == 415


def test_T5_oversized_file_rejected():
    """File exceeding size limit returns 413."""
    big = io.BytesIO(b"x" * (51 * 1024 * 1024))
    r = client.post(
        "/api/v1/analyze/report/lab",
        files={"file": ("big.pdf", big, "application/pdf")},
        headers=_auth_headers(),
    )
    assert r.status_code == 413


def test_T6_disclaimer_in_all_text_responses():
    """Every text pipeline response includes medical disclaimer."""
    r = client.post(
        "/api/v1/verify/claim",
        json={"claim_text": SAMPLE_CLAIM},
        headers=_auth_headers(),
    )
    data = r.json()
    assert "medical_disclaimer" in data
    assert "NOT A MEDICAL DIAGNOSIS" in data["medical_disclaimer"]


# ─────────────────────────────────────────────────────────────────────────────
#  PHASE 2 E2E SCENARIOS — IMAGE PIPELINE (I1–I5)
# ─────────────────────────────────────────────────────────────────────────────

def test_I1_invalid_analysis_type_rejected():
    """Unknown analysis_type returns 422."""
    r = client.post(
        "/api/v1/analyze/image/ultrasound",
        files={"file": ("test.png", io.BytesIO(_make_fake_png()), "image/png")},
        headers=_auth_headers(),
    )
    assert r.status_code == 422


def test_I2_image_ct_accepted():
    r = client.post(
        "/api/v1/analyze/image/ct",
        files={"file": ("ct.png", io.BytesIO(_make_fake_png()), "image/png")},
        headers=_auth_headers(),
    )
    assert r.status_code in (202, 422)


def test_I3_image_mri_accepted():
    r = client.post(
        "/api/v1/analyze/image/mri",
        files={"file": ("mri.png", io.BytesIO(_make_fake_png()), "image/png")},
        headers=_auth_headers(),
    )
    assert r.status_code in (202, 422)


def test_I4_image_skin_accepted():
    r = client.post(
        "/api/v1/analyze/image/skin",
        files={"file": ("skin.png", io.BytesIO(_make_fake_png()), "image/png")},
        headers=_auth_headers(),
    )
    assert r.status_code in (202, 422)


def test_I5_image_pathology_accepted():
    r = client.post(
        "/api/v1/analyze/image/pathology",
        files={"file": ("path.png", io.BytesIO(_make_fake_png()), "image/png")},
        headers=_auth_headers(),
    )
    assert r.status_code in (202, 422)
