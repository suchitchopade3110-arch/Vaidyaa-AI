"""HTTP-level security regression tests against a real PostgreSQL schema.

Each test here pins one access-control hole that existed before and must not
come back:

  - anonymous QR minting for any signed report (PHI leak via share link)
  - QR minting for another user's report
  - sign-off of non-existent, foreign, or unfinished jobs
  - cross-user patient history reads (HTTP routes and the worker's
    history merge into job results)
  - job listing returning nothing / other users' jobs
  - open self-registration in production
  - unthrottled password guessing on /auth/login

Needs PostgreSQL migrated to head (`alembic upgrade head`) and Redis.
Skipped when the database is unreachable unless REQUIRE_DB_TESTS=1 is set —
CI sets it, so a missing database fails the build instead of passing silently.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.core.config import settings

_engine = create_engine(settings.SYNC_DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1))

try:
    with _engine.connect() as _conn:
        _conn.execute(text("SELECT 1 FROM async_jobs LIMIT 1"))
    _DB_OK, _DB_ERR = True, ""
except Exception as exc:  # pragma: no cover - environment dependent
    _DB_OK, _DB_ERR = False, str(exc)

if not _DB_OK:
    if os.getenv("REQUIRE_DB_TESTS") == "1":
        raise RuntimeError(f"REQUIRE_DB_TESTS=1 but the database is unavailable: {_DB_ERR}")
    pytest.skip(f"database unavailable: {_DB_ERR}", allow_module_level=True)

from app.main import app  # noqa: E402  (import after the DB probe)
from app.models.async_job import AsyncJobRecord  # noqa: E402
from app.models.report import Report  # noqa: E402
from app.models.sign_off import SignOff  # noqa: E402
from app.models.user import User  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _lifespan():
    with client:  # one event loop for the async engine's connection pool
        yield


def _token(sub: str, role: str = "clinician") -> str:
    return jwt.encode(
        {"sub": sub, "role": role, "type": "access", "exp": datetime.now(timezone.utc) + timedelta(minutes=10)},
        settings.SECRET_KEY,
        algorithm="HS256",
    )


def _h(sub: str, role: str = "clinician") -> dict:
    return {"Authorization": f"Bearer {_token(sub, role)}"}


def _job(owner: str, pipeline: str = "report") -> str:
    job_id = str(uuid.uuid4())
    with Session(_engine) as s:
        s.add(AsyncJobRecord(id=job_id, user_id=uuid.UUID(owner), org_id=uuid.UUID(owner), pipeline=pipeline, status="queued"))
        s.commit()
    return job_id


def _report(job_id: str, patient_id: uuid.UUID | None = None, risk: float = 10.0) -> uuid.UUID:
    report_id = uuid.uuid4()
    with Session(_engine) as s:
        if patient_id is not None:
            s.execute(
                text("INSERT INTO patients (id, patient_code) VALUES (:id, :code) ON CONFLICT (id) DO NOTHING"),
                {"id": str(patient_id), "code": f"P-{str(patient_id)[:8]}"},
            )
        s.add(Report(id=report_id, patient_id=patient_id, report_type="lab", status="COMPLETED",
                     risk_score=risk, celery_task_id=job_id))
        s.commit()
    return report_id


def _sign(job_id: str, clinician: str) -> None:
    with Session(_engine) as s:
        s.add(SignOff(id=uuid.uuid4(), job_id=job_id, clinician_id=uuid.UUID(clinician), model_versions="test"))
        s.commit()


def _user(role: str = "clinician") -> str:
    """A real users row — sign_offs.clinician_id has an FK to users.id."""
    uid = uuid.uuid4()
    with Session(_engine) as s:
        s.add(User(id=uid, username=f"t_{uid.hex[:12]}", hashed_password="x", role=role))
        s.commit()
    return str(uid)


@pytest.fixture
def users():
    return _user(), _user()


@pytest.fixture
def celery_success(monkeypatch):
    import app.api.v1.routes.signoff as signoff_mod

    class _R:
        state = "SUCCESS"
        result = {"status": "completed"}

    monkeypatch.setattr(signoff_mod, "AsyncResult", lambda job_id, app=None: _R())


# ── QR share-link minting ────────────────────────────────────────────────────


def test_qr_mint_requires_authentication(users):
    a, _ = users
    job = _job(a)
    _report(job)
    _sign(job, a)
    r = client.get(f"/reports/{job}/qr")
    assert r.status_code == 401
    assert r.headers.get("content-type", "").startswith("application/json")
    assert r.headers.get("www-authenticate") == "Bearer"


def test_qr_mint_for_another_users_report_is_404(users):
    a, b = users
    job = _job(a)
    report_id = _report(job)
    _sign(job, a)
    for ident in (job, str(report_id)):
        r = client.get(f"/reports/{ident}/qr", headers=_h(b))
        assert r.status_code == 404, ident


def test_qr_mint_owner_signed_report_ok_unsigned_blocked(users):
    a, _ = users
    signed = _job(a)
    _report(signed)
    _sign(signed, a)
    r = client.get(f"/reports/{signed}/qr", headers=_h(a))
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"

    unsigned = _job(a)
    _report(unsigned)
    r = client.get(f"/reports/{unsigned}/qr", headers=_h(a))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "NOT_SIGNED_OFF"


# ── Clinician sign-off ───────────────────────────────────────────────────────


def test_sign_off_rejects_unknown_job(users, celery_success):
    a, _ = users
    r = client.post(f"/api/v1/jobs/{uuid.uuid4()}/sign-off", json={"model_versions": "x"}, headers=_h(a))
    assert r.status_code == 404


def test_sign_off_rejects_another_users_job(users, celery_success):
    a, b = users
    job = _job(a)
    r = client.post(f"/api/v1/jobs/{job}/sign-off", json={"model_versions": "x"}, headers=_h(b))
    assert r.status_code == 404


def test_sign_off_rejects_unfinished_job(users):
    a, _ = users
    job = _job(a)  # never dispatched -> Celery reports PENDING
    r = client.post(f"/api/v1/jobs/{job}/sign-off", json={"model_versions": "x"}, headers=_h(a))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "JOB_NOT_COMPLETE"


def test_sign_off_owner_ok_then_duplicate_409(users, celery_success):
    a, _ = users
    job = _job(a)
    r = client.post(f"/api/v1/jobs/{job}/sign-off", json={"model_versions": "xgb:v1"}, headers=_h(a))
    assert r.status_code == 201, r.text
    r = client.post(f"/api/v1/jobs/{job}/sign-off", json={"model_versions": "xgb:v1"}, headers=_h(a))
    assert r.status_code == 409


def test_admin_may_sign_off_any_job(users, celery_success):
    a, b = users  # noqa: F841
    job = _job(a)
    admin = _user(role="admin")
    r = client.post(f"/api/v1/jobs/{job}/sign-off", json={"model_versions": "x"}, headers=_h(admin, role="admin"))
    assert r.status_code == 201, r.text
    # ...but an ordinary clinician still can't sign someone else's job.
    assert client.post(f"/api/v1/jobs/{job}/sign-off", json={"model_versions": "x"}, headers=_h(b)).status_code == 404


def test_viewer_role_cannot_sign_off(users, celery_success):
    a, _ = users
    job = _job(a)
    r = client.post(f"/api/v1/jobs/{job}/sign-off", json={"model_versions": "x"}, headers=_h(a, role="viewer"))
    assert r.status_code == 403


# ── Patient history ──────────────────────────────────────────────────────────


def test_patient_history_is_scoped_to_callers_jobs(users):
    a, b = users
    patient = uuid.uuid4()
    a_report = _report(_job(a), patient, risk=11)
    b_report = _report(_job(b), patient, risk=22)

    r = client.get(f"/patients/{patient}/history", headers=_h(a))
    assert r.status_code == 200
    ids = {row["report_id"] for row in r.json()["history"]}
    assert ids == {str(a_report)}

    r = client.get(f"/patients/{patient}/latest", headers=_h(b))
    assert r.status_code == 200 and r.json()["report_id"] == str(b_report)

    r = client.get(f"/patients/{patient}/history", headers=_h(str(uuid.uuid4())))
    assert r.status_code == 404

    r = client.get(f"/patients/{patient}/history", headers=_h(str(uuid.uuid4()), role="admin"))
    assert {row["report_id"] for row in r.json()["history"]} == {str(a_report), str(b_report)}

    # Comparing against someone else's report is a 404, not a leak.
    r = client.get(f"/patients/{patient}/compare", params={"current_id": str(a_report), "previous_id": str(b_report)}, headers=_h(a))
    assert r.status_code == 404


def test_worker_history_merge_only_includes_submitters_reports(users):
    from app.services.patient_history_service import get_patient_history_sync

    a, b = users
    patient = uuid.uuid4()
    a_old = _report(_job(a), patient)
    _report(_job(b), patient)
    current_job = _job(a)

    rows = get_patient_history_sync(str(patient), limit=5, owner_job_id=current_job)
    assert {row["report_id"] for row in rows} == {str(a_old)}
    assert get_patient_history_sync(str(patient), limit=5, owner_job_id=None) == []


# ── Job listing ──────────────────────────────────────────────────────────────


def test_job_list_returns_only_callers_jobs(users):
    a, b = users
    mine = {_job(a, "report"), _job(a, "claim")}
    _job(b, "image")
    r = client.get("/api/v1/jobs?limit=50", headers=_h(a))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2
    assert {j["job_id"] for j in body["jobs"]} == mine
    assert {j["pipeline"] for j in body["jobs"]} == {"report", "claim"}
    assert client.get("/api/v1/jobs?limit=0", headers=_h(a)).status_code == 400


# ── Auth hardening ───────────────────────────────────────────────────────────


def test_self_registration_disabled_when_configured_off(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SELF_REGISTRATION", False)
    r = client.post("/auth/register", json={"username": f"u_{uuid.uuid4().hex[:8]}", "password": "longenough1"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "REGISTRATION_DISABLED"


def test_self_registration_defaults_off_in_production(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_SELF_REGISTRATION", None)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert settings.self_registration_enabled is False
    monkeypatch.setattr(settings, "APP_ENV", "development")
    assert settings.self_registration_enabled is True


def test_login_is_rate_limited():
    from app.core.rate_limit import AUTH_RATE_LIMIT, limiter

    budget = int(AUTH_RATE_LIMIT.split("/")[0])
    limiter.reset()
    was_enabled = limiter.enabled
    limiter.enabled = True
    try:
        codes = [
            client.post("/auth/login", json={"username": "nobody-here", "password": "wrong-password"}).status_code
            for _ in range(budget + 2)
        ]
    finally:
        limiter.enabled = was_enabled
        limiter.reset()
    assert codes[:budget] == [401] * budget
    assert 429 in codes[budget:]


# ── Iteration 2: outcome-aware sign-off, job status persistence ──────────────


def _celery(monkeypatch, state, result=None):
    import app.api.v1.routes.signoff as signoff_mod

    class _R:
        pass

    _R.state, _R.result = state, result
    monkeypatch.setattr(signoff_mod, "AsyncResult", lambda job_id, app=None: _R())


@pytest.mark.parametrize("inner", ["failed", "low_quality"])
def test_failed_analysis_cannot_be_signed_off(users, monkeypatch, inner):
    # Celery SUCCESS only means the task returned; the pipeline reports its
    # own failure in the payload, and that must not be signable/shareable.
    a, _ = users
    job = _job(a)
    _celery(monkeypatch, "SUCCESS", {"status": inner, "error": "Quality gate"})
    r = client.post(f"/api/v1/jobs/{job}/sign-off", json={"model_versions": "x"}, headers=_h(a))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "JOB_NOT_COMPLETE"


def _set_status(job_id: str, status: str) -> None:
    with Session(_engine) as s:
        s.execute(text("UPDATE async_jobs SET status=:s WHERE id=:id"), {"s": status, "id": job_id})
        s.commit()


def test_sign_off_after_result_expiry_uses_stored_outcome(users, monkeypatch):
    a, _ = users
    done, failed = _job(a), _job(a)
    _set_status(done, "completed")
    _set_status(failed, "failed")
    _celery(monkeypatch, "PENDING")  # Celery has forgotten both results
    assert client.post(f"/api/v1/jobs/{done}/sign-off", json={"model_versions": "x"}, headers=_h(a)).status_code == 201
    assert client.post(f"/api/v1/jobs/{failed}/sign-off", json={"model_versions": "x"}, headers=_h(a)).status_code == 409


def test_worker_records_pipeline_outcome_on_job_row(users):
    from app.workers.pipeline_tasks import analyze_report_task

    a, _ = users
    ok, bad, crashed = _job(a), _job(a), _job(a)
    analyze_report_task.on_success({"status": "completed"}, ok, (), {})
    analyze_report_task.on_success({"status": "low_quality"}, bad, (), {})
    analyze_report_task.on_failure(RuntimeError("boom"), crashed, (), {}, None)
    with Session(_engine) as s:
        rows = dict(s.execute(text("SELECT id, status FROM async_jobs WHERE id IN (:a,:b,:c)"),
                              {"a": ok, "b": bad, "c": crashed}).all())
    assert rows == {ok: "completed", bad: "low_quality", crashed: "failed"}

    r = client.get("/api/v1/jobs?limit=50", headers=_h(a))
    listed = {j["job_id"]: j["status"] for j in r.json()["jobs"]}
    assert listed[ok] == "completed" and listed[bad] == "low_quality" and listed[crashed] == "failed"


# ── Iteration 2: WebSocket auth without the token in the URL ─────────────────


def test_websocket_accepts_token_via_subprotocol_and_rejects_others(users):
    from starlette.websockets import WebSocketDisconnect

    a, b = users
    job = _job(a)
    with client.websocket_connect(f"/ws/{job}", subprotocols=["vaidya.bearer", _token(a)]) as ws:
        assert ws.accepted_subprotocol == "vaidya.bearer"
        assert ws.receive_json()["job_id"] == job
    with client.websocket_connect(f"/ws/{job}?token={_token(a)}") as ws:  # legacy fallback
        assert ws.receive_json()["job_id"] == job
    for protocols in (["vaidya.bearer", _token(b)], ["vaidya.bearer", "garbage"], ["vaidya.bearer"]):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/{job}", subprotocols=protocols) as ws:
                ws.receive_json()


# ── Iteration 2: auth must survive a rate-limit storage outage ───────────────


def test_login_still_works_when_rate_limit_storage_is_down(monkeypatch):
    from limits.storage import storage_from_string
    from limits.strategies import FixedWindowRateLimiter

    from app.core.rate_limit import limiter

    dead = storage_from_string("redis://127.0.0.1:6390/0")  # nothing listens here
    monkeypatch.setattr(limiter, "_storage", dead)
    monkeypatch.setattr(limiter, "_limiter", FixedWindowRateLimiter(dead))
    monkeypatch.setattr(limiter, "_storage_dead", False)
    monkeypatch.setattr(limiter, "enabled", True)
    r = client.post("/auth/login", json={"username": "nobody-here", "password": "wrong-password"})
    assert r.status_code == 401  # not 500


def test_txt_report_upload_is_rejected_up_front(users):
    a, _ = users
    r = client.post("/api/v1/analyze/report/lab", files={"file": ("labs.txt", b"Glucose 142 mg/dL 70-99", "text/plain")},
                    headers=_h(a))
    assert 400 <= r.status_code < 500


# ── Iteration 3: QR token lifecycle and durable report results ───────────────


def _signed_report_with_qr_token(a: str) -> tuple[str, str]:
    """A signed-off report job owned by `a`, and a live QR download token."""
    import asyncio

    from app.db.session import AsyncSessionLocal
    from app.services.qr_service import generate_report_token

    job = _job(a)
    _report(job)
    _sign(job, a)

    async def mint():
        async with AsyncSessionLocal() as db:
            return await generate_report_token(job, db, patient_id=None)

    return job, client.portal.call(mint) if hasattr(client, "portal") and client.portal else asyncio.run(mint())


def test_qr_token_survives_a_failed_pdf_render(users, monkeypatch):
    import app.routes.qr_reports as qr_routes

    a, _ = users
    job, token = _signed_report_with_qr_token(a)

    async def boom(report_id, db):
        raise RuntimeError("renderer crashed")

    monkeypatch.setattr(qr_routes, "get_report_pdf_bytes", boom)
    assert client.get("/reports/download", params={"token": token}).status_code == 500
    monkeypatch.undo()

    ok = client.get("/reports/download", params={"token": token})
    assert ok.status_code == 200 and ok.headers["content-type"] == "application/pdf"
    assert client.get("/reports/download", params={"token": token}).status_code == 410  # now spent


def test_report_result_is_persisted_and_serves_after_celery_expiry(users):
    from app.workers.pipeline_tasks import analyze_report_task

    a, _ = users
    job = _job(a)
    patient = str(uuid.uuid4())
    result = {"status": "completed", "job_id": job, "patient_id": patient, "pipeline_type": "report",
              "panel_type": "cbc", "risk_score": 72.5, "risk_label": "high",
              "lab_values": [{"test": "Hemoglobin", "result": 6.1, "unit": "g/dL", "flag": "LOW"}],
              "anomalies": [{"field": "Hemoglobin", "direction": "LOW"}], "explanation": "Severe anaemia",
              "uncertainty_flag": False, "sources": []}
    analyze_report_task.on_success(result, job, (), {})
    analyze_report_task.on_success(result, job, (), {})  # idempotent: no duplicate row

    with Session(_engine) as s:
        rows = s.execute(text("SELECT risk_score, patient_id FROM reports WHERE celery_task_id=:j"), {"j": job}).all()
    assert len(rows) == 1 and rows[0][0] == 72.5 and str(rows[0][1]) == patient

    # Celery knows nothing about this id (as after result expiry): the owner
    # still sees it in patient history and can sign off + share it.
    r = client.get(f"/patients/{patient}/history", headers=_h(a))
    assert r.status_code == 200 and r.json()["history"][0]["risk_score"] == 72.5
    assert client.post(f"/api/v1/jobs/{job}/sign-off", json={"model_versions": "x"}, headers=_h(a)).status_code == 201
    assert client.get(f"/reports/{job}/qr", headers=_h(a)).status_code == 200


def test_single_job_status_reports_pipeline_outcome_and_survives_expiry(users, monkeypatch):
    import app.api.v1.routes.jobs as jobs_mod

    a, _ = users
    failed, expired = _job(a), _job(a)
    _set_status(expired, "completed")

    class _R:
        def __init__(self, state, result=None):
            self.state, self.result = state, result

    states = {failed: _R("SUCCESS", {"status": "failed", "error": "Quality gate: unreadable file"}),
              expired: _R("PENDING")}
    monkeypatch.setattr(jobs_mod, "AsyncResult", lambda tid, app=None: states[tid])

    r = client.get(f"/api/v1/jobs/{failed}", headers=_h(a)).json()
    assert r["status"] == "failed" and r["error"] == "Quality gate: unreadable file"
    r = client.get(f"/api/v1/jobs/{expired}", headers=_h(a)).json()
    assert r["status"] == "completed"


def test_worker_exception_text_is_not_returned_to_client(users, monkeypatch):
    import app.api.v1.routes.jobs as jobs_mod

    a, _ = users
    job = _job(a)

    class _Failed:
        state, result = "FAILURE", None
        info = RuntimeError("psycopg2.OperationalError: password authentication failed for user vaidya")

    monkeypatch.setattr(jobs_mod, "AsyncResult", lambda tid, app=None: _Failed())
    body = client.get(f"/api/v1/jobs/{job}", headers=_h(a)).text
    assert "password" not in body and "psycopg2" not in body
