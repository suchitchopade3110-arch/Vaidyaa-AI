"""Shared SlowAPI limiter.

app/main.py registers it on `app.state.limiter`; routes decorate with
`@limiter.limit(...)` and must take a `request: Request` parameter.

Counters live in Redis so limits hold across uvicorn workers. If Redis is
unreachable the limiter falls back to per-process memory instead of
failing the request — rate limiting must never take login/refresh down
with it (a Redis outage would otherwise 500 every auth call and the UI
would log everyone out).

Behind a reverse proxy, run uvicorn with `--proxy-headers
--forwarded-allow-ips=<proxy ip>` so `request.client.host` is the real
client; otherwise every user shares the proxy's bucket.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    storage_uri=settings.RATE_LIMIT_STORAGE or settings.REDIS_URL,
    enabled=settings.RATE_LIMIT_ENABLED,
    in_memory_fallback_enabled=True,
    swallow_errors=True,
)

# Credential endpoints: generous for humans, tight for password guessing.
AUTH_RATE_LIMIT = settings.AUTH_RATE_LIMIT
