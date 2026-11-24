"""DB-free review rows: payload (de)serialisation, input parsing, the edit merge policy, acceptance rules and the
status transition table (plans/P2.md sections 4.4, 7.2).

``apply_row_edit`` is the ONE function that turns a clinician's change into engine input. The PATCH route and the
eval's ``ConfirmedV1Scorer`` both call it, so what the eval measures is what the product scores.
"""

from __future__ import annotations

import copy
import dataclasses
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.services.lab_engine.evaluate import format_reference
from app.services.lab_engine.models import ParsedRow, RangeOption
from app.services.lab_engine.tokenizer import parse_range_text
from app.services.lab_engine.units import fmt_num

SPECIMEN_VALUES = ("serum", "urine", "csf", "fluid", "other", "unknown")
FLAG_VALUES = ("H", "L", "HH", "LL", "ABN")
CONFIRMABLE_STATUSES = ("pending", "accepted", "edited", "added")
SCORED_STATUSES = ("accepted", "edited", "added")

UNSET: Any = object()          # "field not part of this edit" (None is a real value: clear the field)


class RowInputError(ValueError):
    """A clinician input the review flow refuses. ``code`` is the API error code (HTTP 422 / 409)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# ── DB-free row ──────────────────────────────────────────────────────────────

@dataclass
class RowData:
    """One extracted row or unread line, exactly the ``extracted_rows`` columns without database identity."""

    row_index: int = 0
    kind: str = "row"                       # row | unread_line
    name_raw: str = ""
    analyte_key: Optional[str] = None
    value_raw: Optional[str] = None
    value_num: Optional[float] = None
    qualifier: Optional[str] = None
    text_value: Optional[str] = None
    unit_raw: Optional[str] = None
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    ref_text: Optional[str] = None
    printed_flag: Optional[str] = None
    specimen: str = "unknown"
    confidence: float = 1.0
    needs_review: bool = False
    review_reason: Optional[str] = None
    review_codes: list[str] = field(default_factory=list)
    prelim_flag: Optional[str] = None
    source_page: Optional[int] = None
    source_bbox: Optional[list[float]] = None
    source_text: Optional[str] = None
    origin: str = "text"
    status: str = "pending"
    parsed_payload: Optional[dict] = None
    version: int = 1
    row_id: Optional[str] = None


# ── ParsedRow <-> JSON ───────────────────────────────────────────────────────

def serialize_parsed(p: ParsedRow) -> dict:
    """JSON-safe dict of a whole ParsedRow (tuples become lists)."""
    return _jsonable(dataclasses.asdict(p))


def _jsonable(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def _pair(v: Any) -> Optional[tuple]:
    return None if v is None else (v[0], v[1])


def deserialize_parsed(d: dict) -> ParsedRow:
    """Inverse of ``serialize_parsed``; the engine sees exactly the row the extractor produced."""
    d = copy.deepcopy(d)
    d["range_options"] = [RangeOption(**o) for o in d.get("range_options") or []]
    if d.get("sex_ranges") is not None:
        d["sex_ranges"] = {k: _pair(v) for k, v in d["sex_ranges"].items()}
    if d.get("age_ranges") is not None:
        d["age_ranges"] = [tuple(a) for a in d["age_ranges"]]
    d["result_range"] = _pair(d.get("result_range"))
    known = {f.name for f in dataclasses.fields(ParsedRow)}
    return ParsedRow(**{k: v for k, v in d.items() if k in known})


def value_raw_from_parsed(p: ParsedRow) -> Optional[str]:
    """The result as read, as text: "6.9", "<0.01", "2-3", "Reactive"."""
    if p.value is None:
        return p.text_value
    if p.result_range is not None:
        return f"{fmt_num(p.result_range[0])}-{fmt_num(p.result_range[1])}"
    return f"{p.qualifier or ''}{fmt_num(p.value)}"


# ── value input ──────────────────────────────────────────────────────────────

@dataclass
class ValueInput:
    value_raw: str
    value_num: Optional[float] = None
    qualifier: Optional[str] = None
    text_value: Optional[str] = None
    result_range: Optional[tuple[float, float]] = None


_QUAL = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "≤": "<=", "≥": ">="}
_NUMBER = re.compile(r"^(?P<q><=|>=|<|>|≤|≥)?\s*(?P<n>[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)$")
_NUMERICISH = re.compile(r"^[<>=≤≥\s]*[+-]?[\d.,]*\d[\d.,]*$")
_RANGE = re.compile(r"^(?P<a>\d+(?:\.\d+)?)\s*-\s*(?P<b>\d+(?:\.\d+)?)$")
_TITRE = re.compile(r"^1\s*:\s*\d+$")
_MAX_ABS = 1e9


def parse_value_input(text: Any) -> ValueInput:
    """Clinician-typed result -> structured value. See plans/P2.md section 4.4 for the accepted forms."""
    raw = ("" if text is None else str(text)).strip()
    if not raw:
        raise RowInputError("VALUE_REQUIRED", "Enter a result.")
    if "," in raw and _NUMERICISH.match(raw):
        raise RowInputError("INVALID_VALUE", "Use a decimal point, without thousands separators.")
    if re.fullmatch(r"[+-]?(?:inf(?:inity)?|nan)", raw, re.I):
        raise RowInputError("INVALID_VALUE", "That number is not a possible result.")
    m = _NUMBER.match(raw)
    if m:
        value = float(m.group("n"))
        if not math.isfinite(value) or abs(value) >= _MAX_ABS:
            raise RowInputError("INVALID_VALUE", "That number is not a possible result.")
        return ValueInput(raw, value_num=value, qualifier=_QUAL.get(m.group("q") or ""))
    m = _RANGE.match(raw)
    if m:
        a, b = float(m.group("a")), float(m.group("b"))
        if a > b or b >= _MAX_ABS:
            raise RowInputError("INVALID_VALUE", "A result range must run from low to high.")
        return ValueInput(raw, value_num=b, result_range=(a, b))
    if _TITRE.match(raw):
        return ValueInput(raw, text_value=re.sub(r"\s+", "", raw))
    if re.search(r"[A-Za-z]", raw):
        return ValueInput(raw, text_value=raw)
    raise RowInputError("INVALID_VALUE", "That is not a result the system can read.")


# ── the edit merge policy (plans/P2.md 7.2) ──────────────────────────────────

def _clean_name(name: Any) -> str:
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    if not (1 <= len(n) <= 120):
        raise RowInputError("NAME_REQUIRED", "A test name of 1 to 120 characters is required.")
    return n


def _strictness(ref_text: str) -> tuple[bool, bool]:
    t = ref_text.strip()
    return (t.startswith(">") and not t.startswith(">="), t.startswith("<") and not t.startswith("<="))


def _check_range(lo: Optional[float], hi: Optional[float]) -> None:
    for v in (lo, hi):
        if v is not None and (not math.isfinite(float(v)) or abs(float(v)) >= _MAX_ABS):
            raise RowInputError("INVALID_RANGE", "The reference range must be a finite number.")
    if lo is not None and hi is not None and lo > hi:
        raise RowInputError("INVALID_RANGE", "The low end of the range must not be above the high end.")


def apply_row_edit(parsed: Optional[ParsedRow], *, name_raw: Any = UNSET, value_raw: Any = UNSET,
                   value: Optional[ValueInput] = None, unit_raw: Any = UNSET, ref_low: Any = UNSET,
                   ref_high: Any = UNSET, ref_text: Any = UNSET, printed_flag: Any = UNSET,
                   specimen: Any = UNSET) -> ParsedRow:
    """A copy of ``parsed`` (a blank row when None) with the given fields changed per the merge policy.

    ``value`` passes an already structured result (the eval does, so decimal-comma text is never re-parsed);
    ``value_raw`` is parsed with ``parse_value_input``. Any edit clears the extractor's warnings."""
    p = copy.deepcopy(parsed) if parsed is not None else ParsedRow(name="", source="manual")
    touched = False

    if name_raw is not UNSET:
        p.name = _clean_name(name_raw)
        touched = True

    if value is None and value_raw is not UNSET:
        value = parse_value_input(value_raw)
    if value is not None:
        p.value, p.qualifier, p.text_value, p.result_range = (
            value.value_num, value.qualifier, value.text_value, value.result_range)
        p.candidates, p.ambiguity, p.note = None, None, ""
        touched = True

    if unit_raw is not UNSET:
        unit = (str(unit_raw).strip() if unit_raw is not None else "")
        if len(unit) > 32:
            raise RowInputError("INVALID_UNIT", "The unit is too long.")
        p.unit = unit or None
        p.ref_unit = None
        touched = True

    if ref_low is not UNSET or ref_high is not UNSET or ref_text is not UNSET:
        lo = p.ref_low if ref_low is UNSET else ref_low
        hi = p.ref_high if ref_high is UNSET else ref_high
        text = p.ref_text if ref_text is UNSET else ref_text
        strict_lo, strict_hi = p.ref_strict_low, p.ref_strict_high
        if ref_text is not UNSET and ref_low is UNSET and ref_high is UNSET:
            rng = parse_range_text(text or "") if text else None
            lo, hi = (rng["lo"], rng["hi"]) if rng and not rng["sex_ranges"] else (None, None)
            strict_lo, strict_hi = (rng["strict_lo"], rng["strict_hi"]) if rng and not rng["sex_ranges"] else (False, False)
        elif text:
            strict_lo, strict_hi = _strictness(text)
        else:
            strict_lo = strict_hi = False
        _check_range(lo, hi)
        p.ref_low, p.ref_high, p.ref_strict_low, p.ref_strict_high = lo, hi, strict_lo, strict_hi
        if ref_text is UNSET or not text:
            text = format_reference(lo, hi, strict_lo, strict_hi) if (lo is not None or hi is not None) else None
        p.ref_text = text or None
        p.sex_ranges, p.age_ranges, p.range_options = None, None, []
        p.interpretive_only, p.ref_unit = False, None
        touched = True

    if printed_flag is not UNSET:
        flag = (str(printed_flag).strip().upper() if printed_flag else None) or None
        if flag is not None and flag not in FLAG_VALUES:
            raise RowInputError("INVALID_FLAG", "The printed flag must be H, L, HH, LL or ABN.")
        p.printed_flag = flag
        touched = True

    if specimen is not UNSET:
        if specimen not in SPECIMEN_VALUES:
            raise RowInputError("INVALID_SPECIMEN", "Choose blood, urine, CSF, fluid or other.")
        p.specimen = specimen
        touched = True

    if touched:
        p.warnings = []
    return p


