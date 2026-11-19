# Progress

**Resume protocol:** read this file, `git log`, `plans/`, `reviews/`, `docs/DECISIONS.md`.

## Current phase: P1 — Measuring stick (in progress)

Plan: `plans/P1.md` (nami). Task state (T-numbers from plan §10):

| Task | State |
|---|---|
| T01 scaffolding (usopp) | done, pushed (`4915c8b`) |
| T02–T04 clinical tables, GT rules, scenarios (sanji) | in progress — hit a weekly usage limit mid-T02 at ~10:50 UTC; resumed after reset; partial work is in the working tree |
| T05–T10 layouts, renderings, generate.py | pending |
| T11–T13 adapters, matching, metrics, run.py, gate | pending (zoro T11/T12) |
| T14 CI, T15 baseline, T16 zoro review, T17 independent review | pending |

P0 (baseline) is complete: ruff clean, alembic head `a1b2c3d4e5f6`, 1216 pytest passed, E2E 31 passed / 1 xfailed. Draft PR #33.

### Environment notes (container restarts drop services)
After any restart run `scripts/dev_up.sh` (Postgres, Redis, API, Celery). X-ray weights live in `~/.torchxrayvision` and may need re-fetching (`HF_HUB_OFFLINE=0`, see DECISIONS).

## Latest numbers
- Tests: 1216 passed (P0) · E2E 31 passed/1 xfail · Eval: not yet measured · Review: n/a
