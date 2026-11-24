"""Extraction-only report reader (plans/P2.md section 6.1).

``extract_report`` turns a report file into review rows: what was read, where on the page, how confidently, and
whether a clinician must look at it. It scores nothing for anyone: the engine pass it runs feeds only the review
triggers and the internal ``prelim_flag`` (S1). It never calls the models, RAG or the knowledge base (EX-08).

Pipeline and engine helpers are called through module attributes (``_pl.analyze_lab_rows`` ...) so the many
existing ``monkeypatch.setattr(pipeline, ...)`` tests and the eval's spies still intercept them.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from app.services.lab_engine.models import ParsedRow, ScoredRow
from app.services.lab_engine.tokenizer import TracedRow, UnreadLine, parse_report_text_traced
from app.services.review.layout import DocumentLayout, RowGeometry
from app.services.review.rows import RowData, serialize_parsed, value_raw_from_parsed
from app.services.review.rules import RowContext, compute_confidence, review_verdict, unread_verdict
from app.services.review.specimen import assign_specimens

logger = logging.getLogger(__name__)

EXTRACTOR_VERSION = "p2.1"
NO_ROWS_WARNING = "No results could be read automatically. Check the whole report against the page and add each result."


@dataclass
class ExtractionOutput:
    status: str = "ok"                        # ok | failed
    error_code: Optional[str] = None
    file_kind: str = "pdf"                    # pdf | image | csv
    pages: list[dict[str, Any]] = field(default_factory=list)
    rows: list[RowData] = field(default_factory=list)       # rows and unread lines, document order
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    raw_text: str = ""
    ocr_confidence: Optional[float] = None
    extractor_version: str = EXTRACTOR_VERSION
    review_all: bool = False
    unread_count: int = 0                     # result-like parts that could not be read (engine's count)


def file_kind_of(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".csv", ".tsv", ".txt"):
        return "csv"
    return "image"


def _csv_lines(file_path: str) -> list[str]:
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as fh:
        return fh.read().splitlines()


def _bbox_list(g: RowGeometry) -> Optional[list[float]]:
    return [round(float(v), 2) for v in g.bbox] if g.bbox else None


def _attributed(warnings: list[str], name: str) -> list[str]:
    prefix = f"{name}:"
    return [w for w in warnings if w.startswith(prefix)]


def _effective_range(p: ParsedRow, sex: Optional[str]) -> tuple[Optional[float], Optional[float]]:
    """The printed range that applies to this patient (never assumes a sex)."""
    if p.ref_low is not None or p.ref_high is not None:
        return p.ref_low, p.ref_high
    if p.sex_ranges and sex in p.sex_ranges:
        return p.sex_ranges[sex]
    return None, None


def _build_row(scored: ScoredRow, *, origin: str, line_ids: tuple[int, ...], geometry: RowGeometry,
               source_text: Optional[str], located: bool, sex: Optional[str], all_warnings: list[str],
               confidence_override: Optional[float] = None) -> RowData:
    p = scored.parsed
    lo, hi = _effective_range(p, sex)
    ocr_conf = geometry.confidence if origin in ("ocr", "legacy_urine") else None
    conf = (confidence_override if confidence_override is not None
            else compute_confidence(origin, line_count=max(1, len(line_ids)), min_token_conf=ocr_conf))
    ctx = RowContext(origin=origin, ocr_min_conf=ocr_conf, line_count=max(1, len(line_ids)), located=located,
                     warnings=tuple(_attributed(all_warnings, scored.display_name)))
    verdict = review_verdict(scored, ctx)
    return RowData(
        kind="row", name_raw=p.name, analyte_key=scored.analyte_key, value_raw=value_raw_from_parsed(p),
        value_num=p.value, qualifier=p.qualifier, text_value=p.text_value, unit_raw=p.unit, ref_low=lo, ref_high=hi,
        ref_text=p.ref_text, printed_flag=p.printed_flag, specimen=p.specimen or "unknown", confidence=conf,
        needs_review=verdict.needs_review, review_reason=verdict.reason, review_codes=list(verdict.codes),
        prelim_flag=scored.flag, source_page=geometry.page, source_bbox=_bbox_list(geometry),
        source_text=source_text, origin=origin, status="pending", parsed_payload=serialize_parsed(p))


def _unread_row(u: UnreadLine, geometry: RowGeometry, origin: str, source_text: str) -> RowData:
    v = unread_verdict()
    conf = geometry.confidence if geometry.confidence is not None else (1.0 if origin in ("text", "csv") else 0.5)
    return RowData(kind="unread_line", name_raw="", specimen="unknown", confidence=conf, needs_review=True,
                   review_reason=v.reason, review_codes=list(v.codes), source_page=geometry.page,
                   source_bbox=_bbox_list(geometry), source_text=source_text, origin=origin, status="pending")


def _row_origin(file_kind: str, layout: DocumentLayout, geometry: RowGeometry) -> str:
    if file_kind == "csv":
        return "csv"
    if geometry.page is not None:
        page = layout.page(geometry.page)
        return "ocr" if page is not None and page.method == "ocr" else "text"
    return "ocr" if file_kind == "image" else "text"


def _legacy_rows(raw_text: str, layout: DocumentLayout, file_kind: str, sex: Optional[str],
                 age: Optional[int], shown: list[ScoredRow], has_blood: bool, _pl: Any) -> list[RowData]:
    """Legacy urinalysis rows under today's branch rule (see module docstring of urine.py)."""
    from app.services.review import urine

    parsed = urine.legacy_rows(raw_text)
    if not parsed:
        return []
    seen = {_pl._lab_name_key(r.display_name) for r in shown} if has_blood else set()
    parsed = [p for p in parsed if _pl._lab_name_key(p.name) not in seen]
    if not parsed:
        return []
    analysis = _pl.analyze_lab_rows(parsed, sex, age)
    out: list[RowData] = []
    for scored in analysis.rows:
        p = scored.parsed
        needles = [p.name.split()[0]] if p.name.split() else []
        val = p.text_value or (f"{p.value:g}" if p.value is not None else "")
        phys = layout.find_line(*needles, val) if (needles and val and file_kind != "csv") else None
        geometry = (layout.locate([phys.id]) if phys is not None else RowGeometry(None, None, "", None))
        located = geometry.bbox is not None or file_kind == "csv"
        row = _build_row(scored, origin="legacy_urine", line_ids=(), geometry=geometry,
                         source_text=geometry.source_text or None, located=located, sex=sex,
                         all_warnings=analysis.warnings)
        out.append(row)
    return out


