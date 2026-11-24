"""Ground-truth dataclasses and hand-written validator for the GT JSON schema (P1 plan section 4.1).

No jsonschema dependency; ``validate_gt`` returns a list of human-readable errors (empty = valid).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

GT_SCHEMA = "vaidya-eval-gt/1"

FLAGS = frozenset({
    "NORMAL", "HIGH", "LOW", "CRITICAL_HIGH", "CRITICAL_LOW", "POSITIVE", "NEGATIVE", "INFO", "UNKNOWN",
})
VALUE_TYPES = frozenset({"numeric", "qualitative", "titre", "range_result", "info"})
SPECIMENS = frozenset({"serum", "urine", "csf", "fluid", "other"})
UNIT_PLACEMENTS = frozenset({"cell", "header", "inline", "none"})
RANGE_KINDS = frozenset({"two_sided", "upper", "lower", "sex", "age", "interpretive", "cutoff", "none"})
HEADLINES = frozenset({"critical", "abnormal", "normal"})
AMB_CODES = frozenset({
    "AMB_UNITLESS_COUNT", "AMB_SEX_RANGES_NO_SEX", "AMB_AGE_RANGES_NO_AGE", "AMB_NO_RANGE_NO_DEMOGRAPHICS",
    "AMB_TITRE_NO_CUTOFF", "AMB_UNLABELLED_MULTIVALUE", "AMB_PRINTED_FLAG_CONFLICT",
})
SPLITS = frozenset({"dev", "test", "heldout"})


@dataclass
class GTRange:
    kind: str = "none"
    low: float | None = None
    high: float | None = None
    text: str = ""
    options: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GTRow:
    row_id: str
    page: int
    bbox: list[float]
    line_text: str
    name: str
    analyte_key: str | None
    key_optional: bool
    specimen: str
    section: str
    value_type: str
    value: float | None
    value_text: str
    qualifier: str | None
    text_value: str | None
    unit: str
    unit_placement: str
    unit_true: str
    range: GTRange
    effective_range_page: dict[str, Any] | None
    effective_range_true: dict[str, Any] | None
    printed_flag: str
    flag: str
    flag_true: str
    is_critical: bool
    critical_rule: str | None
    canonical: dict[str, Any]
    expected_needs_review: bool
    ambiguity: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    previous: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class GTReport:
    report_id: str
    split: str
    family: str
    template_id: str | None
    seed: int
    generator_version: str
    pages: list[dict[str, Any]]
    lab: dict[str, Any]
    patient: dict[str, Any]
    scenario: dict[str, Any]
    unit_system: str
    sections: list[dict[str, Any]]
    rows: list[GTRow]
    decoys: list[dict[str, Any]]
    expected: dict[str, Any]
    schema: str = GT_SCHEMA

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["rows"] = [r.to_dict() for r in self.rows]
        return d


_REPORT_KEYS = (
    "schema", "report_id", "split", "family", "template_id", "seed", "generator_version", "pages", "lab", "patient",
    "scenario", "unit_system", "sections", "rows", "decoys", "expected",
)
_ROW_KEYS = (
    "row_id", "page", "bbox", "line_text", "name", "analyte_key", "key_optional", "specimen", "section", "value_type",
    "value", "value_text", "qualifier", "text_value", "unit", "unit_placement", "unit_true", "range",
    "effective_range_page", "effective_range_true", "printed_flag", "flag", "flag_true", "is_critical",
    "critical_rule", "canonical", "expected_needs_review", "ambiguity", "features", "previous",
)


def validate_gt(d: dict[str, Any]) -> list[str]:
    """Validate a GT dict (as loaded from JSON). Returns a list of errors."""
    errs: list[str] = []
    for k in _REPORT_KEYS:
        if k not in d:
            errs.append(f"report: missing key {k}")
    if errs:
        return errs
    if d["schema"] != GT_SCHEMA:
        errs.append(f"report: schema {d['schema']!r} != {GT_SCHEMA!r}")
    if d["split"] not in SPLITS:
        errs.append(f"report: bad split {d['split']!r}")
    exp = d["expected"]
    if exp.get("headline") not in HEADLINES:
        errs.append("expected.headline invalid")
    for i, r in enumerate(d["rows"]):
        rid = r.get("row_id", f"#{i}")
        for k in _ROW_KEYS:
            if k not in r:
                errs.append(f"row {rid}: missing key {k}")
        if any(k not in r for k in _ROW_KEYS):
            continue
        if r["flag"] not in FLAGS:
            errs.append(f"row {rid}: flag {r['flag']!r} not allowed")
        if r["flag_true"] not in FLAGS or r["flag_true"] == "UNKNOWN":
            errs.append(f"row {rid}: flag_true {r['flag_true']!r} must be a determinate flag")
        if r["value_type"] not in VALUE_TYPES:
            errs.append(f"row {rid}: bad value_type")
        if r["specimen"] not in SPECIMENS:
            errs.append(f"row {rid}: bad specimen")
        if r["unit_placement"] not in UNIT_PLACEMENTS:
            errs.append(f"row {rid}: bad unit_placement")
        if r["range"].get("kind") not in RANGE_KINDS:
            errs.append(f"row {rid}: bad range kind")
        if r["is_critical"] and r["specimen"] != "serum":
            errs.append(f"row {rid}: is_critical on non-blood specimen")
        for code in r["ambiguity"]:
            if code not in AMB_CODES:
                errs.append(f"row {rid}: unknown ambiguity code {code}")
        if bool(r["ambiguity"]) != bool(r["expected_needs_review"]):
            errs.append(f"row {rid}: expected_needs_review must equal (ambiguity non-empty)")
        if r["flag"] == "UNKNOWN" and not r["ambiguity"]:
            errs.append(f"row {rid}: UNKNOWN flag without an ambiguity code")
        if len(r["bbox"]) != 4:
            errs.append(f"row {rid}: bbox must have 4 numbers")
    return errs
