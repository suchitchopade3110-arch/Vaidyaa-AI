# P1 T16 review (zoro, Opus): Sonnet-written P1 eval code

Reviewer: zoro · 2026-11-19 · branch `v2/verified-lab-intelligence` · base `ebb1066` (T15 baseline)

The review covers everything under `tools/eval/` (catalogue, critical_table, units, gt_schema, clinical,
scenarios, config, layouts/*, render, generate, targets, gate, hygiene, report, run, match, metrics,
review_sim, ocr_cache, adapters/*), the `tests/test_eval_*.py` suites and the two eval workflows. It is checked
against plans/P1.md (§3.4, §3.5, §3.8, §4.2, §6.6–6.12) and docs/PRD.md (§3, §6, Appendix E).

Constraints honoured:
- A `--profile full` run was in progress, so nothing was run against `eval/` and nothing under `eval/` was touched.
- GT was rebuilt in memory only, through `generate.build_printed` + `layouts.base.build_gt`.
- Nothing per report was printed for heldout: only aggregate counts and one combined digest.
- No file under `app/` was changed, and no GT-side module that feeds the test-split fingerprint was changed, so
  `eval/baseline.json` stays valid.

## Summary

| Severity | Count | Fixed here | Deferred to the lead |
|---|---|---|---|
| HIGH | 1 | 0 | 1 (F1: GT change) |
| MED | 3 | 2 (F3, F4 test-side) | 1 (F2: metric definition) + F4's move into `verify` |
| LOW | 15 (L1–L14 and F1b) | 4 (L1, L2, L3, and L4 test-side) | 11 (corpus changes or documentation) |

Every MED+ finding has a test. The fixed ones have a failing-first test. The deferred ones have a `strict=True`
xfail that fails (XPASS) as soon as a fix lands. Verification is at the end.

Re-derived independently and found sound (no finding):
- Table C values and guard bands (table below).
- Catalogue reference intervals and unit factors.
- The titre cut-off logic: the printed cut-off is ">= 1:80" or ">= 1:160", ratio >= cut-off gives POSITIVE, and
  titres printed without a cut-off are only <= 1:40 or >= 1:320.
- The interpretive tables. Clear margins keep values out of the disputed categories, and LDL carries a printed
  "< 100".
- Determinism. Generator and judge outputs are byte-identical under `PYTHONHASHSEED` 0, 4242 and 987654. There is
  no global RNG, no `hash()`, no clock in outputs, and no order-dependent set iteration; `id()` is used only for
  membership.
- The M1–M7 definitions in `metrics.py`, `match.py` and `review_sim.py` (§6.5–6.8), apart from F2.
- Hygiene in `hygiene.py`, `report.py` and `run.py`: fd-level quiet, a whitelist that raises on unknown keys,
  class-name-only crashes, and heldout messages withheld on every error path.

---

## HIGH

### F1 — Range-absent rows in SI/alternative units get a wrong GT flag (deferred: changes GT)

- **Where.** `tools/eval/clinical.py:159-172` (`effective_range`, consensus branch) and `:175-188`
  (`_true_region`). The printed value is compared with `CATALOGUE[key].consensus`, which is in the
  **conventional** unit, with no conversion. `review_sim.true_row` (`tools/eval/review_sim.py:65-80`) then hands
  the scorer `effective_range_true` (in mg/dL) together with `unit_raw = unit_true` (µmol/L).
- **Reproduction.** Male, age 40, no printed range:
  - `clinical.derive_row("creatinine", value=80, unit="umol/L", unit_true="umol/L",
    printed_range=PrintedRange("none"), ...)` gives `flag=HIGH, flag_true=HIGH`. The truth is 0.90 mg/dL against
    M 0.7–1.3, which is NORMAL. This makes a normal report's headline "abnormal".
  - Hb 140 g/L gives HIGH; fasting glucose 5.0 mmol/L gives LOW; Ca 2.35 mmol/L gives LOW; bilirubin 10 µmol/L
    gives HIGH.
  - Test: `tests/test_eval_clinical.py::test_range_absent_si_value_is_judged_in_canonical_units` (strict xfail, 5
    cases, including the simulated-clinician path).
- **Why the existing checks miss it.** The oracle (`ClinicalRulesScorer`) reuses `derive_row`, so it is
  self-consistent with the wrong GT. GN-15 and `verify` do not look at units.
- **Corpus impact today.**
  - G01 and G02 are the only families with range-absent rows, and they are SI in 30% of reports. All 4 dev/test
    SI range-absent rows happen to be analytes whose SI unit equals the conventional one, so 0 rows are wrong.
  - Heldout: 1 row with a wrong `flag`/`flag_true` (aggregate count only).
  - So the committed numbers are barely affected, but the generator emits wrong GT whenever the dice land on a
    convertible analyte.
- **Recommended fix (for the lead).**
  1. In the consensus branch of `effective_range` and `_true_region`, convert `value` to canonical first
     (`units.to_canonical(key, value, unit_true or unit)`) before `region(...)`.
  2. Express `effective_range_true` (and `effective_range_page`, when it is the consensus) in `unit_true`, so
     `true_row` gives the scorer a consistent (value, unit, range).
  3. Fold in F1b (below).
  4. This changes `clinical.py` (every split fingerprint) and one heldout GT row. It needs a DECISIONS line and a
     re-baseline (plan §4.2.6, §8).

### F1b (LOW, latent) — same root cause for unit-less counts

- **Where.** `clinical.py:262` computes `flag_true` with `value` in the printed space. A unit-less count
  (AMB_UNITLESS_COUNT) whose `unit_true` is `/cumm` gets WBC 7200 against 4.0–11.0, so `flag_true=HIGH`.
- **Why latent.** IN-E has `cumm_p = 0` and the generic families have `cumm=False`, so no shared family emits this
  today.
- **Test.** `tests/test_eval_clinical.py::test_unitless_count_true_flag_uses_the_true_unit` (strict xfail).
- **Fix.** Covered by the F1 fix (convert with `unit_true`).

---

## MED

### F2 — Unread-line coverage is unbounded; one "line" can cover a whole page (deferred: metric definition)

- **Where.** `tools/eval/match.py:406-411` (`covers`) and `:442-447`. Every unmatched GT row whose value and a name
  token appear anywhere in `u.source_text` is covered, and covered rows are then:
  - not M1 omissions;
  - M2 hits;
  - "added" with GT true values by the simulated clinician, so M5 is right.
- **Reproduction.** 20-row report with K 6.9 critical. The extraction has **zero rows** and one `unread_line`
  whose `source_text` is the page text. Result: **M1 0/20, M2 1/1, M5 1/1**; only M3 (0/20) and M7 (1/1) notice.
  Test: `tests/test_eval_matching.py::test_one_unread_line_cannot_cover_a_whole_page` (strict xfail).
- **Impact.** None on P1 numbers: v0 emits no unread lines (§6.3.3). It matters for P2: `ExtractedRowsAdapter`
  emits unread lines (FR-1.3), and the P2 gate measures M1/M2 with these semantics (§14). A degenerate
  "everything unread" adapter would pass M1 and M2.
- **Recommended fix (lead; it changes the §6.6.7 definition).**
  - A covering line must be one visual line: same `source_page` as g, and a bbox vertical overlap with `g.bbox`
    once P2 provides bboxes.
  - Until then, cap coverage at 2 GT rows per unread line, per the plan's own "two per line".
  - Record the decision in DECISIONS before P2 cuts its baseline.

### F3 — GN-12 canary leak test scanned only `app/**/*.py` (fixed)

- **Where.** `tests/test_eval_heldout.py` `canary_hits`. The scan was `root.rglob("*.py")` over `app/` only.
- **Why it matters.** The P3 template registry is `app/services/lab_engine/templates/` (PRD FR-3.1). Its
  "detection signals (header tokens, …)" may live in JSON or YAML, and `clinical_reference.py` sits at the repo
  root. A heldout header word copied into either passed GN-12, which is the only code-level heldout guard (§6.11.7).
- **Reproduction.** `test_gn12_leak_detection_covers_template_data_files` writes canaries 3 and 5 into a `.yaml`
  and a `.json` under `services/lab_engine/templates/`. The old helper returns `[]`, so the test fails.
- **Fix.** `canary_hits(*roots)` now reads every text file (py, json, yaml, toml, txt, md, csv, html, js, …;
  skipping `__pycache__` and files over 5 MB) under `ENGINE_ROOTS = (app/, clinical_reference.py, data/)`.
  Messages still carry indices only. `test_gn12_scan_reaches_the_engine_roots` guards against a vacuous scan.
  Commit `1761be8`.

### F4 — `generate --verify` lacks two plan-mandated checks (fixed test-side; moving into `verify` deferred)

- **Where.** `tools/eval/generate.py:439-498` has neither of these:
  - §6.8: "`ClinicalRulesScorer` on GT rows must reproduce the GT headline for 100% of jobs; verify fails
    otherwise." This check has already caught one real bug class: the titre cut-off fix in `42771f8`.
  - §3.6: "verify fails if more than 0.5% of rows are dropped". The `scenarios.draw_values` docstring claims verify
    counts drops; nothing does.
- **Measured.**
  - Oracle check: 0 mismatches over 1,079 scopes (dev 647, test 216, heldout 216 as a count).
  - Drops: 0 of 14,919 primary draws.
  - So these are missing guards, not live defects.
- **Fix.**
  - `tests/test_eval_corpus.py::test_oracle_reproduces_every_gt_headline` runs the §6.8 check over every
    test-split report and page scope (3.6 s, reusing the existing materialised fixture).
  - Non-vacuity: a mutated `true_row` that reads `effective_range_page` is caught on 8 scopes. The old titre bug is
    not visible at headline level in this corpus, because cut-offs are 1:80/1:160 and emitted titres are far from
    them.
  - Commit `a5a0587`.
- **Deferred.** Putting both checks into `verify` changes `generate.py`, which feeds every fingerprint. Do it at the
  next deliberate re-baseline.

---

## LOW

| # | Finding (file:line) | Reproduction / evidence | Status |
|---|---|---|---|
| L1 | The ratchet compared only the renderings the current run measured (`tools/eval/gate.py:86-96`). | `--renderings native --gate ratchet` passed while scan/photo/rot90 regressed unseen. | **Fixed** `f728afb`: a baseline cell the run did not measure exits 2 (`test_ha05_ratchet_on_a_subset_of_the_baseline_cells_exits_2`). The committed baseline has all five test cells, which the CI profile always measures. |
| L2 | Shard runtime listed per-job wall times in sorted-job-id order (`tools/eval/run.py:522`), so position i of a heldout shard identified job H(i+1). Per-report timing went to disk and to the CI artifact. | `test_ha06_shard_runtime_is_not_in_job_order` | **Fixed** `f728afb`: lists are sorted; p50/p95 and the merge are order-independent. |
| L3 | T10 workaround: `templates.py:558` calls `_name_width()` with no argument unless UK-A, because the heldout override took no argument. | `test_heldout_value_engine_accepts_the_base_name_width_signature` (TypeError before the fix). | **Fixed** `1761be8` in `heldout/families.py:88` (`*_unused`). Heldout output is byte-identical (aggregate digest `79a66bd9…` before and after); only the heldout source fingerprint moves, so the committed `metrics.json` heldout fingerprint is now one generation old (no gate reads it). The `templates.py` conditional is left in place: simplifying it moves the test-split fingerprint. Do it at the next re-baseline. |
| L4 | The plan's 100% oracle self-consistency check is absent from `verify` (the F4 half on the GT side). | see F4 | Test added; the move into `verify` is deferred. |
| L5 | Range-absent "normal" is the middle 60% of the consensus (`scenarios.py:193,196`). That is not inside every catalogue variant, against §3.6 "any reasonable interval then gives the same flag". | ALT 7–56 against variants "< 40" / "F < 35"; creatinine M 0.82–0.89 against variant M 0.9–1.3; RBC F 4.0–4.19 against F 4.2–5.4; MCHC 34.6–35.2 against 31.5–34.5; unspecified glucose 110–126 against 70–110; HbA1c 3.0–3.99 against 4.0–5.6. Dev/test: 0 affected rows of 73 (1 unit-less WBC sits exactly on the consensus bound 11.0). | Deferred (corpus). Recommend sampling normal inside the intersection of all variants. |
| L6 | Range-absent `printed_bounds` convert variant bounds with the analyte's unit rather than the variant's own unit (`scenarios.py:256`). This affects the UK Ca/P/Mg variants in mmol/L. | Only the "one step from a bound" check uses the wrong bounds; there is no effect on flags. | Deferred (changing it shifts RNG streams). |
| L7 | Printed flags are drawn from `flag_true` (`templates.py:630`) even when the page hides the sex (`templates.py:431` prints sex-labelled ranges when the input sex is None). In flag-printing families an empty flag cell, or an H/L, reveals the hidden-sex verdict, while GT says UNKNOWN plus review. | About 21 AMB_SEX/AGE rows in dev/test sit in flag families (IN-B, US-A, G03, G05). | Defensible (printed flags can be lab errors), but record it in DECISIONS so P3 does not "fix" it by trusting printed flags. |
| L8 | The plan's quantitative urine-glucose variant ("< 15 mg/dL") and numeric urobilinogen (§3.4) are not generated: `catalogue.py:330-338` has qualitative only. The CL-06 test asserts a proxy, `critical_direction("glucose_fasting", 1000, "urine")` (`tests/test_eval_clinical.py:241`), not the planned HIGH. | — | Deferred (corpus). The CL-06 expectation should be restored with the variant. |
| L9 | `run.process_job` re-raises any `ValueError` from `job_record` (`run.py:292`), including one raised inside the engine scorer. One bad job aborts the whole run with exit 2 (heldout: "details withheld") instead of counting a crash. | — | Doc only. Fail-loud is conservative, but it differs from the "crash counts by class" wording. Narrow it to the GT-consistency error. |
| L10 | `eval/gate.json` is not in the repo (G3 and DECISIONS 12 say it is committed). `gate.load_config` silently falls back to `DEFAULT_CONFIG`, which has the same values. | `git ls-files eval` lists only baseline.json, metrics.json and report.md. | Lead: commit `eval/gate.json` (eval/ was off-limits during this review). |
| L11 | eval-full heldout shards save their OCR cache (OCR text of heldout pages) to the Actions cache (`.github/workflows/eval-full.yml:82`). | — | Not per-report results, and the corpus is regenerable from committed code. Optionally skip the cache save when `matrix.split == heldout`. |
| L12 | `ClinicalRulesScorer`, used as the *active* scorer, cannot re-derive an accepted sex-labelled row whose `effective_range_page` is (correctly) null, because the row carries no options (`clinical.py:352`). | A perfect GT-as-extraction run over dev+test with the oracle as scorer: M5 319/320 (D160, ESR, sex unknown). M1 0, M2 152/152, M3 8875/8875, M4 0, M6 67/67, M7 251/8875 and R-NR 251/251 are otherwise exact. | Metric unit tests only. P2's `confirmed_v1` must carry the printed options (e.g. via `ref_text`). |
| L13 | Latent `range_result` derivation gaps (`clinical.py:248-254`): words such as "Plenty"/"Numerous" become 0 and so NORMAL; a straddling "4-6" against 0–5 is NORMAL; an upper-only printed range would raise. | The generator never emits these (abnormal min > hi, normal max <= hi). | Doc only. Raise on unknown words if a future family adds them. |
| L14 | Table C policy dependence not covered by the guard bands: "analyte not on the institution's list" policies for creatinine and troponin (some labs list neither), and possibly a WBC high limit of > 100 at some centres (could not verify; the Q-Probes-derived median is > 30 [1]). | The guard bands cover threshold spread, not "no critical at all". | Declare as a limitation in report.md and DECISIONS 8; Table C itself needs no change. |

---

## Table C and catalogue: independent derivation (focus 1)

The limits below are my own clinical reading (Kost 1990 means, common institutional lists, CAP Q-Probes
medians), compared with `tools/eval/critical_table.py:29-53`. The test throughout is whether every emitted
critical is critical, and every emitted non-critical is non-critical, under any common policy.

| Analyte | Common institutional limits (low / high) | Table C critical and guard | Verdict |
|---|---|---|---|
| K mmol/L | 2.5–3.0 / 6.0–6.5 (Kost 2.8/6.2) | < 2.5 [2.5, 3.0]; > 6.5 [6.0, 6.5] | Sound. Engine 3.0/6.0 sit on the guard edges (closed). |
| Na mmol/L | 115–125 / 155–160 (Kost 120/158) | < 118 [118, 125]; > 160 [155, 160] | Sound. A < 115 policy would call 116–117 non-critical (rare; LOW at most). |
| Glucose mg/dL | 40–55 / 400–500 (Kost 46/484) | < 40 [40, 55]; > 500 [300, 500] | Sound. 501–750 is critical under every adult list I know. |
| Ca total mg/dL | 6.0–7.0 / 12.0–14.0 (Kost 6.6/12.9) | < 6.0 [6.0, 7.0]; > 13.5 [12.0, 13.5] | Sound. Factor 4.008 (0.2495) is correct. |
| iCa mmol/L | 0.75–0.90 / 1.50–1.65 (Kost 0.78/1.58) | < 0.75; > 1.65 | Sound. |
| Mg mg/dL | 0.9–1.2 / 4.0–5.0 (Kost 1.0/4.7) | < 0.9; > 5.0 | Sound. Factor 0.4114 is correct. |
| P mg/dL | 0.9–1.5 / none consensus | < 0.9; cap < 8.0 | Sound. |
| Hb g/dL | 6.0–8.0 / 18.5–21 (Kost 6.6/19.9) | < 6.0; > 21.0 | Sound. Derived Hct leaves the [22, 57] cap and is omitted, as planned. |
| WBC 10³/µL | 1.0–2.5 / 30–50 | < 1.0; > 50 | Sound for 30–50 policies; see L14. |
| ANC 10³/µL | 0.5–1.0 | < 0.5 | Sound. |
| Platelets 10³/µL | 20–50 / 750–1000 (Kost 37/910) | < 20; > 1000 | Sound. lakh/cumm ×100 is correct. |
| INR | 4.0–5.0 high | > 5.0 [3.5, 5.0] | Sound. The engine's > 3.0 gives the intended M4 for INR 3.0–3.4. |
| pH | 7.15–7.25 / 7.55–7.65 (Kost 7.21/7.59) | < 7.15; > 7.65 | Sound. |
| pCO2 mmHg | 15–25 / 60–70 (Kost 19/67) | < 15; > 70 | Sound. |
| pO2 mmHg | 40–50 (Kost 43) | < 40 | Sound. A rare < 55–60 policy would call 51–60 critical; LOW. |
| HCO3 mmol/L | 10–15 / 38–45 (Kost total CO2 11/40) | < 10; > 45 | Sound. |
| Creatinine mg/dL | 5–10 (Kost about 7.4) | > 10 [5, 10] | Sound for listing labs; L14 for non-listing labs. |
| hs-TnT ng/L / TnI ng/mL | any > URL, or > 100 / > 0.5 | > 100 / > 0.5, guard from the printed URL | Sound: the [URL, limit] guard removes the "any positive" disagreement. |

The plausibility limits contain every emitted range, and caps are applied in the conventional unit.

Every catalogue reference interval matches standard adult intervals:
- CBC, differential, renal, electrolytes, LFT, lipids, thyroid, iron, vitamins, coagulation, cardiac, ABG,
  urine and CSF.
- Table P bands are reasonable paediatric approximations.

Every SI factor was re-derived from molar masses:
- glucose 18.016, creatinine 88.42, urea 0.1665, BUN 0.357, uric acid 59.48, bilirubin 17.1;
- cholesterol 0.02586, TG 0.01129, T3 0.01536, T4/FT4 12.87, FT3 1.536;
- iron 0.1791, B12 0.7378, folate 2.266, vitamin D 2.496, Ca 0.2495, P 0.3229, Mg 0.4114;
- HbA1c IFCC (% − 2.15) × 10.929.

All of them are correct. The one clinical-correctness defect found is in how the consensus is *applied* (F1), not
in the tables.

## Deferred to the lead (each needs a DECISIONS line; F1 also needs a re-baseline)

1. **F1 (HIGH).** Fix `clinical.effective_range` and `_true_region` to compare in canonical units, and express
   `effective_range_true` in `unit_true`.
   - Moves every split fingerprint and 1 heldout GT row.
   - Remove the strict xfails `test_range_absent_si_value_is_judged_in_canonical_units` and
     `test_unitless_count_true_flag_uses_the_true_unit` when it lands.
2. **F2 (MED).** Bound unread-line coverage to one visual line (same page, bbox overlap, or at most 2 rows per
   line) before P2 cuts its baseline. Remove the strict xfail `test_one_unread_line_cannot_cover_a_whole_page`.
3. **F4.** Move the oracle self-consistency check and a dropped-row counter (0.5% budget) into `generate --verify`
   at the next re-baseline.
4. **L3.** Simplify the call site at `templates.py:558` at the same re-baseline. Add a DECISIONS note that the
   heldout fingerprint moved with no output change in `1761be8`.
5. **Corpus items for the next deliberate re-baseline.** L5 (variant-intersection normals), L6 (variant-unit
   bounds) and L8 (quantitative urine glucose and numeric urobilinogen, restoring CL-06).
6. **Documentation.** L7 (printed flags on hidden-sex rows), L14 (Table C "not listed" policies) and L12 (P2
   scorer must carry the printed options).
7. **Commit `eval/gate.json` (L10).**

## Verification performed

- Eval suites, run with `nice -n 10 pytest <file> -q -p no:cacheprovider`. Final run over all nine
  `tests/test_eval_*.py` files: **468 passed, 7 xfailed** (strict: F1 ×5, F1b, F2) in 217 s.
- Ruff is clean on every changed file.
- In-memory GT rebuild of dev+test (320 reports, 8,875 rows) to quantify:
  - F1: 0 dev/test rows; heldout 1 row (aggregate).
  - L5: 0 of 73 range-absent rows variant-dependent.
  - Oracle consistency: 0 mismatches over 1,079 scopes.
  - Drops: 0 of 14,919 primary draws.
  - Perfect-extraction metric sanity: M1 0, M2 100%, M3 100%, M4 0.
- Determinism: dev+test generator and judge digests are identical under three `PYTHONHASHSEED` values.
- Heldout: one aggregate digest over all GT and page ops, unchanged by the families.py edit. Nothing per report
  was printed.
- Not run: `make eval*`, `tools.eval.run`, and `tools.eval.generate` against `eval/corpus` (the full run was in
  progress).

[1]: The Q-Probes-derived WBC limits "< 2.0 or > 30.0 ×10³/µL" (medians of participants) come from a copy of the
ASCP critical-values practice parameter ([scribd](https://www.scribd.com/document/773122962/Critical-values-ASCP-practice-parameter);
unverified against the original). One health system lists ≥ 50.0 K/µL
([Avera list](https://www.avera.org/app/files/public/cf2d4c33-2a35-412b-b0f8-6650ba3460a4/A_Comprehensive%20Avera%20Critical%20Values%20List_072823-final.pdf)).
The 2007 Q-Probes comparison of 163 labs did not include WBC
([Wagar et al.](https://scilit.com/publications/906f4cf09b1350c7a780e0a3c2c068cf)).
