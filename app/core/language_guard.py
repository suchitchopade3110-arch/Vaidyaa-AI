"""REG-01 — non-diagnostic output contract.

Keeps the sellable product outside CDSCO Class C classification (pre-market
approval, ISO 13485 QMS, adverse-event reporting) by construction: nothing
in a response should read as a diagnosis, a patient-specific probability, a
severity ranking, or a recommended clinical action.

What's here: the vocabulary this is measured against, and the constants
routes/services should use going forward.

What's NOT here: this module is not called from anywhere yet. Sweeping the
existing API descriptions, PDF headers, and user-facing copy for the banned
words (README included) is a larger, mostly-mechanical pass — see
scripts/check_prohibited_terms.py for the CI-side half of that, also a stub.
"""

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


def contains_prohibited_term(text: str) -> str | None:
    """Return the first prohibited term found in `text` (case-insensitive),
    or None if it's clean.

    TODO(REG-01): wire this into a CI check (scripts/check_prohibited_terms.py)
    over API response models' descriptions, PDF templates, and README.md,
    and into a pre-response guard in the report/image/claim result builders
    so a bad string can't ship even if the sweep misses a spot.
    """
    lowered = text.lower()
    for term in PROHIBITED_TERMS:
        if term in lowered:
            return term
    return None
