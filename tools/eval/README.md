# tools/eval: the P1 evaluation harness

Measures lab-report extraction and headline scoring against a synthetic, fully known corpus (plan: `plans/P1.md`).
Section numbers below refer to that plan.

## Quick start

```
source .venv/bin/activate
make eval-corpus     # optional: pre-generate everything (about 3 GB, git-ignored)
make eval            # the CI gate: ratchet vs eval/baseline.json, test split
```

`make eval` generates what it needs on demand, so `eval-corpus` is only a convenience. `--no-materialize` makes a
run refuse to generate anything (a missing file or stale manifest exits 2).

## Generating the corpus

`python -m tools.eval.generate --splits dev,test,heldout --renderings native` (flags: `--splits`, `--renderings`
as a list of `native,csv,scan,photo,rot90` or `all`, `--verify`, `--out`). Splits: dev 240, test 80, heldout 80
reports. Output goes to `eval/corpus/<split>/` (git-ignored). Seeds, sizes and profiles live in
`tools/eval/config.py`. Each split has a fingerprint; a generator change changes it (see Determinism).

## Make targets

| Target | Runs | Use |
|---|---|---|
| `make eval` | `tools.eval.run --profile ci` | CI gate (ratchet), about 13 min cold, about 3 min with a warm OCR cache |
| `make eval-dev` | `--profile dev` | tuning; gate none |
| `make eval-full` | `--profile full` | dev + test + heldout, every rendering in full; gate report |
| `make eval-heldout` | `--profile heldout` | heldout only, aggregate output only |
| `make eval-corpus` | `generate --splits dev,test,heldout --renderings all` | pre-generate the corpus |
| `make eval-verify` | `generate --verify --splits dev,test,heldout` | check plan and corpus invariants |
| `make eval-baseline` | `--profile ci --update-baseline` | rewrite `eval/baseline.json`; needs a DECISIONS line |

`EVAL_ARGS` passes extra flags, for example `make eval EVAL_ARGS="--sample 6 --gate none"`.

## Profiles and sampling

| Profile | Splits | native / csv | scan | photo | rot90 | Default gate |
|---|---|---|---|---|---|---|
| `ci` | test | 100% | 25% | 25% | 100% | `eval/gate.json` |
| `dev` | dev | 100% | 25% | 25% | 100% | none |
| `full` | dev, test, heldout | 100% | 100% | 100% | 100% | report |
| `heldout` | heldout | 100% | 100% | 100% | 100% | report |
| `smoke` | test, first 6 reports | 100% | 0 | 0 | 0 | none |

Overrides: `--splits a,b`, `--renderings native,csv,scan,photo,rot90`, `--sample N` (first N reports) or
`--sample scan=0.5,photo=0.5`, `--sample-n N`, `--shard k/n`, `--jobs N` (spawn pool). Any override without an
explicit `--gate` defaults to `--gate none`. A partial run is never compared with the full baseline: `--shard` with
`--gate ratchet|targets` exits 2, the gate exits 2 when a cell's job count or GT denominators differ from the
baseline's, and `aggregate` exits 2 unless every cell holds all its planned jobs (each shard file records the full
cell size and its `k/n`). Heldout is never narrowed: `--sample`, `--sample-n`, `--shard` or any sampling fraction
below 1 with heldout selected exits 3 (whole split x rendering cells via `--splits`/`--renderings` are fine).
Image sampling is stratified by scenario class and ranked by SHA-256(`id|img-sample-v1`), so it is stable.

Other flags: `--gate none|report|ratchet|targets`, `--gate-config` (default `eval/gate.json`), `--baseline`,
`--out DIR` (default `eval/` for a complete `full` run, `eval/runs/<profile>/` otherwise; only a complete
`full` run may write `eval/`), `--ocr-cache on|off`, `--corpus-dir`, `--dump-failures DIR` (dev/test only),
`--shard-out FILE`, `--update-baseline`. Sharded runs are merged with
`python -m tools.eval.run aggregate --in DIR [--out DIR --gate report]`; with `--gate report` (the nightly) a
missing cell exits 2 and a crashed job exits 1.

## Gate

`eval/gate.json` is merged over the defaults in `tools/eval/gate.py` (`mode`, `profile`, `split`, `baseline`,
`tolerance`). It currently equals the defaults: ratchet M1 to M5 and M2_post on the test split against `eval/baseline.json`,
tolerance 0 counts for native and csv, 1 count for scan, photo and rot90, and the crash count may not rise. M6 and
M7 are never ratcheted; they are checked only against targets in `targets` mode, which the P3 gate switches on
(change `"mode"` to `"targets"`). Targets (`targets.py`, PRD section 3, plan 6.9) are immutable constants.
A fingerprint, profile, sampling, shard, adapter, scorer or judge digest that differs from the baseline exits 2: the
baseline is never silently reused. The judge digest (`gate.judge_sources`) hashes the sources that define what is
counted: `match`, `review_sim`, `metrics`, `targets`, `gate`, `run`, `ocr_cache` and every `adapters/*.py` (the v0
needs_review rules included), so changing a metric definition stops the ratchet instead of passing as an
improvement. Accepting a deliberate trade-off needs `make eval-baseline` plus a `docs/DECISIONS.md` line naming
the metrics that moved.

