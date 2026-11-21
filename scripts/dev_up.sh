#!/usr/bin/env bash
# Bring up local Postgres + Redis (no Docker) and the API + Celery worker for dev/E2E.
# Requires .venv and .env (see docs/PRD.md Appendix C). Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."
service postgresql start >/dev/null 2>&1 || true
redis-cli ping >/dev/null 2>&1 || redis-server --daemonize yes --dir /tmp >/dev/null
# shellcheck disable=SC1091
source .venv/bin/activate
set -a; . ./.env; set +a
alembic upgrade head
mkdir -p /tmp/logs
pgrep -f "uvicorn app.main:app" >/dev/null || nohup uvicorn app.main:app --port 8000 >/tmp/logs/uvicorn.log 2>&1 &
pgrep -f "celery -A app.workers.celery_app" >/dev/null || nohup celery -A app.workers.celery_app worker -Q reports,images,claims,celery -c 1 --pool=solo >/tmp/logs/celery.log 2>&1 &
until curl -sf localhost:8000/api/v1/health >/dev/null; do sleep 2; done
echo "API up on :8000"