def build_manual_row(*, name_raw: Any, value_raw: Any = UNSET, value: Optional[ValueInput] = None,
                     unit_raw: Any = None, ref_low: Any = None, ref_high: Any = None, ref_text: Any = None,
                     printed_flag: Any = None, specimen: Any = None,
                     allow_unknown_specimen: bool = False) -> ParsedRow:
    """A clinician-added result. The specimen is required: a urine value defaulted to serum could fire a serum
    critical (S5)."""
    if specimen not in SPECIMEN_VALUES or (specimen == "unknown" and not allow_unknown_specimen):
        raise RowInputError("SPECIMEN_REQUIRED", "Say whether this result is from blood, urine, CSF, fluid or other.")
    if value is None and value_raw is UNSET:
        raise RowInputError("VALUE_REQUIRED", "Enter a result.")
    kwargs: dict[str, Any] = dict(name_raw=name_raw, unit_raw=unit_raw, printed_flag=printed_flag, specimen=specimen)
    if value is not None:
        kwargs["value"] = value
    else:
        kwargs["value_raw"] = value_raw
    if ref_low is not None or ref_high is not None or ref_text is not None:
        kwargs.update(ref_low=ref_low, ref_high=ref_high, ref_text=ref_text)
    p = apply_row_edit(ParsedRow(name="", source="manual"), **kwargs)
    p.line = ""
    return p


