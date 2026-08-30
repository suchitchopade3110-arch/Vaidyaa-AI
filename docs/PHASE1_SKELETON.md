# Phase 1 business-readiness skeleton

Structural scaffolding for the seven Phase 1 items in the business-readiness
requirements doc (SEC-01, SEC-02, REG-01, REG-02, DPD-01, PLT-01, PLT-04).
Models, migrations, and route/module shapes exist for all seven; the
enforcement logic that makes each one actually block something is mostly
still TODO, marked at each call site with a `# TODO(<ITEM-ID>)` comment.

This file is the map. Each item below links to where its pieces live and
states plainly what's real versus stubbed — read it before assuming
something is enforced just because a file with the right name exists.

## SEC-01 — per-user job ownership — **DONE**

- `app/models/async_job.py` (`AsyncJobRecord` table) + migration.
- `app/core/ownership.py`: `require_job_owner` (404s on missing/mismatched
  owner) and `record_job_ownership` (writes the row at submission).
- Wired into every route that takes a job id: submission routes
  (`reports.py`, `images.py`, `claims.py`) call `record_job_ownership`
  right after dispatch; status/result/pdf routes (`jobs.py`, `reports.py`,
  `images.py`, `claims.py`, `pdf_reports.py`) depend on
  `require_job_owner`; `websocket_routes.py`'s `/ws/{job_id}` does the
  same check manually (it can't use `Depends(get_current_user)` — the
  token arrives as a query param, not a header).
- **Known gap, not covered:** `images.py`'s `/image/{analysis_id}` route
  is keyed by an `ImageAnalysis` row PK, not a Celery task_id, and that
  table has no `user_id` column. Needs its own fix — see the TODO on that
  route.
- **Integration test still needed:** the acceptance criteria calls for
  "user A submits a job, user B requests it by ID, receives 404" as an
  automated test. Not added in this pass — `tests/` wasn't touched.

## SEC-02 — close unauthenticated endpoints — **DONE**

- `/yolo_outputs` static mount removed (separate cleanup pass).
  `GET /report/{job_id}/pdf` requires auth + ownership (now via
  `require_job_owner`, since SEC-01 landed). `POST /api/text` requires
  auth. `WS /ws/{job_id}` authenticates via a `?token=` query param and
  checks ownership before accepting the connection.
- **Not done:** accepting the WS token via Sec-WebSocket-Protocol as a
  fallback to the query param — TODO in `websocket_routes.py`.

## SEC-03 — job listing (Phase 2, not built here)

Noted only because SEC-01 unblocks it: `app/api/v1/routes/jobs.py`'s
`list_recent_jobs` still hardcodes `[]`; a TODO there points at
`AsyncJobRecord` as the read path once SEC-01's write side exists.

## REG-01 — non-diagnostic output contract

- **Real:** `app/core/language_guard.py` — the banned-term list and the
  `FINDING_REVIEW_LABEL` / `CONFIDENCE_LABEL` constants routes should use.
- **Not done:** nothing calls `contains_prohibited_term` yet.
  `scripts/check_prohibited_terms.py` is a CI-check stub with an empty
  surface list — the actual sweep (API descriptions, PDF headers,
  README.md) is unwritten, and no CI step calls the script.

## REG-02 — mandatory clinician sign-off

- **Real:** `app/models/sign_off.py` (`SignOff` table) and
  `POST /api/v1/jobs/{job_id}/sign-off` (`app/api/v1/routes/signoff.py`) —
  writes a row, returns it.
- **Not done:** no audit_logs entry alongside the sign-off; no route
  blocks PDF export, QR share, or "complete" status on a missing SignOff;
  `apply_draft_watermark` (`app/services/pdf_report.py`) is an unwired
  stub that returns its input unchanged.

## DPD-01 — consent capture and purpose binding

- **Real:** `app/models/consent.py` (`ConsentRecord` table) and
  `app/core/consent.py` (`require_valid_consent` — reads the table).
- **Deliberately permissive:** `require_valid_consent` logs a warning and
  returns `None` on a missing record instead of raising. There is no
  consent-capture UI/endpoint yet to write rows in the first place, so a
  hard 403 here would just break every existing upload. Flip it once (a)
  a capture flow exists and (b) submission routes pass real
  `data_principal_id`/`purpose` values through.

## PLT-01 — organisation hierarchy

- **Real:** `app/models/organisation.py` (`Organisation`, `Department`),
  `User.org_id` / `User.department_id` (nullable), and the migration for
  all of it.
- **Not done:** JWTs don't carry an `org_id` claim yet (no org onboarding
  flow to populate it from); `app/core/tenancy.py`'s `scope_to_org` helper
  exists but nothing calls it — there's no repository layer for it to sit
  in front of, since routes query models directly today. Cross-org
  isolation is **not** enforced by this skeleton.

## PLT-04 — frontend build pipeline

- **Real:** `ui/package.json`, `ui/vite.config.js`, `ui/index.dev.html`,
  `ui/src/main.jsx` — a working (if minimal) Vite + React scaffold that
  builds and runs.
- **Not done:** the real screens (`dashboard.jsx`, `report-analyzer.jsx`,
  `image-analysis.jsx`, `claim-verifier.jsx`, `job-tracker.jsx`,
  `shared.jsx`) are untouched — they're written for in-browser Babel
  (implicit globals, no `import`/`export`) and are not imported by
  `src/main.jsx`. Porting each to an ES module, and swapping `app/main.py`'s
  `/ui` mount from serving `ui/` directly to serving `ui/dist/`, is the
  rest of PLT-04.

## What this skeleton deliberately does not touch

Per the requirements doc's own scope note and the Phase 1 selection this
was built against: SEC-04 (double-prefixed routes), REG-03 through REG-06,
DPD-02 through DPD-06, PLT-02/PLT-03, all of INT-*, all of FTR-*, all of
EVD-*, and OPS-*. Those are Phase 2/3 items or explicitly out of scope for
this pass.
