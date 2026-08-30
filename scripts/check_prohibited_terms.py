#!/usr/bin/env python3
"""REG-01 CI check (stub) — fail the build if banned diagnostic language
appears in user-facing surfaces.

Not wired into CI yet: no step in .github/workflows/ci.yml calls this.
Add one once the sweep below is real; until then this is a documented
starting point, not an active gate.

TODO(REG-01):
  - Walk API response model docstrings/Field descriptions under app/schemas/.
  - Walk PDF template strings in app/services/pdf_report.py.
  - Walk README.md and any other top-level user-facing docs.
  - Exit non-zero and print file:line for every hit so CI fails loudly.
"""
import sys

from app.core.language_guard import contains_prohibited_term

# Placeholder surface list. TODO(REG-01): replace with an actual file walk.
SURFACES_TO_CHECK: list[tuple[str, str]] = []


def main() -> int:
    failures = 0
    for source, text in SURFACES_TO_CHECK:
        hit = contains_prohibited_term(text)
        if hit:
            print(f"{source}: prohibited term {hit!r}")
            failures += 1

    if not SURFACES_TO_CHECK:
        print(
            "check_prohibited_terms: SURFACES_TO_CHECK is empty — this is a "
            "stub (REG-01), not a real sweep. See module docstring."
        )

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