## Metrics (plan 6.8; all counts additive, per split and rendering, Wilson 95% intervals)

- M1 silent-error rate: wrong, invented or omitted rows not flagged needs_review, over GT rows.
- M2 critical recall: GT critical rows flagged critical or needs_review before confirmation (PRD section 3 as
  written; target 100%).
- M2_post critical handling after confirmation (S3 final state): GT critical rows whose confirmed row the scorer
  flags CRITICAL (target 100%, ratcheted). A critical flagged for review but scored HIGH after confirmation raises
  no alert in v2, so it counts for M2 and not for M2_post.
- M3 row accuracy before review: correct pairs over GT rows plus invented rows.
- M4 false-critical rate: critical flags on unmatched or non-critical rows, over GT rows.
- M5 headline on confirmed: simulated-review scoring equals the GT headline.
- M6 normal to ready: normal-headline reports with no expected review that come out ready (target 90% on every
  rendering: PRD M6 names no rendering).
- M7 review burden: share of extracted rows and unread items flagged needs_review.

Diagnostics (M4-post, M5-pre, M5-oracle, R-NR, legibility, per-family M3, error signatures, timings) are dev and
test only, except crash counts, legibility and M1 components.

## Adapter and scorer swap point (P2, plan 14)

Adapters and scorers are reached only through the registries in `tools/eval/adapters/__init__.py`
(`ADAPTERS`, `SCORERS`; `--adapter`, `--scorer`; defaults `pipeline_v0` and `engine_floor_v0`), and only
`run.py` imports them. The interface is `adapters/base.py` (`EvalJob`, `ExtractionResult`, `ExtractedRow`).
P2 registers `extracted_rows` and the `confirmed_v1` scorer, then re-baselines with a DECISIONS line. GT, matching,
metric definitions and targets stay untouched. Judge-side and GT-side modules must not import `app` or the adapters.

## Determinism

Seeds are hashed per purpose (`config.seed_for`); iteration is sorted, rounding uses Decimal, and PDFs and JSON are
canonical. Native and csv metrics are exactly reproducible; image renderings are reproducible on the same CPU, OCR
build and OCR cache (hence the 1-count image tolerance). `metrics.json` deterministic sections are byte-identical
between runs, and outputs are written atomically. Any generator change alters the split fingerprint, so the ratchet
exits 2 until you re-baseline with a DECISIONS entry in the same commit; failing cases are never removed.

## Heldout hygiene (plan 6.11, FR-5.4)

- Heldout results are aggregate only. Never open, grep or summarise heldout failures, and never read
  `eval/corpus/heldout/` or `tools/eval/layouts/heldout/` (DO NOT READ) when working on extraction, the engine or
  templates; repeat this rule in prompts to other agents.
- `hygiene.py` enforces it: `--dump-failures` and any narrowing (`--sample`, `--sample-n`, `--shard`, fractions
  below 1) are refused with heldout (exit 3); heldout jobs run silenced and record
  exceptions by class name only; only whitelisted, redacted cells reach disk; tests scan for canary tokens in `app/`.

## Exit codes

0 pass; 1 gate failed; 2 harness or config error (missing baseline or corpus, fingerprint mismatch, import
failure, bad arguments); 3 hygiene violation.

## Committed versus generated (plan 9)

Committed: `tools/eval/**`, `tests/test_eval_*.py`, `tests/fixtures/eval_golden/`, `eval/gate.json`,
`eval/baseline.json`, and `eval/metrics.json` plus `eval/report.md` only from a phase-end `make eval-full`
(never commit a partial run over them; the harness refuses to write them from anything but a complete `full` run).
Git-ignored: `eval/corpus/`, `eval/.cache/` (OCR cache), `eval/failures/`, `eval/shards/`, `eval/runs/`.

## CI

- `ci.yml` job `eval-gate` (blocking): restores the test corpus and OCR caches, runs
  `generate --verify --splits test`, then `make eval`, and uploads `eval/runs/ci/metrics.json` and
  `eval/runs/ci/report.md`.
- `ci.yml` job `eval-full-on-label`: the `eval-full` PR label calls `eval-full.yml`.
- `eval-full.yml`: nightly (`41 20 * * *` UTC), `workflow_dispatch` or `workflow_call`; a shard matrix
  (split x rendering) runs with `--gate none --shard-out`, then `aggregate` runs with `--gate report` and uploads
  `eval-full-metrics`. It never commits.
