"""Adapter and scorer protocols and the shared result dataclasses (P1 plan section 6.2: the P2 swap point).

Adapter-side module. It imports nothing from ``app``; the adapters that do (``pipeline_v0``) import it lazily.

Judge-side modules (``match``, ``review_sim``, ``metrics``, ...) must never import this package (GN-11, plan 2.2).
They work on these objects by duck typing: every field below is read with plain attribute access, so a judge-side
record with the same attribute names (for example the confirmed rows ``review_sim`` builds) is accepted by any
``Scorer``. Likewise ``ScoreResultLike`` is the structural contract for a score: ``ScoreResult`` (adapter side) and
``tools.eval.clinical.OracleScore`` (the judge-side oracle, T03) both satisfy it, which is how the two are reconciled
without an import in either direction.

``ExtractedRow`` mirrors the FR-1.2 ``extracted_rows`` columns so P2's ``ExtractedRowsAdapter`` maps 1:1, and
``ConfirmedRow`` carries the same value fields plus the review origin. Changing the adapter or scorer never changes
GT, matching or metric definitions (it does invalidate ``eval/baseline.json``).
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol, Sequence, runtime_checkable

# ---------------------------------------------------------------- vocabularies

ROW_KINDS = ("row", "unread_line")
SPECIMENS = ("serum", "urine", "csf", "fluid", "other", "unknown")
EXTRACTED_ORIGINS = ("text", "ocr", "column", "csv", "legacy_urine", "unread_line", "manual")
CONFIRMED_ORIGINS = ("accepted", "edited", "added")
STATUSES = ("ok", "failed", "empty")
HEADLINES = ("critical", "abnormal", "normal", "unknown")

# product risk label (lower-cased) -> headline (plan 6.3.2 step 8); anything else is "unknown"
_LABEL_TO_HEADLINE = {
    "critical": "critical",
    "high": "abnormal",
    "moderate": "abnormal",
    "medium": "abnormal",
    "low": "normal",
    "normal": "normal",
    "unknown": "unknown",
}


def headline_from_label(label: Any) -> str:
    """Map a product risk label to a three-level headline (plus unknown).

    critical -> critical; high, moderate, medium -> abnormal; low, normal -> normal; unknown, None or anything
    unrecognised -> unknown (the conservative reading: an unrecognised label is never read as normal)."""
    if label is None:
        return "unknown"
    return _LABEL_TO_HEADLINE.get(str(label).strip().lower(), "unknown")


class Reason(NamedTuple):
    """A review reason, ``(code, text)``. Serialises as a two-element list."""

    code: str
    text: str


# ---------------------------------------------------------------- job

@dataclass(frozen=True)
class EvalJob:
    """One unit of evaluation (plan 6.1): a whole report, one photo page, or one CSV.

    ``files`` holds exactly one input path; ``pages`` the GT pages in scope (all pages, or ``[n]`` for a photo job).
    ``sex`` / ``age`` are the GT *input* demographics (what the clinician entered): 'male' | 'female' | None."""

    job_id: str
    report_id: str
    split: str
    rendering: str
    files: tuple[str, ...]
    pages: tuple[int, ...] = ()
    sex: str | None = None
    age: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(str(f) for f in self.files))
        object.__setattr__(self, "pages", tuple(int(p) for p in self.pages))
        if self.sex not in (None, "male", "female"):
            raise ValueError(f"EvalJob.sex must be 'male', 'female' or None, got {self.sex!r}")


# ---------------------------------------------------------------- extraction

@dataclass
class ExtractedRow:
    """One row as the product shows it before review (FR-1.2 ``extracted_rows``).

    ``ref_low`` / ``ref_high`` are the effective printed range for this patient in the printed unit; ``flag`` is the
    preliminary computed flag shown before review. ``payload`` is opaque (the v0 ``ParsedRow`` or legacy dict) and
    is never serialised or compared."""

    row_index: int
    kind: str = "row"
    name_raw: str = ""
    analyte_key: str | None = None
    value_raw: str = ""
    value_num: float | None = None
    qualifier: str | None = None
    text_value: str | None = None
    unit_raw: str = ""
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: str | None = None
    printed_flag: str | None = None
    specimen: str = "unknown"
    flag: str = "UNKNOWN"
    needs_review: bool = False
    review_reasons: list[Reason] = field(default_factory=list)
    confidence: float | None = None
    origin: str = "text"
    source_page: int | None = None
    source_text: str = ""
    source_bbox: list[float] | None = None  # FR-1.2 column; v0 has no geometry, P2 fills it
    payload: Any = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return _plain(self, exclude=("payload",))


@dataclass
class ExtractionResult:
    """Everything one adapter run produced for one job (plan 6.2).

    ``meta`` is deterministic (OCR methods per page, panel type, OCR confidence). ``timing`` holds everything that
    is not (wall times, OCR cache hits and misses) and, like ``payload``, is excluded from equality."""

    job_id: str
    status: str
    rows: list[ExtractedRow] = field(default_factory=list)
    report_review_items: list[Reason] = field(default_factory=list)
    review_all: bool = False
    pre_review_headline: str = "unknown"
    product_label: str | None = None
    raw_text: str = ""
    adapter: str = ""
    adapter_version: str = ""
    meta: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict, compare=False)
    payload: Any = field(default=None, repr=False, compare=False)

    @property
    def extracted(self) -> list[ExtractedRow]:
        """Rows of kind ``row`` (E_j in plan 6.8)."""
        return [r for r in self.rows if r.kind == "row"]

    @property
    def unread_lines(self) -> list[ExtractedRow]:
        """Rows of kind ``unread_line`` (U_j in plan 6.8). Always empty for v0."""
        return [r for r in self.rows if r.kind == "unread_line"]

    def to_dict(self, *, include_timing: bool = False) -> dict[str, Any]:
        """Plain, JSON-ready dict; ``payload`` is never included, ``timing`` only on request."""
        exclude = ("payload",) if include_timing else ("payload", "timing")
        return _plain(self, exclude=exclude)


# ---------------------------------------------------------------- confirmed rows and scoring

@dataclass
class ConfirmedRow:
    """A row after (simulated) review: the value fields of ``ExtractedRow`` plus the review origin.

    ``origin`` is accepted (as extracted; ``payload`` is the extracted row's payload), edited (replaced by the true
    values) or added (an omission the clinician typed in). ``extracted_index`` links back to the extracted row."""

    name_raw: str = ""
    analyte_key: str | None = None
    value_raw: str = ""
    value_num: float | None = None
    qualifier: str | None = None
    text_value: str | None = None
    unit_raw: str = ""
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: str | None = None
    printed_flag: str | None = None
    specimen: str = "unknown"
    origin: str = "accepted"
    extracted_index: int | None = None
    payload: Any = field(default=None, repr=False, compare=False)

    @classmethod
    def from_extracted(cls, row: Any, origin: str = "accepted") -> "ConfirmedRow":
        """Accept an extracted row as is (duck-typed: any object with the ExtractedRow value fields)."""
        values = {f: getattr(row, f, None) for f in CONFIRMED_VALUE_FIELDS}
        values["unit_raw"] = values["unit_raw"] or ""
        values["value_raw"] = values["value_raw"] or ""
        values["name_raw"] = values["name_raw"] or ""
        values["specimen"] = values["specimen"] or "unknown"
        return cls(**values, origin=origin, extracted_index=getattr(row, "row_index", None),
                   payload=getattr(row, "payload", None))

    def to_dict(self) -> dict[str, Any]:
        return _plain(self, exclude=("payload",))


CONFIRMED_VALUE_FIELDS = (
    "name_raw", "analyte_key", "value_raw", "value_num", "qualifier", "text_value", "unit_raw", "ref_low",
    "ref_high", "ref_text", "printed_flag", "specimen",
)


@runtime_checkable
class ScoreResultLike(Protocol):
    """The structural contract judge-side code relies on for a score (``ScoreResult`` and ``OracleScore``)."""

    headline: str
    row_flags: list[str]
    label_raw: str


@dataclass
class ScoreResult:
    """A scorer's verdict on a confirmed row set.

    ``headline`` is critical | abnormal | normal | unknown; ``row_flags[i]`` is the flag of input row ``i``;
    ``label_raw`` is the scorer's own label before mapping."""

    headline: str
    row_flags: list[str] = field(default_factory=list)
    label_raw: str = ""

    @classmethod
    def coerce(cls, score: Any) -> "ScoreResult":
        """Any ``ScoreResultLike`` (e.g. the judge-side ``OracleScore``) as a ``ScoreResult``."""
        if isinstance(score, cls):
            return score
        if not isinstance(score, ScoreResultLike):
            raise TypeError(f"{type(score).__name__} is not ScoreResultLike (needs headline, row_flags, label_raw)")
        return cls(str(score.headline), list(score.row_flags), str(score.label_raw or ""))


