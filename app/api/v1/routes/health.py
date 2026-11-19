import logging
from datetime import timezone, datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine

UTC = timezone.utc

log = logging.getLogger(__name__)

router = APIRouter()


@router.get("", summary="Basic health check")
async def health():
    """Static ok/service/version payload — no dependency checks."""
    return {
        "status": "ok",
        "service": "VaidyaAI",
        "timestamp": datetime.now(UTC).isoformat(),
        "version": "1.0.0",
    }


@router.get("/live")
async def live():
    """Liveness probe — process is up. No dependency checks."""
    return {"status": "alive", "timestamp": datetime.now(UTC).isoformat()}


@router.get("/ready")
async def ready():
    """Readiness probe — checks DB and Redis connectivity."""
    checks = await _run_readiness_checks()
    all_ok = all(check["ok"] for check in checks.values())
    payload = {
        "status": "ready" if all_ok else "not_ready",
        "checks": checks,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    return JSONResponse(status_code=200 if all_ok else 503, content=payload)


async def _run_readiness_checks() -> dict:
    checks: dict = {}

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:
        log.warning("Readiness: database check failed: %s", exc)
        checks["database"] = {"ok": False, "error": type(exc).__name__}

    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        client.ping()
        checks["redis"] = {"ok": True}
    except Exception as exc:
        log.warning("Readiness: redis check failed: %s", exc)
        checks["redis"] = {"ok": False, "error": type(exc).__name__}

    return checks
