"""REG-01 — non-diagnostic output contract.

Keeps the sellable product outside CDSCO Class C classification (pre-market
approval, ISO 13485 QMS, adverse-event reporting) by construction: nothing
in a response should read as a diagnosis, a patient-specific probability, a
severity ranking, or a recommended clinical action.

`scan_text` is the real detector, used by scripts/check_prohibited_terms.py
(the CI-side sweep over README.md, route docstrings/summaries, and schema
Field descriptions — the surfaces that are safe to mechanically police
without touching pipeline behavior).

Deliberately NOT swept here, and not something this module can safely
catch: prohibited terms inside LLM prompts, internal module/function names,
and NER label taxonomies borrowed from a pretrained model's own vocabulary
("problem" / "diagnosis" as a spaCy/med7 entity type, say) — renaming those
is unrelated churn, not user-facing copy. The one item that WAS in this
category — JSON response field names like `"diagnosis"`/`"primary_
diagnosis"` and the LLM prompt asking for them
(app/services/differential_diagnosis.py, app/services/pipeline_
controller.py, app/routes/text_routes.py) — has been fixed now that PLT-04
ported the frontend off the old field names, unblocking the breaking API
change. See docs/PHASE1_SKELETON.md for the full account.
"""
import re

# Case-insensitive substring match. Kept short and specific on purpose —
# a broad list produces false positives on legitimate clinical vocabulary
# ("this may indicate a differential" reads fine; "diagnosis: pneumonia"
# does not).
PROHIBITED_TERMS = (
    "diagnosis",
    "diagnose",
    "diagnostic confidence",
    "detect",
    "detection",
    "screening",
)

# Use this instead of a disease name/probability pair in image findings.
FINDING_REVIEW_LABEL = "finding requires clinician review"

# Use this instead of "diagnostic confidence" / "confidence" anywhere a
# score is surfaced to a user. The number itself doesn't change — only the
# label attached to it. See REG-01 acceptance criteria.
CONFIDENCE_LABEL = "retrieval/extraction confidence"

# Matched immediately before a prohibited term, this means the sentence is
# disclaiming it ("NOT a medical diagnosis", "Do not diagnose", "isn't a
# screening tool") rather than claiming it — which is exactly the
# compliant pattern REG-01 wants in a medical disclaimer. A blanket ban on
# the word itself would make it impossible to say "this is not a
# diagnosis," which defeats the point.
_DISCLAIMER_PATTERN = re.compile(r"\b(not|no|never|isn't|aren't|don't|doesn't)\b", re.IGNORECASE)
_DISCLAIMER_WINDOW_CHARS = 20
# A negation only disclaims a term in the same clause — stop the window at
# the nearest sentence/line break so an unrelated "no" earlier in the
# paragraph ("no Celery job/polling. Returns diagnosis...") can't shield a
# real violation two sentences later.
_CLAUSE_BREAK = re.compile(r"[.\n]")


def contains_prohibited_term(text: str) -> str | None:
    """Return the first prohibited term found in `text` (case-insensitive,
    disclaiming uses excluded), or None if it's clean."""
    findings = scan_text(text)
    return findings[0][1] if findings else None


def scan_text(text: str) -> list[tuple[int, str]]:
    """Return every (character offset, term) prohibited-term match in
    `text`, skipping ones that read as a disclaimer (see
    _DISCLAIMER_PATTERN above)."""
    lowered = text.lower()
    findings: list[tuple[int, str]] = []
    for term in PROHIBITED_TERMS:
        for match in re.finditer(r"\b" + re.escape(term), lowered):
            window_start = max(0, match.start() - _DISCLAIMER_WINDOW_CHARS)
            window = lowered[window_start:match.start()]
            last_break = list(_CLAUSE_BREAK.finditer(window))
            if last_break:
                window = window[last_break[-1].end():]
            if _DISCLAIMER_PATTERN.search(window):
                continue
            findings.append((match.start(), term))
    return findings
