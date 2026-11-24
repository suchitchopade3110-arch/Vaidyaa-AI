"""Column-aware lab PDF parser for multi-panel reports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class LabValue:
    test: str
    result: Optional[str] = None
    unit: Optional[str] = None
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    ref_raw: Optional[str] = None
    flag: str = "NORMAL"
    section: str = "GENERAL"
    page: Optional[int] = None                       # 1-based source page (provenance, E7)
    bbox: Optional[tuple[float, float, float, float]] = None  # union of the words used, displayed-page points


HEADER_RE = re.compile(
    "|".join(
        [
            r"SID\s*No\.?\s*:.*",
            r"Ph\s*:\s*\d{10}.*",
            r"Patient\s*ID\s*:.*",
            r"Name\s*:.*",
            r"Age\s*/\s*Sex\s*:.*",
            r"Ref\.\s*By\s*:.*",
            r"Registered\s*Date\s*:.*",
            r"Report\s*Date\s*:.*",
            r"^\s*\d+\s*/\s*\d+\s*$",
        ]
    ),
    re.IGNORECASE,
)

SECTION_RE = re.compile(
    r"^\s*(HAEMATOLOGY|DIFFERENTIAL\s*COUNT|SEROLOGY|BIOCHEMISTRY|"
    r"ELECTROLYTES|URINE\s*ANALYSIS|MACROSCOPIC|MICROSCOPIC|"
    r"BLOOD\s*GROUPING|DENGUE|ESR)\b",
    re.IGNORECASE,
)

REF_RANGE_RE = re.compile(
    r"(?P<lt>[<>])\s*(?P<single>\d+\.?\d*)"
    r"|(?P<low>\d+\.?\d*)\s*(?:-|–|to)\s*(?P<high>\d+\.?\d*)",
    re.IGNORECASE,
)
NUMERIC_RE = re.compile(r"^[:\s]*(?P<val>\d+(?:\.\d+)?)")
UNIT_RE = re.compile(
    r"\b(cells/cumm|cells/mm3|mg/dl|g/dl|mEq/L|mmol/L|ng/mL|%|mm|IU/L|grams?)\b",
    re.IGNORECASE,
)

COL_BOUNDS = {
    "test": (0, 300),
    "result": (300, 480),
    "unit": (480, 580),
    "ref": (580, 1000),
}

PANEL_KEYWORDS = {
    "urinalysis": ["urine", "pus cells", "epithelial", "specific gravity"],
    "haematology": ["haemoglobin", "wbc", "rbc", "platelet", "esr", "differential"],
    "biochemistry": ["sodium", "potassium", "creatinine", "bilirubin", "glucose", "bicarbonate"],
    "serology": ["crp", "dengue", "hiv", "hbsag", "vdrl", "widal"],
    "microbiology": ["culture", "sensitivity", "organism"],
}


def _in_col(x0: float, col: str) -> bool:
    lo, hi = COL_BOUNDS[col]
    return lo <= x0 < hi


def _clean_result(raw: str) -> str:
    return re.sub(r"^[:\s]+", "", raw or "").strip()


def _parse_ref_range(ref_raw: str) -> tuple[Optional[float], Optional[float]]:
    match = REF_RANGE_RE.search(ref_raw or "")
    if not match:
        return None, None
    if match.group("lt"):
        value = float(match.group("single"))
        return (None, value) if match.group("lt") == "<" else (value, None)
    return float(match.group("low")), float(match.group("high"))


def _determine_flag(result_str: str, ref_low: Optional[float], ref_high: Optional[float]) -> str:
    match = NUMERIC_RE.match(result_str or "")
    if not match:
        return "NORMAL"
    value = float(match.group("val"))
    if ref_low is not None and value < ref_low:
        return "ABNORMAL_LOW"
    if ref_high is not None and value > ref_high:
        return "ABNORMAL_HIGH"
    return "NORMAL"


def _extract_words_by_column(page) -> dict[int, dict[str, list[str]]]:
    """Words per y bucket and column. The bucket's ``"_boxes"`` entry holds the displayed-page rectangles of the
    words that landed in a column (rotation applied), for provenance."""
    import fitz

    matrix = page.rotation_matrix if getattr(page, "rotation", 0) else None
    lines: dict[int, dict[str, list]] = {}
    for x0, y0, x1, y1, word, *_rest in page.get_text("words"):
        y_bucket = round(y0 / 8) * 8
        lines.setdefault(y_bucket, {"test": [], "result": [], "unit": [], "ref": [], "_boxes": []})
        for col in COL_BOUNDS:
            if _in_col(float(x0), col):
                lines[y_bucket][col].append(str(word))
                rect = fitz.Rect(x0, y0, x1, y1)
                if matrix is not None:
                    rect = rect * matrix
                    rect.normalize()
                lines[y_bucket]["_boxes"].append((rect.x0, rect.y0, rect.x1, rect.y1))
                break
    return lines


def _union(boxes):
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))


def parse_lab_pdf(pdf_path: str) -> list[LabValue]:
    import fitz

    results: list[LabValue] = []
    current_section = "GENERAL"
    doc = fitz.open(pdf_path)
    try:
        for page_no, page in enumerate(doc, start=1):
            col_lines = _extract_words_by_column(page)
            for y_bucket in sorted(col_lines):
                row = col_lines[y_bucket]
                test_str = " ".join(row["test"]).strip()
                res_str = _clean_result(" ".join(row["result"]).strip())
                unit_str = " ".join(row["unit"]).strip()
                ref_str = " ".join(row["ref"]).strip()

                if not test_str:
                    continue
                if HEADER_RE.search(test_str) or HEADER_RE.search(res_str):
                    continue
                if re.match(
                    r"^\s*(Method|Technical\s*Note|Test|Result|Reference|Units|Verified|End\s*Of\s*Report|Done\s*by)",
                    test_str,
                    re.IGNORECASE,
                ):
                    continue

                section_match = SECTION_RE.match(test_str)
                if section_match:
                    current_section = section_match.group(1).upper().replace(" ", "_")
                    continue
                if not res_str or res_str in {"-", "--", "—"}:
                    continue

                ref_low, ref_high = _parse_ref_range(ref_str)
                unit = unit_str
                if not unit:
                    unit_match = UNIT_RE.search(ref_str)
                    if unit_match:
                        unit = unit_match.group(0)

                results.append(
                    LabValue(
                        test=test_str,
                        result=res_str,
                        unit=unit,
                        ref_low=ref_low,
                        ref_high=ref_high,
                        ref_raw=ref_str[:120] if ref_str else None,
                        flag=_determine_flag(res_str, ref_low, ref_high),
                        section=current_section,
                        page=page_no,
                        bbox=_union(row["_boxes"]),
                    )
                )
    finally:
        doc.close()
    return results


def detect_panel_type(lab_values: list[LabValue | dict]) -> str:
    section_to_panel = {
        "haematology": "haematology",
        "differential_count": "haematology",
        "serology": "serology",
        "biochemistry": "biochemistry",
        "electrolytes": "biochemistry",
        "urine_analysis": "urinalysis",
        "macroscopic": "urinalysis",
        "microscopic": "urinalysis",
        "dengue": "serology",
        "esr": "haematology",
    }
    counts: dict[str, int] = {}
    for item in lab_values:
        if isinstance(item, dict):
            section = str(item.get("section") or "").lower()
            name = str(item.get("name") or item.get("test") or "").lower()
        else:
            section = str(item.section or "").lower()
            name = str(item.test or "").lower()
        panel = section_to_panel.get(section)
        if panel is None:
            panel = next((key for key, words in PANEL_KEYWORDS.items() if any(word in name for word in words)), "general")
        counts[panel] = counts.get(panel, 0) + 1
    non_general = {key: value for key, value in counts.items() if key != "general" and value > 0}
    if len(non_general) > 2:
        return "comprehensive"
    if non_general:
        return max(non_general, key=non_general.get)
    return "general"


def parse_lab_report_for_pipeline(pdf_path: str) -> dict:
    parsed = parse_lab_pdf(pdf_path)
    panel_type = detect_panel_type(parsed)
    lab_values = []
    for item in parsed:
        if item.ref_low is not None and item.ref_high is not None:
            reference = f"{item.ref_low} - {item.ref_high}"
        elif item.ref_high is not None:
            reference = f"< {item.ref_high}"
        elif item.ref_low is not None:
            reference = f"> {item.ref_low}"
        else:
            reference = item.ref_raw or "--"
        lab_values.append(
            {
                "name": item.test,
                "test": item.test,
                "value": item.result,
                "result": item.result,
                "unit": item.unit or "",
                "reference": reference,
                "reference_range": reference,
                "ref_low": item.ref_low,
                "ref_high": item.ref_high,
                "ref_raw": item.ref_raw,
                "flag": item.flag,
                "section": item.section,
                "page": item.page,
                "bbox": item.bbox,
            }
        )

    abnormal_items = [
        {"name": item.test, "value": item.result, "flag": item.flag, "section": item.section}
        for item in parsed
        if item.flag != "NORMAL"
    ]
    return {
        "lab_values": lab_values,
        "panel_type": panel_type,
        "abnormal_count": len(abnormal_items),
        "normal_count": len(parsed) - len(abnormal_items),
        "abnormal_items": abnormal_items,
    }


_RESULT_CELL = re.compile(
    r"^\s*(?P<q><=|>=|[<>\u2264\u2265])?\s*(?P<n>[+-]?(?:\d[\d,]*\.?\d*|\.\d+))\s*(?P<rest>.*?)\s*$"
)
_QUALIFIERS = {"<": "<", ">": ">", "<=": "<=", ">=": ">=", "\u2264": "<=", "\u2265": ">="}


def column_rows_to_parsed(lab_values: list) -> list:
    """Column-parser rows (see ``column_rows_to_parsed_located``) as ParsedRows only."""
    return [parsed for parsed, _page, _bbox in column_rows_to_parsed_located(lab_values)]


def column_rows_to_parsed_located(lab_values: list) -> list:
    """Column-parser rows (the dicts from parse_lab_report_for_pipeline) ->
    lab-engine ParsedRow list, so they are scored by exactly the same code as
    rows read from text or CSV.

    The printed reference text (`ref_raw`) is re-read with the engine's range
    parser, which understands one-sided, strict and sex-labelled ranges; the
    parser's own ref_low/ref_high (first number pair found) is only a fallback.

    Returns ``(ParsedRow, page, bbox)`` triples; page and bbox are None when the dict carries none."""
    from app.services.lab_engine.models import ParsedRow
    from app.services.lab_engine.tokenizer import normalize_flag, parse_range_text, qualitative_class
    from app.services.lab_engine.units import is_unit_token, parse_number

    out = []
    for item in lab_values or []:
        row = item if isinstance(item, dict) else dict(getattr(item, "__dict__", {}))
        name = str(row.get("test") or row.get("name") or "").strip()
        result = str(row.get("result", row.get("value", "")) if row.get("result", row.get("value")) is not None else "").strip()
        unit = (row.get("unit") or "").strip() or None
        ref_raw = row.get("ref_raw") or None
        if not name or not result:
            continue
        parsed = ParsedRow(name=name, unit=unit, source="column_parser",
                           line=f"{name} | {result} | {unit or ''} | {ref_raw or row.get('reference') or ''}")
        m = _RESULT_CELL.match(result)
        value = parse_number(m.group("n").lstrip("+"), False) if m else None
        if m and value is not None:
            parsed.value = value
            parsed.qualifier = _QUALIFIERS.get(m.group("q") or "")
            flags, leftovers = [], []
            for tok in re.sub(r"[()\[\]]", " ", m.group("rest")).split():
                if normalize_flag([tok]):
                    flags.append(tok)
                elif is_unit_token(tok) and not parsed.unit:
                    parsed.unit = tok
                else:
                    leftovers.append(tok)
            parsed.printed_flag = normalize_flag(flags) if flags else None
            if leftovers:
                parsed.warnings.append(f"{name}: unread text after the result ({' '.join(leftovers)})")
        else:
            parsed.text_value = result
            if qualitative_class(result) is None:
                parsed.warnings.append(f"{name}: result '{result}' is not a number the parser could read")
        rng = parse_range_text(ref_raw) if ref_raw else None
        if rng is not None:
            parsed.ref_low, parsed.ref_high = rng["lo"], rng["hi"]
            parsed.ref_strict_low, parsed.ref_strict_high = rng["strict_lo"], rng["strict_hi"]
            parsed.ref_unit, parsed.sex_ranges, parsed.ref_text = rng["unit"], rng["sex_ranges"], ref_raw
        elif row.get("ref_low") is not None or row.get("ref_high") is not None:
            parsed.ref_low, parsed.ref_high = row.get("ref_low"), row.get("ref_high")
            parsed.ref_text = ref_raw or row.get("reference")
        out.append((parsed, row.get("page"), row.get("bbox")))
    return out
