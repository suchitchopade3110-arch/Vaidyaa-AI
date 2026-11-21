.PHONY: audit eval eval-dev eval-full eval-heldout eval-corpus eval-verify eval-baseline

# Scan requirements.txt for known-vulnerable dependency versions.
# Findings are reported only — upgrades are a deliberate, separate decision
# since some pins (celery, redis, torch, etc.) are intentional.
audit:
	pip install --quiet pip-audit
	pip-audit -r requirements.txt

# P1 eval harness (plans/P1.md section 7.2). `eval` is the CI gate (ratchet vs eval/baseline.json, test split);
# EVAL_ARGS passes extra flags (e.g. EVAL_ARGS="--sample 6 --gate none" for a quick local run).
# Exit codes: 0 pass, 1 gate failed, 2 harness/config error (e.g. no baseline), 3 heldout hygiene violation.
EVAL_ARGS ?=

eval:
	python -m tools.eval.run --profile ci $(EVAL_ARGS)

eval-dev:
	python -m tools.eval.run --profile dev $(EVAL_ARGS)

eval-full:
	python -m tools.eval.run --profile full $(EVAL_ARGS)

eval-heldout:
	python -m tools.eval.run --profile heldout $(EVAL_ARGS)

eval-corpus:
	python -m tools.eval.generate --splits dev,test,heldout --renderings all

eval-verify:
	python -m tools.eval.generate --verify --splits dev,test,heldout

eval-baseline:
	python -m tools.eval.run --profile ci --update-baseline $(EVAL_ARGS)
