"""needs_review triggers (S2, plans/P2.md section 6.6).

``review_verdict`` turns one engine-scored row (the preliminary pass) plus its provenance into review codes and
plain-English reasons. A row needs review when ANY code fires; ``review_reason`` joins the texts in priority order.
Reason texts never contain codes. Invariants (tested): every unread line and every row whose preliminary flag is
UNKNOWN or CRITICAL_* needs review; a row without a code does not; the reason is non-empty iff review is needed.

The preliminary flag feeds ONLY these triggers and the internal ``prelim_flag`` column; it is never shown (S1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.services.lab_engine.analytes import REGISTRY
from app.services.lab_engine.models import ScoredRow

OCR_CONFIDENCE_THRESHOLD = 0.90          # FR-1.3
MARKED_PCT = 50.0                        # the engine's HIGH severity: more than 50 % beyond the bound

PRIORITY = (
    "NR-CRITICAL", "NR-UNREAD", "NR-MULTI", "NR-UNIT", "NR-RANGE10", "NR-MARKED", "NR-FLAG", "NR-OCR",
    "NR-SPECIMEN", "NR-UNKNOWN-TEST", "NR-NORANGE", "NR-COLUMN", "NR-REASSEMBLED", "NR-LEGACY-URINE", "NR-INVALID",
    "NR-QUAL", "NR-NOLOCATE", "NR-WARNING", "NR-UNKNOWN",
)

_NUMERIC_TYPES = ("numeric", "qualified", "unit_mismatch")


@dataclass
class RowContext:
    """What the extractor knows about a row besides the engine's verdict."""

    origin: str = "text"                       # text | ocr | column | csv | manual | legacy_urine
    ocr_min_conf: Optional[float] = None       # minimum token confidence of an OCR-read row (or OCR-located legacy row)
    line_count: int = 1                        # physical lines the row was built from
    located: bool = True                       # source_bbox found
    warnings: Sequence[str] = field(default_factory=tuple)   # engine warnings attributed to this row


@dataclass
class ReviewVerdict:
    codes: list[str]
    reasons: list[str]

    @property
    def needs_review(self) -> bool:
        return bool(self.codes)

    @property
    def reason(self) -> Optional[str]:
        return "; ".join(self.reasons) if self.reasons else None


def _critical_limit_in(analyte_key: Optional[str], direction: str) -> bool:
    a = REGISTRY.get(analyte_key) if analyte_key else None
    return bool(a and any(c.direction == direction for c in a.criticals))


def _requires_unit(analyte_key: Optional[str]) -> bool:
    """A registry analyte whose result has a unit (not pH, specific gravity, INR, ratios, indices)."""
    a = REGISTRY.get(analyte_key) if analyte_key else None
    return bool(a and (a.unit or "").strip() and not a.generic)


def unread_verdict() -> ReviewVerdict:
    return ReviewVerdict(["NR-UNREAD"], ["Line could not be read — add it as a result or dismiss it"])


def review_verdict(r: ScoredRow, ctx: Optional[RowContext] = None) -> ReviewVerdict:
    ctx = ctx or RowContext()
    p = r.parsed
    found: dict[str, str] = {}
    numeric = r.value_type in _NUMERIC_TYPES and r.value is not None

    if r.flag.startswith("CRITICAL"):
        found["NR-CRITICAL"] = "Looks critical — please verify"

    if p.candidates or p.ambiguity or r.value_type == "ambiguous":
        n = len(p.candidates or [])
        found["NR-MULTI"] = (f"Two values on this line ({n} candidates)" if n == 2
                             else "Several numbers on this line — which is the result?")

    if numeric:
        unit_blank = not (p.unit or "").strip()
        if unit_blank and _requires_unit(r.analyte_key):
            found["NR-UNIT"] = "Unit not printed"
        elif r.value_type == "unit_mismatch" or "not convertible" in (r.note or ""):
            found["NR-UNIT"] = "Unit not recognised"

        lo, hi = r.ref_low, r.ref_high
        if r.range_source != "none":
            v = r.value
            if (lo is not None and lo > 0 and v < 0.1 * lo) or (hi is not None and hi > 0 and v > 10 * hi):
                found["NR-RANGE10"] = "Value far outside the usual range — check the decimal point and unit"

    if r.flag in ("HIGH", "LOW") and r.severity == "HIGH":
        direction = "above" if r.flag == "HIGH" else "below"
        if not _critical_limit_in(r.analyte_key, direction):
            found["NR-MARKED"] = "Markedly abnormal — please verify"

    if p.printed_flag and r.discrepancy:
        found["NR-FLAG"] = "Printed flag disagrees with the range"

    if ctx.origin in ("ocr", "legacy_urine") and ctx.ocr_min_conf is not None and ctx.ocr_min_conf < OCR_CONFIDENCE_THRESHOLD:
        found["NR-OCR"] = "Hard to read on the scan — check it against the page"

    if p.specimen == "unknown":
        found["NR-SPECIMEN"] = "Specimen unclear — choose blood, urine, CSF or other"

    if r.analyte_key is None:
        found["NR-UNKNOWN-TEST"] = "Test not recognised"

    if r.flag == "UNKNOWN" and r.range_source == "none" and r.value_type != "invalid":
        text = "No reference range for this patient"
        if any("not provided" in w for w in ctx.warnings):
            text += " — it depends on sex/age, which was not given"
        found["NR-NORANGE"] = text

    if ctx.origin == "column":
        found["NR-COLUMN"] = "Read from table columns — check the row"
    if ctx.line_count >= 2 and ctx.origin == "text":
        found["NR-REASSEMBLED"] = "Rebuilt from separate cells — check the values belong together"
    if ctx.origin == "legacy_urine":
        found["NR-LEGACY-URINE"] = "Read by the urinalysis reader — please verify"

    if r.value_type == "invalid":
        found["NR-INVALID"] = "Value is not possible — check it"
    if r.value_type == "qualitative" and r.flag == "UNKNOWN":
        found["NR-QUAL"] = "Result word not understood"

    if ctx.origin not in ("csv", "manual") and not ctx.located:
        found["NR-NOLOCATE"] = "Could not find this on the page — check the source"

    explained = {"NR-INVALID", "NR-NORANGE", "NR-MULTI", "NR-QUAL", "NR-CRITICAL", "NR-FLAG", "NR-UNIT"} & set(found)
    warns = [w for w in [*p.warnings, *ctx.warnings] if w]
    if warns and not explained:
        text = warns[0].split(":", 1)[1].strip() if ":" in warns[0] else warns[0]
        found["NR-WARNING"] = text[:300]

    if r.flag == "UNKNOWN" and not found:
        found["NR-UNKNOWN"] = "Not assessed — check the row"

    codes = [c for c in PRIORITY if c in found]
    return ReviewVerdict(codes, [found[c] for c in codes])


def compute_confidence(origin: str, *, line_count: int = 1, min_token_conf: Optional[float] = None) -> float:
    """Reading confidence of a row (FR-7.1, plans/P2.md section 6.4)."""
    if origin in ("csv", "manual"):
        return 1.0
    if origin == "column":
        return 0.85
    if origin in ("ocr", "legacy_urine"):
        return float(min_token_conf) if min_token_conf is not None else 0.5
    return 1.0 if line_count < 2 else 0.85
