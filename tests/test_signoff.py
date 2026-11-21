"""Unit tests for REG-02 — clinician sign-off.

Same fake-session pattern as tests/test_ownership.py: exercises the route
handler and the qr_service gate directly against a fake AsyncSession, no
live DB/server needed.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException

from app.api.v1.routes.signoff import SignOffRequest, sign_off_job
from app.models.audit_log import AuditLog
from app.models.sign_off import SignOff
from app.services.qr_service import require_signed_off


class _FakeScalars:
    def __init__(self, rows):
        self._rows = rows

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    def __init__(self, existing_signoffs: list | None = None):
        self._existing = existing_signoffs or []
        self.added: list = []
        self.flushed = False

    async def execute(self, _query):
        return _FakeResult(self._existing)

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        self.flushed = True


def _clinician(sub: str) -> dict:
    return {"sub": sub, "role": "clinician", "type": "access"}


class _State:
    def __init__(self, state, result=None):
        self.state = state
        self.result = result if result is not None else {"status": "completed"}


class _Record:
    def __init__(self, status="queued"):
        self.status = status


@pytest.fixture
def owned_completed_job(monkeypatch):
    """The job exists, is the caller's, and Celery reports it finished.
    (Those checks have their own tests below and in test_security_api.py.)"""
    import app.api.v1.routes.signoff as signoff_mod

    async def _owned(db, job_id, user, allow_admin=False):
        return _Record()

    monkeypatch.setattr(signoff_mod, "get_owned_job", _owned)
    monkeypatch.setattr(signoff_mod, "AsyncResult", lambda job_id, app=None: _State("SUCCESS"))
    return signoff_mod


# ── sign_off_job ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sign_off_writes_signoff_and_audit_log(owned_completed_job):
    clinician = uuid.uuid4()
    db = _FakeSession()

    response = await sign_off_job(
        "job-1",
        SignOffRequest(model_versions="chexnet:v1"),
        user=_clinician(str(clinician)),
        db=db,
    )

    assert response.job_id == "job-1"
    assert response.clinician_id == str(clinician)
    signoff_rows = [o for o in db.added if isinstance(o, SignOff)]
    audit_rows = [o for o in db.added if isinstance(o, AuditLog)]
    assert len(signoff_rows) == 1
    assert len(audit_rows) == 1
    assert audit_rows[0].action == "report.sign_off"
    assert audit_rows[0].resource_id == "job-1"
    assert db.flushed is True


@pytest.mark.asyncio
async def test_duplicate_sign_off_rejected_with_409(owned_completed_job):
    clinician = uuid.uuid4()
    existing = SignOff(
        id=uuid.uuid4(), job_id="job-1", clinician_id=clinician, model_versions="chexnet:v1"
    )
    db = _FakeSession(existing_signoffs=[existing])

    with pytest.raises(HTTPException) as exc:
        await sign_off_job(
            "job-1",
            SignOffRequest(model_versions="chexnet:v2"),
            user=_clinician(str(clinician)),
            db=db,
        )
    assert exc.value.status_code == 409
    # Nothing new should have been staged once the duplicate check fires.
    assert db.added == []


@pytest.mark.asyncio
async def test_sign_off_unknown_or_foreign_job_is_404(monkeypatch):
    import app.api.v1.routes.signoff as signoff_mod

    async def _not_owned(db, job_id, user, allow_admin=False):
        return None

    monkeypatch.setattr(signoff_mod, "get_owned_job", _not_owned)
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await sign_off_job("nope", SignOffRequest(model_versions="x"), user=_clinician(str(uuid.uuid4())), db=db)
    assert exc.value.status_code == 404
    assert db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["PENDING", "STARTED", "RETRY", "FAILURE"])
async def test_sign_off_unfinished_job_is_409(monkeypatch, state):
    import app.api.v1.routes.signoff as signoff_mod

    async def _owned(db, job_id, user, allow_admin=False):
        return _Record()

    monkeypatch.setattr(signoff_mod, "get_owned_job", _owned)
    monkeypatch.setattr(signoff_mod, "AsyncResult", lambda job_id, app=None: _State(state))
    db = _FakeSession()
    with pytest.raises(HTTPException) as exc:
        await sign_off_job("job-1", SignOffRequest(model_versions="x"), user=_clinician(str(uuid.uuid4())), db=db)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "JOB_NOT_COMPLETE"
    assert db.added == []


# ── qr_service.require_signed_off ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_qr_share_blocked_without_signoff():
    db = _FakeSession(existing_signoffs=[])

    with pytest.raises(HTTPException) as exc:
        await require_signed_off("job-1", report=None, db=db)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "NOT_SIGNED_OFF"


@pytest.mark.asyncio
async def test_qr_share_allowed_once_signed_off():
    signoff = SignOff(
        id=uuid.uuid4(), job_id="job-1", clinician_id=uuid.uuid4(), model_versions="chexnet:v1"
    )
    db = _FakeSession(existing_signoffs=[signoff])

    # Should not raise.
    await require_signed_off("job-1", report=None, db=db)
