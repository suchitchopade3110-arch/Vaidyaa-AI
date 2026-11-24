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


# ── scoring side: urine entities rebuilt from confirmed rows ───────────────────

_DIPSTICK = (
    ("leukocyte_esterase", ("leukocyte esterase", "leucocyte esterase", "leukocytes", "leucocytes", "leu")),
    ("nitrite", ("nitrite", "nitrites")),
    ("ketones", ("ketone", "ketones", "ketone bodies", "acetone")),
    ("urobilinogen", ("urobilinogen",)),
    ("bilirubin", ("bilirubin",)),
    ("protein", ("protein", "albumin")),
    ("glucose", ("glucose", "sugar")),
    ("blood", ("blood", "occult blood", "haemoglobin", "hemoglobin")),
    ("ph", ("ph", "reaction")),
    ("specific_gravity", ("specific gravity", "sp gravity", "sp. gravity")),
)
_ABNORMAL_FLAGS = ("POSITIVE", "HIGH", "LOW", "CRITICAL_HIGH", "CRITICAL_LOW")


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9. ]+", " ", (name or "").lower().replace("urine", "")).strip()


def _row_text(r: Any) -> str:
    if r.text_value:
        return str(r.text_value)
    if r.value is None:
        return ""
    return f"{r.value:g}"


def urine_entities_from_rows(scored_rows: list[Any]) -> dict[str, Any]:
    """Entities in the shape ``apply_urine_pattern_rules`` reads, built ONLY from confirmed, engine-scored rows."""
    dipstick: dict[str, dict] = {}
    microscopy: dict[str, Any] = {"casts": {}}
    for r in scored_rows:
        name = _norm(r.display_name)
        text = _row_text(r)
        flag = "abnormal" if r.flag in _ABNORMAL_FLAGS else "normal"
        item = {"value": text, "unit": r.unit or "", "flag": flag}
        if "cast" in name:
            kind = ("rbc_casts" if "rbc" in name or "red" in name else
                    "wbc_casts" if "wbc" in name or "white" in name or "pus" in name else
                    "granular_casts" if "granular" in name else name.replace(" ", "_"))
            microscopy["casts"][kind] = "present" if (r.flag in _ABNORMAL_FLAGS or "present" in text.lower()) else "absent"
            continue
        if re.search(r"\b(pus|wbc|white)\b", name):
            microscopy["wbc"] = item
        elif re.search(r"\b(rbc|red)\b", name) and "cast" not in name:
            microscopy["rbc"] = item
        elif "epithelial" in name:
            microscopy["epithelial_cells"] = item
        elif "bacteria" in name:
            microscopy["bacteria"] = "present" if r.flag in _ABNORMAL_FLAGS else "absent"
        else:
            for key, words in _DIPSTICK:
                if name in words or any(name == w or name.startswith(w + " ") or name.endswith(" " + w) for w in words):
                    dipstick.setdefault(key, item)
                    break
    return {"report_type": "urinalysis", "dipstick": dipstick, "microscopy": microscopy, "quantitative": {},
            "detected_patterns": [], "conditions": [], "urgency": "routine"}