# ---------------------------------------------------------------- protocols

@runtime_checkable
class ExtractionAdapter(Protocol):
    name: str
    version: str

    def extract(self, job: EvalJob) -> ExtractionResult: ...


@runtime_checkable
class Scorer(Protocol):
    name: str
    version: str

    def score(self, rows: Sequence[Any], sex: str | None = None, age: int | None = None) -> ScoreResultLike: ...


# ---------------------------------------------------------------- helpers

def _plain(obj: Any, exclude: tuple[str, ...] = ()) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for f in dataclasses.fields(obj):
        if f.name in exclude:
            continue
        out[f.name] = _plain_value(getattr(obj, f.name))
    return out


def _plain_value(v: Any) -> Any:
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        to_dict = getattr(v, "to_dict", None)
        return to_dict() if callable(to_dict) else _plain(v)
    if isinstance(v, tuple) and hasattr(v, "_fields"):  # NamedTuple (Reason)
        return [_plain_value(x) for x in v]
    if isinstance(v, (list, tuple)):
        return [_plain_value(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _plain_value(x) for k, x in v.items()}
    return v


__all__ = [
    "CONFIRMED_ORIGINS", "CONFIRMED_VALUE_FIELDS", "EXTRACTED_ORIGINS", "HEADLINES", "ROW_KINDS", "SPECIMENS",
    "STATUSES", "ConfirmedRow", "EvalJob", "ExtractedRow", "ExtractionAdapter", "ExtractionResult", "Reason",
    "ScoreResult", "ScoreResultLike", "Scorer", "headline_from_label",
]
