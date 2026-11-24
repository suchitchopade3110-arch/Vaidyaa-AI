# VaidyaAI v2 — Product Requirements Document

**Product:** VaidyaAI — verified lab-report intelligence for clinicians and patients
**Repo:** `suchitchopade3110-arch/Vaidyaa-AI`
**Baseline:** git `HEAD` + `vaidyaa-ai-hardening.patch` (115 files; see §2)
**Executor:** Claude Code, working autonomously. The product owner will not answer questions — every decision has a default in this document.
**Version:** 2.0 · 2026-11-19

---

## 0. How Claude Code must use this document

1. Read this whole file before writing any code. Then read `README.md`, `docs/PHASE1_SKELETON.md` and the files listed in Appendix B.
2. Work through the phases in §11 **in order**. Each phase has a gate; do not start the next phase until the gate is green.
3. **Never ask the user anything.** Where something is ambiguous, pick the most conservative, patient-safe option, write the decision into `docs/DECISIONS.md` (one line: date, decision, reason), and continue.
4. Keep `docs/PROGRESS.md` current: the phase you're on, what's done, what's next, and the latest test, eval and review numbers. After any interruption (usage limit, crash, new session), resume from `docs/PROGRESS.md` and `git log`, not from memory.
5. Commit at least once per completed requirement on the working branch (§11.1). Never commit to `main`, never force-push, never merge.
6. Delegate work using the crew in §11.2. **Do not use Fable models anywhere.**
7. The finish line is §3 and Appendix E: the evaluation metrics met on the held-out set, and an independent review scoring at least 9/10.

---

## 1. Product summary

### 1.1 What VaidyaAI is
A clinical **decision-support** web app. A clinician uploads a lab report (PDF, scan, photo, CSV, or a FHIR/HL7 file). VaidyaAI:
1. extracts every result (test, value, unit, reference range, flag), showing where on the page each one came from;
2. has the clinician **confirm or correct** the extracted values;
3. scores the confirmed values: flags, critical alerts, headline risk, trends against the patient's earlier reports;
4. gives the clinician a structured summary, and the patient a plain-language explanation in English, Hindi or Tamil;
5. is signed off by the clinician, after which a single-use QR link lets the patient view or download the report.

Claim verification (RAG) remains a supporting feature. Chest X-ray analysis is labelled **Experimental** (§E8).

### 1.2 Why this direction
Five independent reviews of the rule-based reader scored it 3 → 4 → 5 → 4 → 4. Each round fixed every reported layout, and the next reviewer's fresh layouts failed again: about 63% of native-text PDFs and 6 of 8 scanned images were read fully correctly. Rule-based parsing of arbitrary report layouts doesn't converge. v2 changes the safety model:

> **No extracted value can influence a score, alert, summary, sign-off or share link until a clinician has confirmed it, and anything the engine is unsure about is highlighted for review.**

The engine still has to be as accurate as possible (fewer corrections means a better product), but correctness is now guaranteed by review, and the engine is judged on **silent errors**: a wrong value that is *not* flagged for review.

### 1.3 Users
| User | Needs |
|---|---|
| **Clinician** (primary) | Fast, trustworthy extraction; obvious critical values; a quick review step; trends; sign-off. |
| **Patient** (via QR) | A readable explanation in their language; what to discuss with their doctor; no diagnosis. |
| **Admin** | User management, audit log, bias/quality dashboards. |

---

## 2. Current state (baseline after applying the patch)

### 2.1 What already works (keep it working)
- FastAPI API, Celery workers, Postgres, Redis and ChromaDB. JWT auth (access + refresh, rotation, 401/403 split), rate limiting with an in-memory fallback, per-user ownership on every job, report, patient-history, QR and WebSocket route.
- Clinician sign-off is required before QR sharing. QR tokens are single-use and burned atomically after the PDF renders. Completed reports persist to the `reports` table.
- Lab engine `app/services/lab_engine/` (`analytes.py`, `units.py`, `tokenizer.py`, `evaluate.py`, `models.py`):
  - ~111 analytes with exact token-set matching;
  - unit conversion with SI equivalents;
  - the printed range takes precedence over registry ranges;
  - sex/age are never assumed;
  - critical thresholds, and a risk floor that cannot be lowered;
  - headline "critical" only when a critical value exists;
  - interpretive category lists are not used as ranges;
  - cumulative (previous/current) columns, two tests per line, method phrases and digits inside names are handled.
- Urinalysis is merged as an additional section; it no longer replaces the blood-test engine.
- PaddleOCR rebuilds rows and columns from bounding boxes; the angle classifier is off (it flipped digits).
- Chest X-ray: whole-film classification matching torchxrayvision's reference output; a plausibility gate (contrast, texture, entropy, autoencoder); borderline 0.50–0.60 findings are not reported as detected.
- UI (`ui/VAIDYAAI.html`, in-browser Babel): login, report/claim/image pages, sex/age inputs, sign-off, QR widget, dashboard.
- Tests: **1216** unit/API tests, coverage 61%, 31 live E2E tests, an acceptance corpus (`tests/fixtures/lab_cases`, `tests/fixtures/lab_pdfs`) and a generalisation set (393 lines).
- CI: `.github/workflows/ci.yml` runs lint, import check, migrations, a model-weight prefetch, and the full suite against Postgres + Redis, with a coverage floor of 38%.

### 2.2 Audit findings still open
The latest independent review (Appendix A) scored the baseline **4/10**. Open findings: N1–N5 HIGH, N6 (done in baseline), N7 (done in baseline), N8–N14 MED, N15–N20 LOW. Each is a requirement in §7 (E2 / E8 / E9).

---

## 3. Goals and success metrics

All metrics are measured by `make eval` (§E5) on the **held-out** split, which development never trains, tunes or writes expectations against.

