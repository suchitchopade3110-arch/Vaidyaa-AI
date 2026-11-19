.PHONY: audit eval eval-full eval-verify eval-baseline

# Scan requirements.txt for known-vulnerable dependency versions.
# Findings are reported only — upgrades are a deliberate, separate decision
# since some pins (celery, redis, torch, etc.) are intentional.
audit:
	pip install --quiet pip-audit
	pip-audit -r requirements.txt

# P1 eval harness (plans/P1.md section 7.2). Placeholders until run.py is implemented:
# each target exits 2 with "not implemented yet (P1 in progress)".
EVAL_ARGS ?=

eval:
	python -m tools.eval.run --profile ci $(EVAL_ARGS)

eval-full:
	python -m tools.eval.run --profile full $(EVAL_ARGS)

eval-verify:
	python -m tools.eval.run --verify --splits dev,test,heldout $(EVAL_ARGS)

eval-baseline:
	python -m tools.eval.run --profile ci --update-baseline $(EVAL_ARGS)
