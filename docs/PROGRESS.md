# Progress

**Resume protocol:** read this file, `git log`, `plans/`, `reviews/`, `docs/DECISIONS.md`.

## Current phase: P2 — Review flow (planning). P1 closed.

Plan: `plans/P1.md` (nami). Task state (T-numbers from plan §10):

| Task | State |
|---|---|
| T01 scaffolding (usopp) | done |
| T02–T10 clinical tables, GT, scenarios, layouts, renderings, generate.py (sanji) | done |
| T11–T13 adapters, matching, metrics, run.py, gate (zoro/sanji) | done |
| T14 CI jobs (`eval-gate`, `eval-full.yml`) | done (YAML validated only; first real run is on the PR) |
| T15 baseline | done — `eval/baseline.json` regenerated after F1/F2 fixes (measured, not tuned) |
| T16 zoro review (`reviews/P1-zoro.md`) | done — F1 HIGH, F2 MED fixed with tests; F3/F4 MED + LOW items logged |
| T15b full-profile run (dev+test+heldout aggregate) | done — committed `eval/report.md` + `eval/metrics.json` are the post-review full run (`--profile full`); `eval/baseline.json` is the CI-profile run |
| T17 independent review (`reviews/P1.md`) | done — 6.0/10, 6 MED + 7 LOW; all 6 MED and LOW-1/2/4/5/6/7 fixed with tests, LOW-3 and T16 carry-overs deferred (DECISIONS 2026-11-21) |

P1 closed 2026-11-22: independent review 6.0/10 (0 HIGH, 6 MED, 7 LOW), all MED fixed with tests; 523 eval tests, full suite 1770 passed/1 xfailed; app/ unchanged. Two legacy `tests/test_rag_integration.py` failures (missing `verify_claim`, 401 unauth) need `app/` changes: P2/P3 item.

P0 (baseline) is complete: ruff clean, alembic head `a1b2c3d4e5f6`, 1216 pytest passed, E2E 31 passed / 1 xfailed. Draft PR #33.

### Environment notes (container restarts drop services)
After any restart run `scripts/dev_up.sh` (Postgres, Redis, API, Celery). X-ray weights live in `~/.torchxrayvision` and may need re-fetching (`HF_HUB_OFFLINE=0`, see DECISIONS).

## Latest numbers
- Tests: 1216 passed (P0) · E2E 31 passed/1 xfail · Eval (post-review, measured not tuned; targets fail by design at P1): CI baseline = test split, native M1 20.71% (417/2014), M2 67.57% (25/37), M2_post 62.16% (23/37), M3 51.86%, M4 0.15%, M5 80.26%; full run (`eval/report.md`) heldout aggregate native M1 26.28%, M2 64.44% (29/45), M2_post 60.00% (27/45), M3 37.29%, M5 72.73%; scan M2 55.56%, photo M2 35.56%. Review: P1 6.0/10 (`reviews/P1.md`), all MED fixed

- P1 final (full run, native): test M1 20.71%, M2 67.57%, M2_post 62.16%; heldout M1 26.28%, M2 64.44% (aggregate). Targets fail by design; P2+ move them.