# ── acceptance (S6 / S5 / S2) ────────────────────────────────────────────────

def acceptance_errors(row: RowData) -> list[str]:
    """Why this row may not be accepted as it stands (codes; empty = acceptable)."""
    errors: list[str] = []
    if row.value_num is None and not (row.text_value or "").strip():
        errors.append("VALUE_REQUIRED")
    if "NR-UNIT" in (row.review_codes or []) and not (row.unit_raw or "").strip():
        errors.append("UNIT_REQUIRED")
    if "NR-SPECIMEN" in (row.review_codes or []) and row.specimen == "unknown":
        errors.append("SPECIMEN_REQUIRED")
    return errors


# ── status transitions (plans/P2.md 4.4 table) ───────────────────────────────

STATUS_REQUESTS = ("accepted", "deleted", "pending")


def next_status(*, kind: str, current: str, requested: str, errors: Sequence[str] = (),
                has_field_edits: bool = False, manual: bool = False) -> str:
    """The status after a client's status request, or a RowInputError.

    | from \\ to           | accepted                 | deleted | pending (restore)                              |
    | pending (row)        | if acceptance_errors = []| yes     | no                                             |
    | pending (unread)     | no (convert or dismiss)  | yes     | no                                             |
    | accepted             | no                       | yes     | yes                                            |
    | edited / added       | no                       | yes     | no                                             |
    | deleted              | no                       | no      | edited if field edits, added if manual, pending|
    """
    invalid = RowInputError("INVALID_TRANSITION", f"A row cannot go from {current} to {requested}.")
    if requested not in STATUS_REQUESTS:
        raise invalid
    if requested == "deleted":
        if current == "deleted":
            raise invalid
        return "deleted"
    if requested == "accepted":
        if current != "pending" or kind != "row":
            raise invalid
        if errors:
            raise RowInputError(errors[0], "This row cannot be accepted as it stands.")
        return "accepted"
    # requested == "pending"
    if current == "accepted":
        return "pending"
    if current == "deleted":
        if manual:
            return "added"
        return "edited" if has_field_edits else "pending"
    raise invalid
