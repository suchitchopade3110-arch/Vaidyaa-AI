# Progress

**Resume protocol:** read this file, `git log`, `plans/`, `reviews/`, `docs/DECISIONS.md`.

## Current phase: P1 — Measuring stick (T17 review pending)

Plan: `plans/P1.md` (nami). Task state (T-numbers from plan §10):

| Task | State |
|---|---|
| T01 scaffolding (usopp) | done |
| T02–T10 clinical tables, GT, scenarios, layouts, renderings, generate.py (sanji) | done |
| T11–T13 adapters, matching, metrics, run.py, gate (zoro/sanji) | done |
| T14 CI jobs (`eval-gate`, `eval-full.yml`) | done (YAML validated only; first real run is on the PR) |
| T15 baseline | done — `eval/baseline.json` regenerated after F1/F2 fixes (measured, not tuned) |
| T16 zoro review (`reviews/P1-zoro.md`) | done — F1 HIGH, F2 MED fixed with tests; F3/F4 MED + LOW items logged |
| T15b full-profile run (dev+test+heldout aggregate) | running (`/tmp/logs/full2.log`); a pool-teardown hang that lost the first run was fixed (graceful `close()/join()`) |
| T17 independent review (`reviews/P1.md`) | pending |

P0 (baseline) is complete: ruff clean, alembic head `a1b2c3d4e5f6`, 1216 pytest passed, E2E 31 passed / 1 xfailed. Draft PR #33.

### Environment notes (container restarts drop services)
After any restart run `scripts/dev_up.sh` (Postgres, Redis, API, Celery). X-ray weights live in `~/.torchxrayvision` and may need re-fetching (`HF_HUB_OFFLINE=0`, see DECISIONS).

## Latest numbers
- Tests: 1216 passed (P0) · E2E 31 passed/1 xfail · Eval: v0 baseline recorded in `eval/report.md` (CI profile test split: native M1 20.45%, M2 75.0%; csv M1 0%; scan M1 28.3%; photo M1 45.9%) — targets fail by design at P1 · Review: n/a
