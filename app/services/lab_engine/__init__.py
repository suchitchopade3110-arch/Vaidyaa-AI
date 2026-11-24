"""Deterministic lab engine: report text / rows -> scored rows, anomalies,
critical alerts and a risk floor. Pure Python, no I/O, no model calls."""
from .analytes import ANALYTE_TO_LEGACY_KEY, REGISTRY, Analyte, canon_tokens, get_analyte, match_analyte
from .evaluate import (
    analyze_lab_rows,
    apply_risk_floor,
    critical_summary,
    decorate_explanation,
    format_reference,
    normalize_age,
    normalize_sex,
    not_assessed_summary,
)
from .models import LabAnalysis, ParsedRow, ScoredRow
from .tokenizer import parse_line, parse_line_rows, parse_lab_text, parse_report_text, reassemble_cells
from .units import resolve_unit, to_canonical, unit_key, units_equivalent

__all__ = [
    "ANALYTE_TO_LEGACY_KEY", "REGISTRY", "Analyte", "LabAnalysis", "ParsedRow", "ScoredRow", "analyze_lab_rows",
    "apply_risk_floor", "canon_tokens", "critical_summary", "decorate_explanation", "format_reference",
    "get_analyte", "match_analyte", "normalize_age", "normalize_sex", "not_assessed_summary", "parse_lab_text",
    "parse_line", "parse_line_rows", "parse_report_text", "reassemble_cells", "resolve_unit", "to_canonical", "unit_key",
    "units_equivalent",
]