def extract_report(file_path: str, report_type: str = "lab", sex: Optional[str] = None,
                   age: Optional[int] = None) -> ExtractionOutput:
    """Read a report file into review rows. Never raises: a failure is ``status="failed"`` with a generic code."""
    file_kind = file_kind_of(file_path)
    try:
        return _extract(file_path, report_type, sex, age, file_kind)
    except Exception as exc:
        # generic on purpose: no exception text, path or value reaches a response or a log line (S10)
        logger.error("Extraction failed (%s)", type(exc).__name__)
        return ExtractionOutput(status="failed", error_code="EXTRACTION_ERROR", file_kind=file_kind,
                                warnings=["The report could not be read."])


def _extract(file_path: str, report_type: str, sex_in: Any, age_in: Any, file_kind: str) -> ExtractionOutput:
    from app import pipeline as _pl
    from app.services.lab_engine import normalize_age, normalize_sex

    sex = normalize_sex(sex_in)
    age = normalize_age(age_in)

    validation = _pl._validate_file_safe(file_path)
    if not validation.valid:
        return ExtractionOutput(status="failed", error_code="FILE_INVALID", file_kind=file_kind,
                                warnings=["The file is not a valid report."])

    ocr = _pl._run_ocr_safe(file_path)
    raw_text = getattr(ocr, "raw_text", "") or ""
    layout = getattr(ocr, "layout", None)
    if not isinstance(layout, DocumentLayout):
        layout = DocumentLayout.build(file_kind, raw_text, [], [])
    out = ExtractionOutput(file_kind=file_kind, pages=layout.page_meta(), raw_text=raw_text,
                           ocr_confidence=getattr(ocr, "confidence", None))

    # --- parse: the column parser's table when it is usable, else the traced text reader -------------------
    column = _pl._parse_column_lab_report_safe(file_path, report_type)
    traced_rows: list[TracedRow] = []
    column_geo: dict[int, tuple[Optional[int], Any]] = {}
    unread: list[UnreadLine] = []
    unread_count = 0
    parse_warnings: list[str] = []
    if column.get("lab_values"):
        from app.services.lab_report_column_parser import column_rows_to_parsed_located

        parsed_rows = []
        for p, page, bbox in column_rows_to_parsed_located(column["lab_values"]):
            parsed_rows.append(p)
            column_geo[id(p)] = (page, bbox)
    else:
        traced = parse_report_text_traced(raw_text)
        traced_rows = traced.rows
        parsed_rows = [t.row for t in traced_rows]
        unread, unread_count, parse_warnings = traced.unread, traced.unread_count, list(traced.warnings)
    ids_by_row = {id(t.row): t.line_ids for t in traced_rows}
    csv_lines = _csv_lines(file_path) if file_kind == "csv" else []

    assign_specimens(parsed_rows)
    analysis = _pl.analyze_lab_rows(parsed_rows, sex, age, unparsed_numeric_lines=unread_count,
                                    extra_warnings=tuple(parse_warnings))
    out.unread_count = unread_count

    # --- shown rows with provenance -------------------------------------------------------------------------
    items: list[tuple[float, RowData]] = []
    for scored in analysis.rows:
        p = scored.parsed
        if id(p) in column_geo:
            page, bbox = column_geo[id(p)]
            geometry = RowGeometry(page, tuple(bbox) if bbox else None, p.line, None)
            row = _build_row(scored, origin="column", line_ids=(), geometry=geometry, source_text=p.line,
                             located=bbox is not None, sex=analysis.sex_used, all_warnings=analysis.warnings)
            key = float(page or 0) * 1e6 + (bbox[1] if bbox else 0.0)
        else:
            ids = ids_by_row.get(id(p), ())
            if file_kind == "csv":
                geometry = RowGeometry(None, None, "", None)
                src = " | ".join(csv_lines[i - 1] for i in ids if 0 < i <= len(csv_lines)) or None
                origin, located = "csv", True
            else:
                geometry = layout.locate(ids)
                src = geometry.source_text or p.line or None
                origin, located = _row_origin(file_kind, layout, geometry), geometry.bbox is not None
            row = _build_row(scored, origin=origin, line_ids=ids if file_kind != "csv" else (), geometry=geometry,
                             source_text=src, located=located, sex=analysis.sex_used, all_warnings=analysis.warnings)
            key = float(min(ids)) if ids else 1e9
        items.append((key, row))

    for u in unread:
        if file_kind == "csv":
            geometry = RowGeometry(None, None, "", None)
            src = " | ".join(csv_lines[i - 1] for i in u.line_ids if 0 < i <= len(csv_lines)) or u.text
            origin = "csv"
        else:
            geometry = layout.locate(u.line_ids)
            src = geometry.source_text or u.text
            origin = _row_origin(file_kind, layout, geometry)
        items.append((float(min(u.line_ids)) if u.line_ids else 1e9, _unread_row(u, geometry, origin, src)))

    items.sort(key=lambda kv: kv[0])
    rows = [r for _, r in items]

    # --- legacy urinalysis rows under today's branch rule -----------------------------------------------------
    has_blood = any(_pl._is_blood_row(r) for r in analysis.rows)
    legacy = _legacy_rows(raw_text, layout, file_kind, analysis.sex_used, analysis.age_used, analysis.rows,
                          has_blood, _pl)
    if legacy and not has_blood:
        rows = [r for r in rows if r.kind == "unread_line"]       # legacy rows replace the engine's rows
    rows = rows + legacy
    for i, r in enumerate(rows):
        r.row_index = i
    out.rows = rows

    # --- warnings and notes --------------------------------------------------------------------------------------
    shown = [r.display_name for r in analysis.rows]
    unread_msg = f"{unread_count} numeric line(s) could not be read"
    out.warnings = [w for w in analysis.warnings
                    if w != unread_msg and not any(w.startswith(f"{n}:") for n in shown)]
    missing = " and ".join(n for n, v in (("sex", analysis.sex_used), ("age", analysis.age_used)) if v is None)
    if missing:
        out.notes.append(f"Patient {missing} not provided: reference ranges that depend on it were not assumed.")
    if not any(r.kind == "row" for r in rows):
        out.warnings.append(NO_ROWS_WARNING)
        out.review_all = True
    return out
