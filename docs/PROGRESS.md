# Progress

**Resume protocol:** read this file, `git log`, `plans/`, `reviews/`, `docs/DECISIONS.md`.

## Current phase: P0 — Baseline ✅ gate green → next: P1 (measuring stick)

| Item | State |
|---|---|
| Branch `v2/verified-lab-intelligence` from `main@ec5503f` | done |
| Hardening patch applied + PRD committed (`docs/PRD.md`) | done |
| Agent files `.claude/agents/*` | done |
| Env: Postgres 16 + Redis 7 (local services) | done |
| Env: python deps (torch from PyPI, +cpu pins filtered) | done |
| Gate: ruff clean · alembic head a1b2c3d4e5f6 · pytest 1216 passed (X-ray weights prefetched) · E2E 31 passed / 1 xfailed | **green** |
| Push + draft PR | done — PR #33 (draft) |

## Latest numbers
- Tests: 1216 passed, 0 skipped (with weights) · E2E 31 passed/1 xfail · coverage floor 38% · Eval: n/a (P1) · Review: n/a
