"""Specimen inference for review rows (S5, plans/P2.md section 6.5).

``serum`` means any blood specimen (P1 decision). A heading that contains a non-blood word wins over a blood word
("URINE SPOT ELECTROLYTES" is urine). In a report that mixes blood and non-blood specimens a row whose specimen
cannot be told is ``unknown`` (needs review); in a report with no non-blood specimen anywhere it is ``serum``.
"""

from __future__ import annotations

import re
from typing import Optional, Sequence

from app.services.lab_engine.analytes import match_analyte
from app.services.lab_engine.models import ParsedRow

SPECIMENS = ("serum", "urine", "csf", "fluid", "other", "unknown")

# Order matters when a heading holds several non-blood words: csf, then body fluids, then urine, then other.
_NON_BLOOD = (
    ("csf", re.compile(r"\bcsf\b|cerebrospinal|cerebro-spinal", re.I)),
    ("fluid", re.compile(r"\bfluids?\b|pleural|ascitic|peritoneal|synovial|pericardial", re.I)),
    ("urine", re.compile(r"\burine\b|\burinary\b|urinalysis|\b24[\s-]*(?:hr|hrs|hour|hours)\b", re.I)),
    ("other", re.compile(r"\bstool\b|\bsemen\b|\bsputum\b|\bswabs?\b", re.I)),
)
_BLOOD = re.compile(
    r"haematolog|hematolog|biochem|chemistry|\bserum\b|\bplasma\b|\bblood\b|\bcbc\b|haemogram|hemogram|\blipid|"
    r"thyroid|\bliver\b|\blft\b|kidney|renal|\bkft\b|\brft\b|electrolyte|serolog|coagulation|hormone|"
    r"blood gas|\babg\b|diabet|cardiac|\biron\b|vitamin|\bgas\b",
    re.I)


def specimen_from_section(section: str) -> Optional[str]:
    """Specimen named by a section heading, or None when the heading names none."""
    text = section or ""
    for specimen, pattern in _NON_BLOOD:
        if pattern.search(text):
            return specimen
    if _BLOOD.search(text):
        return "serum"
    return None


def specimen_from_key(analyte_key: Optional[str]) -> Optional[str]:
    if not analyte_key:
        return None
    if analyte_key.startswith("urine_") or analyte_key in ("specific_gravity", "urine_ph"):
        return "urine"
    if analyte_key.startswith("csf_"):
        return "csf"
    return None


def _evidence(row: ParsedRow) -> Optional[str]:
    """A non-blood specimen this row names on its own (heading or analyte), else None."""
    non_blood = specimen_from_section(row.section)
    if non_blood is not None and non_blood != "serum":
        return non_blood
    analyte = match_analyte(row.name)
    return specimen_from_key(analyte.key if analyte else None)


def report_has_non_blood(rows: Sequence[ParsedRow]) -> bool:
    return any(_evidence(r) is not None for r in rows)


def infer_specimen(row: ParsedRow, mixed: bool) -> str:
    non_blood = _evidence(row)
    if non_blood is not None:
        return non_blood
    if specimen_from_section(row.section) == "serum":
        return "serum"
    return "unknown" if mixed else "serum"


def assign_specimens(rows: Sequence[ParsedRow]) -> list[str]:
    """Set ``ParsedRow.specimen`` on every row (the engine's S5 floor reads it) and return the values."""
    mixed = report_has_non_blood(rows)
    out = []
    for r in rows:
        r.specimen = infer_specimen(r, mixed)
        out.append(r.specimen)
    return out
