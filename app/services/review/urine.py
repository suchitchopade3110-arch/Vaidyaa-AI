"""Legacy urinalysis reader at extraction (N12) and, for scoring, urine entities rebuilt from confirmed rows.

At extraction the legacy reader (Groq NER, regex fallback) only proposes rows; each one needs review and nothing
here queries the knowledge base or applies pattern rules (S1: no result before confirmation).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from app.services.lab_engine.models import ParsedRow
from app.services.lab_engine.tokenizer import parse_range_text
from app.services.review.rows import RowInputError, parse_value_input

logger = logging.getLogger(__name__)

URINE_SECTION = "urine examination"


def legacy_entities(raw_text: str) -> Optional[dict[str, Any]]:
    """The urinalysis entities the pre-P2 product read, when the report is a urinalysis (else None)."""
    try:
        from app.services import clinical_ner_service as ner

        if ner.detect_report_type(raw_text) != "urinalysis":
            return None
        return ner.extract_urine_entities(raw_text)
    except Exception as exc:
        logger.warning("Urinalysis reader skipped: %s", type(exc).__name__)
        return None


def legacy_to_parsed(row: dict[str, Any]) -> Optional[ParsedRow]:
    """One ``_urine_entities_to_rows`` dict -> ParsedRow (name, value or text, unit, printed range, urine specimen)."""
    name = str(row.get("field") or row.get("test") or "").strip()
    raw_value = row.get("value", row.get("result"))
    if not name or raw_value is None or str(raw_value).strip() == "":
        return None
    p = ParsedRow(name=name, unit=(str(row.get("unit") or "").strip() or None), source="text",
                  line=f"{name} {raw_value}", section=URINE_SECTION, specimen="urine")
    try:
        v = parse_value_input(str(raw_value))
        p.value, p.qualifier, p.text_value, p.result_range = v.value_num, v.qualifier, v.text_value, v.result_range
    except RowInputError:
        p.text_value = str(raw_value).strip()
    ref = str(row.get("reference") or "").strip()
    if ref and ref not in ("--", "-"):
        p.ref_text = ref
        rng = parse_range_text(ref) if re.search(r"\d", ref) else None
        if rng and not rng["sex_ranges"]:
            p.ref_low, p.ref_high = rng["lo"], rng["hi"]
            p.ref_strict_low, p.ref_strict_high = rng["strict_lo"], rng["strict_hi"]
    return p


def legacy_rows(raw_text: str) -> list[ParsedRow]:
    """Legacy urine rows (ParsedRow, specimen urine) for a urinalysis report; [] otherwise."""
    entities = legacy_entities(raw_text)
    if not entities:
        return []
    from app import pipeline as _pl

    out = []
    for item in _pl._urine_entities_to_rows(entities):
        p = legacy_to_parsed(item)
        if p is not None:
            out.append(p)
    return out
