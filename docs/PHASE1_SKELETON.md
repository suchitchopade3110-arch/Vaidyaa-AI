# Phase 1 business-readiness skeleton

Tracks the seven Phase 1 items in the business-readiness requirements doc
(SEC-01, SEC-02, REG-01, REG-02, DPD-01, PLT-01, PLT-04). Started as
structural scaffolding for all seven; four (SEC-01, SEC-02, REG-02,
DPD-01) are now fully wired and enforced, not just modeled. REG-01,
PLT-01, and PLT-04 are still skeleton-only — see their sections below for
exactly what's real versus stubbed.

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

## REG-02 — mandatory clinician sign-off — **DONE**

- `app/models/sign_off.py` (`SignOff` table). `POST
  /api/v1/jobs/{job_id}/sign-off` writes a row, an `audit_logs` entry
  (action `report.sign_off`), 409s on a duplicate sign-off for the same
  `job_id`, and is restricted to `clinician`/`admin` roles.
- `app/services/pdf_report.py`'s `generate_report_pdf` takes a `signed`
  flag and stamps every page with a diagonal "DRAFT — NOT REVIEWED"
  watermark when `False` — done in-render (reportlab draws it directly),
  not as a separate PDF-bytes post-process. `app/routes/pdf_reports.py`
  looks up whether a `SignOff` row exists for the job and passes that in;
  it does **not** block the download outright — an authenticated
  clinician can still pull an unsigned copy, watermarked.
- `app/services/qr_service.py`'s `require_signed_off` **does** hard-block
  (403) minting a QR share token for an unsigned report — that path is
  un-authenticated and patient-facing, so it's the one held to "cannot
  be shared until sign-off" literally.
- **Interpretation call, not a doc bug:** the requirements doc's "no
  report can be exported to PDF... until sign-off" line and its
  "unsigned outputs are watermarked" line don't both read as literally
  true at once — a hard export block leaves nothing to watermark. Read
  as: authenticated PDF export gets a watermark, patient-facing QR
  sharing gets a hard block. Worth confirming with whoever owns REG-02
  if that split isn't what was intended.
- **Not done:** validating `job_id` refers to a real, completed job
  before accepting a sign-off (any string is currently accepted); gating
  a job's own "complete" status (as returned by the various job-status
  endpoints) on sign-off — that field describes pipeline completion, not
  clinical review, and conflating the two didn't seem like the right
  call without checking first.

## DPD-01 — consent capture and purpose binding — **DONE**

- `app/models/consent.py` (`ConsentRecord` table). `app/core/consent.py`:
  `require_valid_consent` now raises 403 (`CONSENT_REQUIRED`) instead of
  logging and letting the request through; `grant_consent` /
  `withdraw_consent` do the actual writes.
- `POST /api/v1/consent/grant` and `POST /api/v1/consent/withdraw`
  (`app/api/v1/routes/consent.py`) are the write side — didn't exist at
  all before this pass, so `require_valid_consent` had nothing to check
  against yet. Withdrawal sets `withdrawn_at` on the active record(s);
  it does not retroactively purge already-processed data (that's DPD-03).
- Wired into all three upload routes (`reports.py`, `images.py`,
  `claims.py`): each now calls `require_valid_consent(db, patient_id,
  purpose)` before dispatching, with a purpose constant per pipeline
  (`PURPOSE_REPORT_ANALYSIS` / `PURPOSE_IMAGE_ANALYSIS` /
  `PURPOSE_CLAIM_VERIFICATION`).
- **Known, deliberate gap:** the check only runs **when a `patient_id` is
  provided** — all three routes accept an optional patient_id, and
  there's nothing to bind consent to without one. Anonymous/test uploads
  bypass DPD-01 entirely. Whether anonymous uploads should be allowed at
  all under DPDP is a product decision, not resolved here.
- **Also not done:** these grant/withdraw endpoints are called by an
  authenticated clinician/staff user attesting that consent was obtained
  from the patient at intake — there's no patient-facing auth in this
  system for the data principal to grant consent directly. DPDP expects
  the principal's own action to be the consent event; a staff attestation
  is a real gap, not a technicality, and is flagged in
  `app/api/v1/routes/consent.py`'s docstring rather than presented as
  solved.

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
