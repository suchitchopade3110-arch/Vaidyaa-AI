"""Plain dataclasses shared by the lab engine modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RangeOption:
    """One printed range with the label printed next to it ("Adults: 13.0-17.0",
    "Deficiency: <20", "0-14 yrs: 150-420", "Male: 13.5-17.5"), classified by
    tokenizer.classify_label():
      plain    unlabelled, or labelled neutral / normal-like ("Ref", "Normal", "Desirable", "Sufficiency")
      sex      applies to one sex
      age      applies to an age band (age_lo..age_hi, years)
      category interpretive category ("Deficiency", "Borderline", "Kidney failure") - never a reference range
    """

    kind: str
    label: str
    lo: Optional[float]
    hi: Optional[float]
    strict_lo: bool = False
    strict_hi: bool = False
    text: str = ""
    sex: Optional[str] = None
    age_lo: Optional[float] = None
    age_hi: Optional[float] = None


@dataclass
class ParsedRow:
    """What an extractor saw on the report (no clinical judgement yet)."""

    name: str                              # as printed (cleaned of leading/trailing punctuation)
    value: Optional[float] = None          # numeric result
    qualifier: Optional[str] = None        # "<", ">", "<=", ">=" printed before the value
    text_value: Optional[str] = None       # qualitative result ("Reactive", "Positive", "Nil")
    unit: Optional[str] = None             # as printed
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None       # one-sided "<5.7" -> (None, 5.7); ">40" -> (40, None)
    ref_text: Optional[str] = None         # the printed range text, verbatim
    printed_flag: Optional[str] = None     # "H", "L", "HH", "LL", "ABN"
    source: str = "text"                   # "text" | "csv" | "column_parser"
    line: str = ""                         # original line(s), for audit
    # additions beyond the plan's minimum (all optional)
    ref_strict_low: bool = False           # printed ">x": x itself is NOT normal
    ref_strict_high: bool = False          # printed "<x": x itself is NOT normal
    ref_unit: Optional[str] = None         # unit printed next to the range, when different from the result's
    sex_ranges: Optional[Dict[str, Tuple[Optional[float], Optional[float]]]] = None  # "Male: a - b Female: c - d"
    warnings: List[str] = field(default_factory=list)
    # ---- reader additions for labelled ranges / ambiguity / context (all optional)
    range_options: List[RangeOption] = field(default_factory=list)
    age_ranges: Optional[List[Tuple[float, float, Optional[float], Optional[float], bool, bool]]] = None
    interpretive_only: bool = False         # ranges were printed but only as category lists ("Low: <40 High: >=60")
    candidates: Optional[List[float]] = None  # several bare numbers before the unit and no way to tell which is the result
    ambiguity: Optional[str] = None
    section: str = ""                      # nearest preceding section heading ("URINE ROUTINE")
    result_range: Optional[Tuple[float, float]] = None  # microscopy result printed as a range ("2-3 /hpf")
    note: str = ""                         # how the reader chose the value (e.g. the "current" column)


@dataclass
class ScoredRow:
    """What the engine concluded about one row."""

    parsed: ParsedRow
    analyte_key: Optional[str]
    display_name: str                      # printed name (never replaced by the registry name)
    value: Optional[float]                 # in `unit` below
    unit: str
    note: str
    ref_low: Optional[float]
    ref_high: Optional[float]
    range_source: str                      # "report" | "registry" | "none"
    flag: str                              # HIGH LOW NORMAL CRITICAL_HIGH CRITICAL_LOW UNKNOWN POSITIVE NEGATIVE INFO
    value_type: str                        # numeric | qualified | qualitative | informational | unit_mismatch | ambiguous | invalid
    critical: Optional[Dict[str, Any]] = None
    discrepancy: Optional[str] = None
    severity: str = "NONE"                 # CRITICAL | HIGH | MODERATE | MILD | NONE
    pct_deviation: float = 0.0
    reference_text: str = "--"
    qualifier: Optional[str] = None
    text_value: Optional[str] = None

    @property
    def direction(self) -> str:
        if self.flag in ("HIGH", "CRITICAL_HIGH"):
            return "HIGH"
        if self.flag in ("LOW", "CRITICAL_LOW"):
            return "LOW"
        if self.flag == "POSITIVE":
            return "POSITIVE"
        return ""


NON_ANOMALY_FLAGS = ("NORMAL", "UNKNOWN", "NEGATIVE", "INFO")


@dataclass
class LabAnalysis:
    rows: List[ScoredRow] = field(default_factory=list)
    anomalies: List[ScoredRow] = field(default_factory=list)
    critical_alerts: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    unparsed_numeric_lines: int = 0
    sex_used: Optional[str] = None
    age_used: Optional[int] = None

    @property
    def not_assessed(self) -> List[str]:
        names = [r.display_name for r in self.rows if r.flag == "UNKNOWN"]
        if self.unparsed_numeric_lines:
            names.append(f"{self.unparsed_numeric_lines} unread numeric line(s)")
        return names
