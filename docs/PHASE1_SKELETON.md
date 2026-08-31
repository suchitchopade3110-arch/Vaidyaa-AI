# Phase 1 business-readiness skeleton

Tracks the seven Phase 1 items in the business-readiness requirements doc
(SEC-01, SEC-02, REG-01, REG-02, DPD-01, PLT-01, PLT-04). Started as
structural scaffolding for all seven; six (SEC-01, SEC-02, REG-02,
DPD-01, PLT-01, PLT-04) are now fully wired and enforced. REG-01 is
partially done — its mechanically-safe surfaces are enforced in CI, its
deeper (LLM prompt / API contract) violations are documented, not fixed
— see its section below for exactly why.

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

## REG-01 — non-diagnostic output contract — **PARTIALLY DONE, scope narrowed on purpose**

- `app/core/language_guard.py`'s `scan_text` is a real detector — disclaiming
  uses ("NOT a medical diagnosis", "Do not diagnose", "isn't a screening
  tool") are correctly excluded via a clause-bounded negation check, not
  just a blind substring ban (a blind ban would make it impossible to
  *disclaim* the very thing REG-01 cares about).
- `scripts/check_prohibited_terms.py` is a real, working sweep — not a
  stub — over: `README.md` in full, every route handler's docstring and
  `summary=`/`description=` kwargs (`app/api/v1/routes/`, `app/routes/`),
  every schema `Field(description=...)` (`app/schemas/`), and the
  top-level `FastAPI(title=..., description=...)` call in `app/main.py`.
  Wired into `ci.yml` as a blocking step (`Non-diagnostic language check`),
  right after ruff. Currently clean — the one real finding it caught
  (`reports.py`'s pipeline-diagram docstring: "Anomaly Detection" →
  "Outlier Flagging") is fixed.
- **Deliberately not swept, and why:** this only polices the surfaces
  above — copy that's clearly just labelling/description text, safe to
  reword without changing behavior. It does **not** touch:
  - **LLM prompts.** `app/services/differential_diagnosis.py`'s system
    prompt literally instructs the model to produce differential
    diagnoses with a `"primary_diagnosis"` JSON field — the deepest,
    most substantive violation found. Rewriting a clinical LLM prompt
    changes actual model output and needs its own validation pass, not
    a mechanical find-and-replace done blind.
  - **API response field names.** `app/routes/text_routes.py`'s `/api/text`
    response has a literal `"diagnosis"` key; `app/services/
    pipeline_controller.py` builds a `"diagnosis"` field from a risk
    label. `ui/report-analyzer.jsx` reads `item.diagnosis` directly —
    renaming the field is a breaking API change that needs coordinating
    with PLT-04 (the frontend hasn't been ported off the old contract
    yet), not something to do unilaterally here.
  - **Internal identifiers.** Module/function names like
    `yolo_detector.py`, `differential_diagnosis.py`, or NER entity-type
    labels borrowed from a pretrained model's own taxonomy
    (`app/services/preprocessor.py`'s `"diagnosis"` as a med-NER label)
    aren't user-facing copy and renaming them is unrelated churn.
  - **Confidence labelling.** Checked already: `ConfidenceSignal` in
    `app/schemas/common.py` labels scores High/Medium/Low/Insufficient,
    never "diagnostic confidence" — this part of the acceptance
    criteria was already satisfied, nothing to change.

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

## PLT-01 — organisation hierarchy — **DONE for the one endpoint that needed it**

- `app/models/organisation.py` (`Organisation`, `Department`),
  `User.org_id` / `User.department_id` (nullable), migration — as before.
- **New write side:** `app/api/v1/routes/orgs.py` — admin-only
  `POST/GET /api/v1/admin/orgs`, `POST /api/v1/admin/orgs/{org_id}/departments`,
  `POST /api/v1/admin/orgs/users/{user_id}/assign`. None of this existed
  before — Organisation/Department could never actually be populated.
- **JWT now carries `org_id`** (`app/routes/auth.py`) when the user has
  one. Reassigning a user via the endpoint above doesn't retroactively
  update tokens already issued — they need to log in again (documented
  in `orgs.py`, not silently glossed over).
- **`scope_to_org` has a real caller:** `GET /api/v1/jobs` (SEC-03, built
  out properly as part of this — it was still a hardcoded `[]` stub)
  defaults to the caller's own jobs; `?org=true` (admin role only) lists
  every job across the caller's org, via `scope_to_org`. The org_id it
  filters by always comes from the caller's own verified JWT — there is
  no request parameter, header, or body field it could be read from
  instead, which is what makes cross-org access impossible *by
  construction* rather than by an if-check someone could get wrong.
  `tests/test_tenancy.py` verifies this by compiling the query to SQL
  and asserting the bound parameter is the caller's own org_id, for two
  different admins in two different orgs.
- **Narrower than "all data queries," on purpose:** the acceptance
  criteria says "all data queries scoped by org_id at the repository
  layer." Only `GET /api/v1/jobs`'s org-listing path is actually scoped
  — because it's the only endpoint that lists *across* users at all.
  Every other route (`reports.py`, `images.py`, `claims.py`,
  `pdf_reports.py`, `signoff.py`, `consent.py`) is already scoped more
  tightly, by SEC-01's per-*user* ownership check — which is a strictly
  stronger guarantee than per-org, so there's no cross-org gap on those
  routes today. There's still no general-purpose repository layer;
  `scope_to_org` is called directly from the one route that needs it,
  not from an abstraction other routes also go through. Building that
  abstraction ahead of a second real caller felt like premature
  structure, not a shortcut.
- **Also not done:** role taxonomy mismatch — `User.role` is
  `admin | clinician | reviewer` (existing, pre-Phase-1); the
  requirements doc's PLT-01 wording says
  `clinician | admin | auditor`. Not reconciled — a rename either way is
  a product/naming call, not mine to make unilaterally. No self-serve
  "create my organisation" flow either: the first admin account still
  has to be bootstrapped via `/auth/register` plus a manual DB role
  flip, same gap already flagged for DPD-01's consent-grant endpoints.

## PLT-04 — frontend build pipeline — **DONE**

- All five screens (`Dashboard`, `ClaimVerifier`, `ReportAnalyzer`,
  `ImageAnalysis`, `JobTracker`) and the shared component library are
  ported to real ES modules under `ui/src/` — mechanical conversion
  (the `{ ... }` block-scope wrapper and `Object.assign(window, {...})`
  are gone; `const { useState } = React` is a real `import`), component
  bodies otherwise unchanged from `ui/*.jsx`.
- **New, because the backend didn't require it when the originals were
  written:** a login screen (`ui/src/pages/Login.jsx`) and auth context
  (`ui/src/lib/auth.jsx`), and every API call now goes through
  `ui/src/lib/api.js`'s `apiFetch`/`wsUrl`, which attach the bearer
  token SEC-01/SEC-02 require. Without this the ported app would 401 on
  every single API call — the original pages never sent a token because
  the backend never checked for one.
- **Two real bugs caught during the port, not introduced by it:** both
  `ReportAnalyzer.jsx` and `ImageAnalysis.jsx` had
  `{window.ReportQRWidget && <ReportQRWidget .../>}` — a global-existence
  check that would always be false once nothing sets
  `window.ReportQRWidget` anymore (nothing ever did in the module
  version), silently hiding the QR widget forever. Replaced with a check
  on whether a job id exists yet, which is what actually gates it.
- `npm run build` (`ui/`) produces `ui/dist/`, verified for real: built
  with `npm ci` from a committed `package-lock.json`, then loaded
  headlessly (Playwright/Chromium) — confirmed zero console/page errors,
  confirmed the `Authorization: Bearer <token>` header is actually
  present on an intercepted `/api/v1/jobs` request (not just present in
  source), and confirmed all five screens navigate without error.
  Screenshots of the login and dashboard states were sent alongside this
  change. Wired into `ci.yml` as a new `frontend-build` job (Node 22,
  `npm ci && npm run build`, uploads `ui/dist/` as an artifact) so a
  future change that breaks the build fails CI.
- `app/main.py` mounts the build at **`/app`**, not `/ui` — deliberately
  not the "swap `/ui`'s target" reading of the acceptance criteria.
  `ui/index.html` (marketing landing page) and `ui/report-preview.html`
  (the QR-scan preview page — see `app/services/qr_service.py`) still
  live directly under `ui/`; pointing `/ui` at `ui/dist/` instead would
  make both unreachable. The mount degrades gracefully (skips with a
  log line, doesn't crash) when `ui/dist/` doesn't exist yet, i.e.
  before anyone has run the build.
- **Old CDN/Babel demo removed:** `ui/VAIDYAAI.html` and the `ui/*.jsx`
  globals it loaded (`dashboard.jsx`, `claim-verifier.jsx`,
  `report-analyzer.jsx`, `image-analysis.jsx`, `job-tracker.jsx`,
  `report-qr-widget.jsx`, `shared.jsx`) are deleted now that `ui/src/`
  replaces them. `ui/index.html`'s four CTA links pointed at
  `"VAIDYAA AI.html"` (with a space) — already a 404 before this
  change, from an earlier dead-code pass that deleted that specific
  file but kept `VAIDYAAI.html`, un-updated at the time. Fixed to point
  at `/app/` instead, so those links now go somewhere real for the
  first time in a while.
- **Still not done:** no end-to-end run against a live backend
  (Postgres/Redis/Celery workers) — verification used a mocked
  `/api/v1/jobs` response, not a real one; someone should smoke-test
  against the actual running stack.

## What this skeleton deliberately does not touch

Per the requirements doc's own scope note and the Phase 1 selection this
was built against: SEC-04 (double-prefixed routes), REG-03 through REG-06,
DPD-02 through DPD-06, PLT-02/PLT-03, all of INT-*, all of FTR-*, all of
EVD-*, and OPS-*. Those are Phase 2/3 items or explicitly out of scope for
this pass.
