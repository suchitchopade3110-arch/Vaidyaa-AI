"""Unit tests for REG-01's app/core/language_guard.py.

No DB/server needed — this module is pure string processing.
"""
from __future__ import annotations

from app.core.language_guard import contains_prohibited_term, scan_text


def test_clean_text_has_no_findings():
    assert scan_text("The retrieval confidence for this claim is high.") == []
    assert contains_prohibited_term("Nothing prohibited here.") is None


def test_flags_a_real_violation():
    hit = contains_prohibited_term("This tool provides a diagnosis for pneumonia.")
    assert hit == "diagnosis"


def test_disclaimer_is_not_a_violation():
    """The exact pattern this module exists to allow: saying what the
    system is NOT."""
    text = "AI-ASSISTED ANALYSIS — NOT A MEDICAL DIAGNOSIS. Consult a professional."
    assert scan_text(text) == []


def test_do_not_diagnose_is_not_a_violation():
    assert scan_text("Do not diagnose.") == []


def test_negation_in_an_earlier_unrelated_clause_does_not_shield_a_later_violation():
    """Regression test: an early, unrelated "no" (e.g. "no Celery
    job/polling") must not disclaim a real violation two sentences later —
    caught during development of this scanner."""
    text = (
        "Run the pipeline synchronously (no Celery job/polling).\n\n"
        "Returns diagnosis, confidence, evidence."
    )
    findings = scan_text(text)
    assert len(findings) == 1
    assert findings[0][1] == "diagnosis"


def test_detection_and_screening_are_flagged_outside_disclaimers():
    assert contains_prohibited_term("Our screening tool detects disease early.") in {
        "screening",
        "detect",
    }


def test_isnt_a_screening_tool_is_not_a_violation():
    assert scan_text("This isn't a screening tool.") == []


def test_multiple_findings_returned_in_order():
    findings = scan_text("diagnosis and detection and screening")
    terms = [term for _offset, term in findings]
    assert terms == ["diagnosis", "detect", "detection", "screening"]