| ID | Metric | Target |
|---|---|---|
| M1 | **Silent-error rate**: extracted rows that are wrong (name, value, unit, range or flag) **and** not marked `needs_review` ÷ all ground-truth rows | **≤ 0.5%** native text; **≤ 1.5%** scan/photo |
| M2 | **Critical recall**: ground-truth critical values that end up either flagged CRITICAL or marked `needs_review` | **100%** (any miss fails the gate) |
| M3 | Row extraction accuracy before review (all fields correct) | **≥ 92%** native; **≥ 80%** scan; **≥ 70%** phone photo |
| M4 | False-critical rate (rows flagged CRITICAL whose ground truth isn't critical) | **≤ 0.5%** of rows |
| M5 | Headline risk on a **confirmed** report equals the ground-truth headline | **≥ 98%** |
| M6 | Normal reports whose pre-review state is "ready to confirm" (not "unknown") | **≥ 90%** |
| M7 | Review burden: share of rows marked `needs_review` on native-text reports | **≤ 15%** |
| M8 | Structured inputs (CSV, FHIR, HL7): row accuracy | **100%** on the conformance set |
| M9 | Independent review score (Appendix E) | **≥ 9/10** |
| M10 | Security regressions (§E9 probe suite) | **0** |
| M11 | p95 processing time, 2-page native PDF, CPU only | **≤ 15 s** (scan ≤ 45 s) |

---

## 4. Non-goals
- No diagnosis. VaidyaAI never states a disease as fact; it states findings and suggests discussing them with a doctor.
- No treatment or dosing advice.
- No new imaging capabilities. X-ray stays Experimental; CT/MRI analysis is out of scope.
- No mobile app. The responsive web UI is enough.
- No real patient data in the repo, ever. All fixtures are synthetic (§E5).
- No impersonation of real labs. Templates copy **layout structure only**, with fictional lab names, addresses, logos and phone numbers (§E3).
- No org tenancy (PLT-01), consent capture (DPD-01) or full HIPAA audit. These stay declared limitations in the README.

---

## 5. Key user flows

### F1 — Upload → review → confirm → result (primary)
1. The clinician uploads a report and optionally enters patient ID, sex and age.
2. A job is queued. The UI shows progress over the WebSocket.
3. When extraction completes, the job enters **`awaiting_review`** and the UI opens the **Review screen** (§E1):
   - the left pane shows the rendered source page, with each extracted row's region highlighted;
   - the right pane shows an editable table: test, value, unit, range, flag, confidence and a review badge;
   - rows marked `needs_review` are listed first and must each be explicitly accepted or edited;
   - the clinician can add a missed row, delete an invented row, and change the specimen (serum/urine/CSF/other);
   - **Confirm** is disabled until every `needs_review` row has been acted on.
4. On Confirm, the server re-scores the **confirmed** rows with the lab engine. Flags, criticals, headline risk, trends and summaries are computed only now.
5. The result screen shows the critical banner (if any), the abnormal list, trends, the clinician summary and the patient summary preview.
6. Sign-off (existing) unlocks the QR link.

### F2 — Structured import
A CSV, FHIR R4 `Bundle`/`DiagnosticReport`+`Observation` JSON, or HL7 v2 `ORU^R01` file is parsed deterministically. Rows arrive with `confidence=1.0` and skip `needs_review`, unless the unit is unrecognised or no range is given. Then F1 from step 3, where the review screen just needs one click.

### F3 — Patient view (QR)
The patient opens a single-use link and sees a plain-language summary in their chosen language (EN/HI/TA), the abnormal results explained simply, and the standard safety text. They can download the PDF.

### F4 — Trends
The patient-history page shows a chart per analyte across that user's confirmed reports for the same patient, with reference bands.

---

## 6. Safety invariants (each must be enforced by an automated test)

| ID | Invariant |
|---|---|
| S1 | No score, flag, alert, summary, sign-off or QR may be produced from **unconfirmed** extraction. (Structured imports count as confirmed only when every row is `confidence=1.0` and not `needs_review`.) |
| S2 | Every row the engine is not certain about is `needs_review=true`, with a human-readable `review_reason`. |
| S3 | A ground-truth critical value can never end up both not CRITICAL and not `needs_review` (M2). |
| S4 | The printed range always wins over the registry range. Sex/age-specific registry ranges are used only when sex/age are known; there are no adult registry ranges under 18 unless an age rule exists. |
| S5 | Serum/blood criticals never apply to non-blood specimens (urine, CSF, fluid). Specimen comes from section headers or the clinician's choice. |
| S6 | No critical alert is ever based on an **assumed** unit. Unit-less values in count or ambiguous analytes → `needs_review`. |
| S7 | Headline "critical" requires at least one confirmed critical value. Headline "low"/"normal" requires zero abnormal confirmed values and zero unresolved review rows. |
| S8 | Patient-facing text never names a diagnosis as fact, never gives dosing, and always says to consult the doctor. It is template-generated (no free-text LLM output shown to patients). |
| S9 | Every confirmed value keeps its provenance (source page, region, original text) and an audit trail of edits (who, when, old → new). |
| S10 | Raw exception text, SQL or PHI never appears in API responses, WebSocket messages or patient views. |
| S11 | Every route that touches PHI requires auth and ownership; 404 for other users' data (existing — keep green). |

---

## 7. Requirements by epic

Each requirement has an ID, and each acceptance criterion (AC) must map to at least one test. Put new tests in the file named in brackets.

### E0 — Baseline and branch
- **FR-0.1** Create branch `v2/verified-lab-intelligence` from the current `main`. Apply `vaidyaa-ai-hardening.patch` with `git apply --whitespace=nowarn`. Commit as "Apply hardening baseline (rounds 1–5)".
- **FR-0.2** Bring up the dev environment (Appendix C) and make the full suite, ruff, migrations and the E2E suite pass locally.
- **FR-0.3** Add `docs/PROGRESS.md` and `docs/DECISIONS.md`. Add `dump.rdb`, `.ruff_cache/`, `data/yolo_outputs/` and `uploads/` to `.gitignore` if missing.
- **AC:** all of the above green; recorded in `docs/PROGRESS.md`.

### E1 — Extraction Review (human-in-the-loop) — **highest priority**

**Backend**
- **FR-1.1 New job states.** `queued → extracting → awaiting_review → scoring → completed | failed | low_quality`. Structured imports with every row certain may go straight to `scoring`. Store the state on `async_jobs.status`, and keep the WebSocket and job-status routes accurate.
- **FR-1.2 Data model** (Alembic migration, reversible):
  - `extracted_rows`: `id` UUID PK, `job_id` FK → `async_jobs.id`, `row_index`, `name_raw`, `analyte_key`, `value_raw` text, `value_num` float null, `qualifier`, `text_value`, `unit_raw`, `ref_low`, `ref_high`, `ref_text`, `printed_flag`, `specimen` (`serum|urine|csf|fluid|other|unknown`), `confidence` float 0–1, `needs_review` bool, `review_reason` text, `source_page` int, `source_bbox` JSON `[x0,y0,x1,y1]` in page points, `source_text` text, `origin` (`text|ocr|column|csv|fhir|hl7|manual`), `status` (`pending|accepted|edited|deleted|added`), timestamps.
  - `extraction_edits`: `id`, `row_id`, `user_id`, `field`, `old_value`, `new_value`, `at`. This is the S9 audit trail.
  - `sign_offs.job_id` gets a **unique** constraint (N17). Before adding it, the migration de-duplicates (keeping the earliest row).
- **FR-1.3 Extraction output.** The report task stops after extraction: it writes `extracted_rows`, sets `awaiting_review` and **does not** score. Each row records why it needs review. Minimum triggers:
  - unknown analyte;
  - unit missing or unrecognised;
  - no range and no registry range usable for this sex/age;
  - several candidate values (cumulative or ambiguous);
  - OCR confidence < 0.90 on any token of the row;
  - printed flag disagrees with the computed flag;
  - value outside 0.1×–10× the expected range;
  - critical-looking value;
  - unknown specimen in a mixed report;
  - row produced by the column parser or by reassembly of a split row;
  - a numeric line the engine could not parse (shown as an "unread line" row the clinician can convert or dismiss).
- **FR-1.4 API** (all owner-only, 404 otherwise):
  - `GET /api/v1/jobs/{id}/extraction` → rows, page count, warnings, unread lines, review counts.
  - `GET /api/v1/jobs/{id}/pages/{n}.png?scale=` → rendered source page (PDF via PyMuPDF; images as-is), with cache headers.
  - `PATCH /api/v1/jobs/{id}/extraction/rows/{row_id}` → edit fields or set `status`; writes `extraction_edits`.
  - `POST /api/v1/jobs/{id}/extraction/rows` → add a manual row.
  - `POST /api/v1/jobs/{id}/extraction/confirm` → 409 if any `needs_review` row is still `pending`. Otherwise it enqueues scoring of the confirmed rows (`accepted|edited|added`, never `deleted`) and returns 202.
  - Scoring reuses the lab engine (`analyze_lab_rows`) and the existing risk floor, trends, ensemble and summaries, on confirmed rows only.
- **FR-1.5 Sign-off and QR** additionally require state `completed` from a confirmed extraction (S1).

**UI** (`ui/` stays in-browser Babel; keep the existing style system in `shared.jsx`)
- **FR-1.6 Review screen** as in F1 step 3:
  - keyboard friendly: ↑/↓ moves between rows, Enter accepts, E edits;
  - clicking a row scrolls and highlights its region on the page image;
  - the review-reason chip explains why ("Unit not printed", "Two values on this line", "Looks critical — please verify");
  - a counter reads "N of M need review"; the Confirm button carries an explanatory tooltip;
  - accessible: real `<button>` elements with the `disabled` attribute, labels, and visible focus.
- **FR-1.7** The dashboard shows the `awaiting_review` count, and jobs in that state link to the Review screen.

**AC (tests: `tests/test_extraction_review.py`, plus browser E2E in `tests/e2e_ui/`)**
- AC-1.1 Scoring endpoints and the QR route return 409/403 before confirm (S1).
- AC-1.2 Confirm returns 409 while any `needs_review` row is pending; it succeeds after accept or edit.
- AC-1.3 An edit changes the scored result. For example, editing K from 4.2 to 6.9 produces a CRITICAL alert and a critical headline.
- AC-1.4 A deleted row never appears in results, summaries or the PDF.
- AC-1.5 The audit trail records each edit with user and timestamp.
- AC-1.6 The page image's bbox matches the row's source text for native-text PDFs (PyMuPDF word boxes) and OCR (Paddle boxes).
- AC-1.7 Another user gets 404 on every new endpoint.
- AC-1.8 Playwright UI test: upload → review → edit one value → confirm → result shows the edited value → sign-off → QR renders.

### E2 — Lab engine: close the audit (Appendix A has full reproductions)
- **FR-2.1 (N1) Column parser.**
  - It may never replace the text path wholesale. Derive column bands from the header row's word positions, not fixed x-bands.
  - Keep the printed range text. Apply Previous/Current/date header roles (reuse `tokenizer._header_roles`).
  - Stop treating "ESR…" and similar lines as section headings, and stop turning headers/footers into rows.
  - When the column and text paths disagree on a row, use the text path and mark the row `needs_review`.
  - Add portrait A4 fixtures where the parser **is** accepted.
- **FR-2.2 (N2) Method/technology column.** Drop cells under METHOD/TECHNOLOGY/TECHNIQUE headers during reassembly. Values on an orphan name line ("GLUCOSE (PP, 2 Hrs)") are never accepted. Method words (CLIA, GOD-POD, HPLC, ISE, …) are never row names.
- **FR-2.3 (N3) Specimen context.** Section headers (URINE …, CSF …, FLUID …, SPOT …, 24-HR …) set the specimen of the following rows until the next section. Specimen is part of analyte resolution: urine sodium, urine creatinine, CSF glucose and CSF protein are separate analytes with their own ranges or none, and **no serum criticals** (S5).
- **FR-2.4 (N4) Unit-less counts.**
  - Read units from column headers ("Result (x10^3/uL)").
  - Otherwise infer the scale from the printed range (a value of 7.2 against 4.0–11.0 is in thousands).
  - If neither works → `needs_review`. Never fire a critical from an assumed unit (S6).
  - OCR-garbled units ("KuL") → `needs_review`.
- **FR-2.5 (N8) Flag cells.** Stop absorbing tail cells at a flag cell that precedes a name cell. Support flag-first layouts.
- **FR-2.6 (N9) Titres.** Parse `1:160`-style results as `qualifier=ratio` with a printed cut-off if present, else `needs_review`. Never drop them silently.
- **FR-2.7 (N10) Invented rows.**
  - A row must have a matched analyte, or a two-sided range, or a recognised unit **and** a name that isn't prose.
  - Extend the interpretive vocabulary: "not calculated", "unable to calculate", "diagnosing", "interpretation", "note", "comment", "S/Co", "cut-off index", "reference: ADA".
- **FR-2.8 (N11) Form-style reports.** Carry "Test Name: X" / "Investigation: X" into the following "Result: …" row.
- **FR-2.9 (N12) Urinalysis.** Show engine rows instead of the legacy regex table. Anchor `clinical_ner_service` labels to line starts so "Ph: 0422-2345678" (a phone number) can never become pH. Remove the junk "blood=count" merge.
- **FR-2.10 (N14) OCR headers.** Tolerate glued or garbled header tokens (fuzzy match on previous/current/result/unit/range). When ambiguous, warn on **every** candidate value, including the current one.
- **FR-2.11 (N15) Footers.** Phone numbers, toll-free numbers, turnaround times, "Page x of y", NABL/CAP/accreditation lines, addresses and signatures are never rows and never count as unread lines.
- **FR-2.12 (N16) Printed flag with a one-sided range** ("HbA1c 7.8 H, <5.7 % of total Hgb"): honour the printed H/L when the computed flag is UNKNOWN; if they conflict → `needs_review`.
- **FR-2.13** Every rule in E2 emits `needs_review` + `review_reason` whenever it is uncertain (S2).
- **AC (tests: `tests/test_lab_engine.py`, `tests/test_specimen.py`):** each finding's Appendix A reproduction becomes a fixture with clinically derived expectations. All existing acceptance and generalisation tests stay green.

### E3 — Lab template registry
- **FR-3.1** `app/services/lab_engine/templates/`: a template = detection signals (header tokens, column titles and order, section names, footer patterns, page geometry) + a column map + quirks. `detect_template(pages) -> (template_id, confidence)`.
- **FR-3.2** Ship **8 templates** modelled on common layout families, with **fictional** lab names (S8 / §4):
  - IN-A (Dr Lal-style portrait, Method column);
  - IN-B (SRL/Agilus-style, Technology column, "Bio. Ref. Interval");
  - IN-C (Metropolis-style, interpretive tables);
  - IN-D (Thyrocare-style cumulative/landscape);
  - IN-E (hospital-lab two-panel);
  - US-A (Quest/LabCorp-style flag column, "Reference Interval");
  - UK-A (NHS-style, SI units, "Normal range" with units in brackets);
  - GEN-CSV.
- **FR-3.3** A detected template drives column assignment; unknown layouts fall back to the generic engine. Template rows are still validated by the analyte/unit/range rules.
- **AC (`tests/test_templates.py`):** detection ≥ 98% on the template split of the corpus; row accuracy on detected templates ≥ 98% native, ≥ 90% scan.

### E4 — Structured inputs
- **FR-4.1** CSV: keep the current structured path, and add a mapping UI hint when headers are unrecognised (falls back to review).
- **FR-4.2** FHIR R4: accept a `Bundle` or a `DiagnosticReport` with contained or referenced `Observation`s. Map `code` (LOINC first, then display text), `valueQuantity` (UCUM unit), `referenceRange`, `interpretation`, `specimen` and patient `gender`/`birthDate` (age). Unknown LOINC codes → match by display; else `needs_review`.
- **FR-4.3** HL7 v2.x `ORU^R01`: parse PID (sex, DOB), OBR, OBX (value type NM/ST/SN, units, reference range, abnormal flags). Use a small dependency-free parser, or `hl7` from PyPI if license and size are OK (record the choice in DECISIONS).
- **FR-4.4** Upload route accepts `.json` (FHIR) and `.hl7`/`.txt` (HL7, detected by `MSH|`). Validate size and type as for other uploads.
- **FR-4.5** A LOINC map for every registry analyte (`analytes.py` gets a `loinc` field). Source codes from public LOINC knowledge and record ambiguities in DECISIONS.
- **AC (`tests/test_structured_inputs.py`):** a conformance set of ≥ 30 FHIR and ≥ 30 HL7 messages (synthetic) gives 100% row accuracy (M8), including UCUM unit conversion, sex/age from the patient resource, and specimen.

### E5 — Evaluation corpus and harness (the measuring stick)
- **FR-5.1 Generator** `tools/eval/generate.py`, deterministic by seed, creates synthetic reports from the E3 templates plus ≥ 10 additional generic layouts:
  - ≥ 400 reports total, 1–3 pages each;
  - realistic value distributions: normal, mild, severe and critical; paediatric, elderly, and unknown sex/age; SI and conventional units; qualifiers; qualitative serology; urine and CSF sections; cumulative columns; footers, method columns, interpretive tables and form-style single tests.
- **FR-5.2 Renderings** per report:
  - native PDF;
  - image-only scanned PDF at 150 dpi with scan noise;
  - phone-photo PNG (perspective warp ≤ 8°, uneven lighting, blur σ ≤ 1.2, JPEG q 60–85);
  - a 90°-rotated variant for 5% of reports.
- **FR-5.3 Ground truth** JSON per report: every row (name, analyte_key, value, unit, range, specimen, flag, is_critical), the expected headline, and the expected `needs_review` minimum set (rows that are genuinely ambiguous on the page).
- **FR-5.4 Splits.**
  - `dev` (60%): tune against this freely.
  - `test` (20%): CI gate.
  - **`heldout` (20%)**: different seed, plus 3 layout families that are **excluded** from dev/test. Never look at its failures to write rules. Only aggregate metrics may be read.
- **FR-5.5 Harness** `tools/eval/run.py` (`make eval`) computes M1–M7 per split and per rendering, writes `eval/report.md` and `eval/metrics.json`, and exits non-zero if the CI-gate thresholds fail. Keep run time ≤ 20 min on CPU; allow `--sample`.
- **FR-5.6 CI:**
  - a new job `eval-gate` runs `test` split on native + 25% sample of scans;
  - nightly (`schedule:`), the full run including `heldout`;
  - store `eval/metrics.json` as an artifact.
- **AC:** the harness is reproducible: same seed, same metrics. `eval/report.md` checked in at each phase end.

### E6 — Patient-friendly summaries and trends
- **FR-6.1** Template-based summaries (S8). `app/services/summaries/` holds phrase tables per analyte and direction × severity, in `en`, `hi` and `ta` (UTF-8 JSON). The clinician summary is concise and technical; the patient summary is plain language at a reading age of about 12. No LLM text is shown to patients.
- **FR-6.2** Fixed safety lines in each language: "This is not a diagnosis…", "Please discuss these results with your doctor", and the emergency guidance for critical values.
- **FR-6.3** Language selection on the patient QR page and in the PDF; default from the browser, with a manual switch.
- **FR-6.4** The trend endpoint already exists (`/patients/{id}/trend/{test}`): make it use confirmed rows and canonical units. The UI chart (inline SVG, no new deps) shows points, the reference band, and the date of each value.
- **AC (`tests/test_summaries.py`):** every registry analyte has phrases in all 3 languages (test fails on any missing key); no diagnosis words in patient text (deny-list test); a Playwright snapshot of the patient page in each language; the trend endpoint returns canonical units across mixed-unit reports.

### E7 — Provenance and confidence
- **FR-7.1** Every row keeps `source_page`, `source_bbox`, `source_text` and `confidence`:
  - native PDFs: PyMuPDF word boxes, confidence 1.0 unless reassembled;
  - OCR: the minimum token confidence;
  - structured inputs: 1.0.
- **FR-7.2** The result page and PDF show "from page N" and a confidence dot per value. Hovering shows the source text.
- **AC:** covered by AC-1.6, plus a PDF export test that the provenance column is present.

### E8 — Imaging (Experimental)
- **FR-8.1** The UI and API label X-ray analysis **Experimental — not for clinical use**. Results are never included in the patient summary.
- **FR-8.2 (N13)** Tighten the plausibility gate:
  - add a chest-X-ray vs other-modality check (CT/MR slice, photo, hand/limb film) using simple, robust signals: DICOM `Modality` tag when present; aspect ratio; intensity histogram shape; the autoencoder score with a calibrated threshold from a small labelled set of synthetic and public-domain non-CXR images;
  - add an orientation/polarity check (rotated or inverted film → `needs_review`-style rejection with a reason);
  - calibrate thresholds on fixtures and record them in DECISIONS.
- **FR-8.3 (N20)** Don't reject wide-aspect real films as screenshots unless other signals agree.
- **AC (`tests/test_clinical_safety.py`):** CT slice, MR slice, grayscale photo, hand film, rotated and inverted CXR are all rejected with reasons; the NIH CXR fixture is still accepted and classified Cardiomegaly.

### E9 — Security, platform and hygiene
- **FR-9.1 (N17)** Unique sign-off per job (FR-1.2). Concurrent sign-off returns exactly one 201 and the rest 409 (a real concurrency test against uvicorn, not TestClient threads).
- **FR-9.2 (N18)** WebSocket failure messages are generic (already done in the baseline). Keep the test.
- **FR-9.3 (N19)** README accuracy: limitations, admin semantics, endpoints, and the new review flow.
- **FR-9.4** In `APP_ENV=production`, `/docs`, `/redoc`, `/openapi.json` and `/metrics` require admin auth or are disabled.
- **FR-9.5** Delete the dead legacy modules: `app/workers/{claim,image,report}_tasks.py`, `tasks_rag.py` if unused, and their registrations in `celery_app.py`. First verify with a grep and the import check that nothing live imports them.
- **FR-9.6** Security probe suite `tests/test_security_api.py`: extend it to every new endpoint (ownership 404, auth 401, role 403, confirm gating).
- **AC:** M10 = 0 regressions; CI green.

### E10 — Docs, demo and release
- **FR-10.1** README: product overview, an architecture diagram (Mermaid), a setup guide (Docker and local), the review-flow walkthrough with screenshots (Playwright-generated), the eval results table from `eval/report.md`, and honest limitations.
- **FR-10.2** `docs/ARCHITECTURE.md` (components, data flow, safety invariants → tests map), `docs/EVALUATION.md` (corpus, splits, metrics, latest numbers).
- **FR-10.3** `scripts/demo.sh` seeds a demo user and 3 sample reports (normal, abnormal, critical) and prints the URLs.
- **FR-10.4** Update `CHANGELOG.md` with a v2.0 entry.

---

## 8. API summary (new and changed)

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/jobs/{id}/extraction` | rows + warnings + review counts |
| GET | `/api/v1/jobs/{id}/pages/{n}.png` | rendered source page |
| PATCH | `/api/v1/jobs/{id}/extraction/rows/{row_id}` | edit / accept / delete; audit |
| POST | `/api/v1/jobs/{id}/extraction/rows` | add manual row |
| POST | `/api/v1/jobs/{id}/extraction/confirm` | 409 until reviewed; enqueues scoring |
| POST | `/api/v1/analyze/report/{type}` | also accepts FHIR `.json` and HL7 `.hl7`/`.txt` |
| GET | `/api/v1/jobs/{id}` | new states (FR-1.1) |
| POST | `/api/v1/jobs/{id}/sign-off` | requires a confirmed, completed job |
| GET | `/reports/preview?token=&lang=` | patient view in en/hi/ta |
| GET | `/patients/{id}/trend/{test}` | confirmed rows, canonical units |

Error format unchanged: `{"error": {"code", "message", "request_id"}}`.

---

## 9. UI requirements (summary)
- Review screen (FR-1.6), dashboard review queue (FR-1.7), result-page provenance (FR-7.2), trends chart (FR-6.4), patient page language switch (FR-6.3), Experimental badge on imaging (FR-8.1).
- Responsive to 360 px width; keyboard navigable; colour is never the only signal (icons and text accompany critical/abnormal states); WCAG AA contrast.
- No new npm dependencies beyond what `ui/package.json` already lists.

---

## 10. Non-functional requirements
- **Privacy:** no PHI in logs (mask names, IDs and values in log lines). Uploaded files are stored under `uploads/` with random names, and deleted 30 days after the job (configurable).
- **Performance:** M11. Render page PNGs lazily, with caching.
- **Offline:** everything except optional RAG/LLM works without internet once models are cached.
- **Determinism:** the lab engine is pure and deterministic; eval runs are reproducible.
- **Code quality:** ruff clean; type hints on new modules; no new heavyweight dependencies without a DECISIONS entry.
- **Coverage:** raise the CI floor as coverage grows (target ≥ 70% overall, ≥ 90% for `app/services/lab_engine/` and the new review modules).

---

## 11. Delivery plan

### 11.1 Git workflow
- Branch: `v2/verified-lab-intelligence`. Commit per requirement; message format `E<epic>.<FR>: <summary>`.
- After Phase 0 (and if `gh auth status` succeeds and the remote is writable), push the branch and open **one draft PR** titled "VaidyaAI v2 — verified lab intelligence", and push after each phase. Never push to `main`, never merge, never force-push. If pushing isn't possible, keep everything local and say so in `docs/PROGRESS.md`.

### 11.2 Crew (Claude Code subagents) — **no Fable**
Create `.claude/agents/` files as in Appendix D.

| Role | Model / effort | Use for |
|---|---|---|
| **nami** (planner) | Opus, `max` | Phase plans, schema/API design, security review of auth-touching changes. Plans only — no code. |
| **sanji** (builder) | Sonnet, `medium` (raise to `high` after a failed attempt) | Implementing planned requirements, tests, fixtures, generators. |
| **zoro** (specialist) | Opus, `xhigh` | UI (Review screen, patient page), auth/ownership/migration code, hard bugs, review of Sonnet-written code. |
| **usopp** (lookout) | Haiku, `low` | Scoped searches, renames, boilerplate, log summaries. Never auth, migrations or clinical logic. |
| **reviewer** | Opus, `max`, **fresh session that sees only the diff** | Phase-end independent review (Appendix E). It must not be the agent that wrote the code. |

Routing rules:
- Every job goes to the cheapest member that can do it well.
- On failure: check the context first, then raise effort one step, then move to the next member up.
- Code written by Sonnet/Haiku is reviewed by Opus.
- Opus-written code is reviewed by a **fresh** Opus `max` session that sees only the diff. Because that is the weakest review option, lean on tests and the eval harness.

### 11.3 Phases and gates

| Phase | Scope | Gate (all must pass) |
|---|---|---|
| **P0** Baseline | E0 | Suite, ruff, migrations and E2E green; PROGRESS/DECISIONS created; draft PR (if possible). |
| **P1** Measuring stick | E5 (generator, renderings, ground truth, splits, harness, CI job) | `make eval` runs; baseline metrics recorded in `eval/report.md` (expect them to fail targets — that's the point). |
| **P2** Review flow | E1 (backend + UI), E7, FR-9.1 | AC-1.1…1.8 green; S1, S2 and S9 tests green; M1 and M2 measured *with* review semantics. |
| **P3** Engine and audit | E2, E3 | M1 ≤ target on `test`; M2 = 100%; M3/M4/M6/M7 on `test` meet targets; Appendix A reproductions green. |
| **P4** Structured inputs | E4 | M8 = 100% on the conformance set. |
| **P5** Patient value | E6 | Summaries tests green; language snapshots; trends E2E. |
| **P6** Imaging and platform | E8, E9 | E8 ACs; security probe 0 regressions; dead code removed; CI green. |
| **P7** Release quality | E10 + full `heldout` eval + final independent review | All of §3 met on `heldout`; reviewer ≥ 9/10; docs complete. |

At **each phase end**:
1. Run the full suite, ruff, E2E and `make eval` (test split; plus heldout at P3 and P7).
2. Spawn **reviewer** (fresh Opus `max`) with the phase diff and Appendix E. It writes `reviews/P<n>.md`.
3. Fix every finding at MED severity or above, with a test each, then re-run the gate. LOW findings go to `docs/PROGRESS.md` (fix if cheap).
4. Update `docs/PROGRESS.md` and `eval/report.md`, commit, push.

### 11.4 Stopping rule
Iterate P3 ↔ review until M1–M7 meet target on `test`, or until **3 consecutive review rounds** fail to improve M1 or M2. In that case, stop, write `docs/STATUS.md` with the evidence and the most promising next step, and continue with the remaining phases (the review flow keeps the product safe regardless). Never weaken a safety assertion or a held-out metric definition to pass a gate.

### 11.5 Final report
At the end, write `docs/FINAL_REPORT.md`: what shipped, metric table (dev/test/heldout × rendering), reviewer scores per phase, open limitations, and how to run the demo. Its first line is a one-paragraph summary a non-technical reader can follow.

---

## 12. Risks and mitigations
| Risk | Mitigation |
|---|---|
| Synthetic corpus is easier than real reports | Hold-out layout families, scan/photo degradations, reviewer-written fresh cases each phase. Document clearly that real-report validation is a recommended human step after v2. |
| Review step feels slow | M7 caps the review burden; keyboard flow; templates and structured imports cut reviews. |
| OCR quality on phone photos | Perspective correction and binarisation before OCR (OpenCV is already a dependency); low-confidence tokens → `needs_review`. |
| Usage limits interrupt long runs | PROGRESS/DECISIONS files, commit per requirement, resume protocol (§0). |
| Translations wrong in hi/ta | Small fixed phrase set; native-script tests for completeness; flag "machine-assisted translation" in DECISIONS. Human proofreading is a recommended post-v2 step. |
| Scope creep | Non-goals (§4); imaging frozen as Experimental. |

---

## Appendix A — Independent audit findings (baseline, review round 5)

Severity: HIGH = can miss or invent a critical, or give a wrong headline; MED = wrong value/flag, or a needless review burden; LOW = hygiene. Turn every reproduction below into a fixture with clinically derived expectations.

**N1 HIGH — column parser overrides the text path** (`lab_report_column_parser.py:57` fixed x-bands, `:280-299`, `:39` section regex, `:147`; `pipeline.py` usability rule ~`:306-327`).
- On a standard A4 portrait layout (Results ≈305pt, Units ≈375, Ref ≈445), printed ranges are discarded and registry ranges substituted. Female ALT 25 (printed 0-35) → HIGH against registry 7-19; male ALT 38 (printed 0-45) → HIGH against 7-30.
- Printed paediatric ranges are lost → 6 rows UNKNOWN.
- A 2-page normal report becomes "unknown": the ESR row is eaten as a section heading, and the footer "Toll free … = 15.0" becomes a row.
- In a landscape Previous | Current layout the previous values are taken (Hb 12.8 NORMAL, K 6.8 CRITICAL), while the current Hb 5.9, Plt 14,000 and Na 121 appear only as unread text.
- Dated cumulative columns are glued into the name: "Potassium 4.2 5.1 = 6.7" → UNKNOWN, critical missed.
- None of the baseline's portrait fixtures is ever accepted by the parser (untested path). → FR-2.1

**N2 HIGH — a method/technology column destroys analyte identity** (`tokenizer.py` `reassemble_cells` ~`:1221-1245`, `_is_tail_cell` ~`:1192`, phase-2 value rule ~`:716`).
- SRL-style "ISE Indirect" column: K 6.9, Na 119 and glucose 41 are flagged only HIGH/LOW with no critical alert; headline "moderate".
- Metropolis-style: "CRITICAL: GLUCOSE (PP 2 mg/dL — Severe hypoglycemia" for a patient whose PP is 268 and FBS 186.
- Rows are named "C.L.I.A", "GOD-POD", "HPLC". → FR-2.2

**N3 HIGH — urine and CSF chemistry scored as serum** (`evaluate.py` ~`:322` name-only matching; ~`:475-486` a critical overrides an in-range printed value).
- "URINE SPOT ELECTROLYTES" Na 38 → "Severe hyponatremia"; K 42 → "Severe hyperkalemia".
- Urine creatinine 88 mg/dL (printed 39-259) → "Renal failure".
- Urine glucose 1000 → "Diabetic crisis".
- CSF glucose 28 → "Severe hypoglycemia"; the CSF differential is scored with blood ranges. → FR-2.3

**N4 HIGH — counts without a recognised unit assumed to be per µL** (`units.py` ~`:284-299`; `tokenizer.py` ~`:865-872` with ~`:367-383`).
- Header "Result (x10^3/uL)" with "WBC 7.2" and "Platelets 250" → both CRITICAL_LOW, headline critical.
- "Total WBC Count 7.2 H" → CRITICAL_LOW despite the printed H.
- OCR-garbled "WBC 7.2 KuL 4.0-10.5" → CRITICAL_LOW. → FR-2.4

**N5 HIGH — serology + urine routine routed to urinalysis** (fixed in the baseline via `_is_blood_row`; keep the regression tests).

**N6 HIGH — OCR angle classifier flipped digits** (fixed in the baseline: `use_angle_cls=False`; 0/240 misreads versus 16/240). Keep the test; extend the eval with rotated pages (page-level rotation handling, FR-5.2).

**N7 MED — "critical" headline without a critical value** (fixed in the baseline as a ceiling; keep the test).

**N8 MED — a flag cell attaches to the row above** (`tokenizer.py` ~`:1236` greedy tail absorption). Flag-first layout: Sodium 139 → CRITICAL_LOW (the "LL" belonged to Hb); Hb 10.9 L → UNKNOWN. → FR-2.5

**N9 MED — titres vanish** (`tokenizer.py` ~`:1015`). Widal O 1:160, H 1:320 → no rows, no warning, headline "low". → FR-2.6

**N10 MED — invented rows** (`tokenizer.py` ~`:583-595`, ~`:1141-1145`). Examples: "LDL: Not calculated (TG > 400 mg/dL)", "Unable to calculate (TG > 4.5 mmol/L)", "Diagnosing Diabetes >= 6.5 %", "S/Co 28.4" → HIGH. → FR-2.7

**N11 MED — form-style single test** ("Test Name : Serum Potassium / Result : 2.6 mmol/L") → row "Result", flagged LOW only, headline moderate. → FR-2.8

**N12 MED — urine-only reports show the legacy regex table** (`clinical_ner_service.py` ~`:229` the pH regex matches "Ph: 0422-2345678", ~`:247` any number "abnormal"; `pipeline.py` urine merge). A normal urine report shows "ph = 0422-2345678 ABNORMAL"; junk "blood=count" rows. → FR-2.9

**N13 MED — the X-ray gate accepts non-chest images** (`pipeline.py` `radiograph_plausibility` ~`:948-984`). Photos score 87-93 against an autoencoder threshold of 100; CT/MR slices score 39-52, the same as films. A CT DICOM slice → "Pleural_Thickening"; grayscale cat/cameraman photos → "Infiltration". MR, a humerus crop, and the NIH film rotated or inverted are all accepted. → FR-8.2

**N14 MED — OCR'd cumulative header glitch** (`tokenizer.py` ~`:519`, `evaluate.py` ~`:265`). Every row UNKNOWN; warnings mention only the previous column's 118/6.9, not the current Hb 6.6. → FR-2.10

**N15 LOW — footers count as unread lines** ("For queries call 1800…", "Turnaround time 4 hrs") → a normal report becomes "unknown". → FR-2.11

**N16 LOW — "HbA1c 7.8 H, <5.7 % of total Hgb"** → UNKNOWN; the printed H is ignored. → FR-2.12

**N17 LOW — no unique index on `sign_offs.job_id`**; check-then-insert allows duplicates under concurrency. → FR-1.2 / FR-9.1

**N18 LOW — WebSocket raw exception text** (fixed in the baseline).

**N19 LOW — README stale** (partly fixed in the baseline; finish under FR-9.3).

**N20 LOW — quality gate rejects a real wide-aspect film as a screenshot** (fails safe). → FR-8.3

**Measured baseline on the reviewer's 65 fresh reports:** native-text PDFs 37/59 fully correct (63%, 85% of checks); PNG 6/8; scan-like JPG 1/8; critical reports headlined critical 20/23. Security probe 30/31 (the miss was the README admin statement).

---

## Appendix B — Key files

| Area | Files |
|---|---|
| App entry, errors, limiter | `app/main.py`, `app/core/{config,auth,ownership,rate_limit,middleware,errors}.py` |
| Report pipeline | `app/pipeline.py` (`run_report_pipeline`, `_lab_rows_from_report`, `_is_blood_row`, urine merge, risk floor, `radiograph_plausibility`) |
| Lab engine | `app/services/lab_engine/{analytes,units,tokenizer,evaluate,models}.py`, `clinical_reference.py` (legacy helpers, derived markers) |
| Extraction | `app/services/preprocessor.py` (`run_ocr`, CSV structured path), `app/services/ocr_service.py` (`lines_from_boxes`), `app/services/lab_report_column_parser.py` |
| Urinalysis | `app/services/clinical_ner_service.py` |
| Imaging | `app/image_pipeline/classifier_v2.py`, `app/services/modality_classifier.py` |
| Workers | `app/workers/{celery_app,pipeline_tasks,db_persist}.py` |
| Routes | `app/api/v1/routes/{reports,images,claims,jobs,signoff,health}.py`, `app/routes/{auth,qr_reports,patient_history_routes,websocket_routes,text_routes}.py` |
| QR | `app/services/qr_service.py` |
| Migrations | `alembic/versions/*` (head: `a1b2c3d4e5f6`) |
| UI | `ui/VAIDYAAI.html`, `ui/shared.jsx`, `ui/report-analyzer.jsx`, `ui/report-qr-widget.jsx`, `ui/dashboard.jsx`, `ui/image-analysis.jsx`, `ui/claim-verifier.jsx`, `ui/report-preview.html` |
| Tests | `tests/test_{lab_engine,clinical_safety,lab_extraction,security_api,pipelines,signoff,auth,ownership,e2e}.py`, `tests/fixtures/*` |
| CI / infra | `.github/workflows/ci.yml`, `docker-compose.yml`, `docker-compose.prod.yml`, `Dockerfile`, `requirements.txt`, `.env.example` |

---

## Appendix C — Environment and commands

```bash
# Python 3.11 venv (CPU torch from the PyTorch CPU index)
python3.11 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install torch==2.3.0 torchvision==0.18.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt

# Services: Postgres 16 + Redis 7 (Docker is simplest)
docker compose up -d postgres redis      # or install locally

# .env for local dev (never commit secrets)
cp .env.example .env   # then set SECRET_KEY, POSTGRES_* to match the compose services, APP_ENV=development

alembic upgrade head
export HF_HUB_OFFLINE=0 REQUIRE_DB_TESTS=1
ruff check app/ tests/
pytest tests/ --ignore=tests/test_e2e.py -m "not integration" -q
# Live stack for E2E:
uvicorn app.main:app --port 8000 &
celery -A app.workers.celery_app worker -Q reports,images,claims,celery -c 1 --pool=solo &
pytest tests/test_e2e.py -q
# UI: http://127.0.0.1:8000/ui/VAIDYAAI.html
```

Notes:
- On Windows, use WSL2.
- `setuptools<81` is required by PaddleOCR (pinned).
- torchxrayvision weights download from GitHub releases on first use.
- If the PyTorch CPU index is unreachable, install `torch==2.3.0` from PyPI.

---

## Appendix D — `.claude/agents/` definitions

Create these files (frontmatter fields: `name`, `description`, `model`, `effort`; body = role instructions).

`nami.md`
```markdown
---
name: nami
description: Planner. Phase plans, schema/API design, security review of auth-touching changes. Plans only; never edits code.
model: opus
effort: max
---
You plan. Read PRD.md, docs/PROGRESS.md and the relevant code. Output a concrete, buildable plan:
files and functions to change, data structures, rules, ordering, and an acceptance-test table with
clinically derived expectations. Choose conservative, patient-safe defaults. Never write code.
```

`sanji.md`
```markdown
---
name: sanji
description: Builder. Implements planned requirements with tests, fixtures and generators. Needs a written plan.
model: sonnet
effort: medium
---
Implement exactly the plan you are given. Write tests first for changed behaviour, derive expectations
from clinical knowledge (never from your own output), keep ruff and the full suite green, never weaken
a safety assertion, and report changes per requirement ID.
```

`zoro.md`
```markdown
---
name: zoro
description: Specialist. UI screens, auth/ownership/migrations, hard bugs, and review of Sonnet/Haiku-written code.
model: opus
effort: xhigh
---
Build or review the hard parts. For UI keep the in-browser-Babel global style of ui/ and the shared.jsx
design system; accessibility matters. For auth/ownership/migrations require a test for every changed path.
When reviewing, report findings with severity, file:line, a reproduced failure and a fix.
```

`usopp.md`
```markdown
---
name: usopp
description: Lookout. Scoped searches, renames, boilerplate, log summaries. Never auth, migrations or clinical logic.
model: haiku
effort: low
---
Do the narrowly scoped mechanical task you are given within the named folder. Keep prompts small.
```

`reviewer.md`
```markdown
---
name: reviewer
description: Independent phase-end reviewer. Fresh session; sees only the phase diff, the PRD and Appendix E. Never reviews code it wrote.
model: opus
effort: max
---
You did not write this code. Review the given diff against PRD.md and Appendix E. Write your OWN fresh
adversarial inputs (never reuse fixtures; never read the heldout split's files), derive expectations
before running, run them end to end, and report: per-finding severity, file:line, reproduced failure,
fix; the phase's metrics; and a score out of 10 using Appendix E. Save to reviews/P<n>.md.
```

---

## Appendix E — Review rubric (what 9/10 means)

Score out of 10 for "working, safe for its stated purpose as a decision-support product, well tested". Start at 10 and subtract:

| Deduction | When |
|---|---|
| −3 | Any path where an unconfirmed or wrong value can reach a score, alert, summary, sign-off or QR without a clinician seeing it (S1/S2 breach), or any missed critical that isn't flagged for review (M2 < 100%). |
| −2 | Any security regression (S10/S11), or patient text that states a diagnosis or dosing (S8). |
| −1 each | Each HIGH finding (wrong value/flag/headline that is **not** flagged for review). |
| −0.5 each | Each MED finding; each §3 metric missing its target on `test`. |
| −0.25 each | Each LOW finding (cap −1 total). |
| −1 | Tests that only pin the implementer's own outputs (expectations not clinically derived), or a missing held-out evaluation. |

A score of 9+ requires: no deductions above −0.5 each, M1/M2 met on **heldout**, and every safety invariant enforced by a test. The reviewer must list the deductions line by line, and separately list declared limitations it excluded (§4).
