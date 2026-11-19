> DO NOT READ when working on extraction, the engine or templates (FR-5.4). Reading this folder or `eval/corpus/heldout/` to write rules invalidates the heldout metric.

# Heldout-only layout families (T08)

**DO-NOT-READ-FOR-ENGINE-WORK.** This folder belongs to the evaluation harness only. Engine, extraction and
template builders (P3 and later) must not open, grep, summarise or copy anything under
`tools/eval/layouts/heldout/` or `eval/corpus/heldout/`, and must repeat this rule in their prompts.

Guards:

- `families.py` lists `CANARY_TOKENS`. Test GN-12 fails if any token appears in `app/**/*.py`; its message prints
  only the canary index.
- The families refuse every split except `heldout`, and per-report heldout output is never written (plan 6.11).
- The plan deliberately holds constraints only (3.2.2). This file is the only place that summarises structure.

## Structural summary (HX-1, HX-2, HX-3)

Each family differs from every dev/test family in at least two of: text-layer order, unit placement, flag
placement, column order, font family (checked by `tests/test_eval_heldout.py`).

| ID | Page / font | Column order | Unit | Flag | Text-layer order |
|---|---|---|---|---|---|
| HX-1 | A4, monospaced | name, range, result, unit, flag | own column after the result | trailing column | column-major per panel |
| HX-2 | A4, sans | name with range stacked beneath, result, flag | glued to the result | parenthesised after the value | bottom-up within each row |
| HX-3 | Letter, serif | result, unit, name, range | own column before the name | letter prefix inside the result cell | cells right to left |

All identities are fictional (plan 3.9). Values, scenarios and pagination come from the shared value engine, so only
page structure is heldout-specific.
