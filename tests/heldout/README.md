> DO NOT READ when working on extraction, the engine or templates (FR-5.4). Reading this folder, `tools/eval/layouts/heldout/` or `eval/corpus/heldout/` to write rules
> invalidates the heldout metric.

These tests check that the heldout-only layout families are structurally different from the shared families
(P1 plan 3.2.2). They describe the heldout layouts, so they live here instead of `tests/test_eval_heldout.py`,
which P3 engine builders read (P1 review LOW-6). Engine-builder prompts must repeat the notice above.
