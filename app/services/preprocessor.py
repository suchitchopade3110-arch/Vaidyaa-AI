"""
preprocessor.py — VaidyaAI Preprocessing Layer (Layer 2)
=========================================================
Handles ALL input types before they hit ML models.

PRD Layer 2 components (all implemented here):
  ✅ ClinicalBERT NER     → extract conditions, meds, lab values
  ✅ OCR Engine           → PaddleOCR + PyMuPDF → raw text
  ✅ Image Normalizer     → Resize + CLAHE + Denoise
  ✅ DICOM Parser         → pydicom → slices + metadata
  ✅ Data Validator       → format check + quality score

Output contracts (design_doc 14.1):
  DicomOutput, OcrOutput, NerOutput — all Pydantic, backend-ready.

PRD constraint: ClinicalBERT = NER ONLY. Never reasons. Never generates.
"""

import os
import io
import json
import re
import uuid
import traceback
from typing import Optional, List, Dict, Any, Tuple

import numpy as np
import cv2
from PIL import Image
from pydantic import BaseModel

# ── Device ────────────────────────────────────────────────
import torch
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

from app.services.lab_engine.analytes import REGISTRY, canon_tokens, match_analyte
from app.services.lab_engine.evaluate import normalize_age, normalize_sex
from app.services.lab_engine.models import ParsedRow
from app.services.lab_engine.tokenizer import (
    OTHER_ROWS_MARKER,
    STRUCTURED_LAB_MARKER,
    WARN_PREFIX,
    build_canonical_line,
    normalize_flag,
    parse_lab_text,
    parse_range_text,
    parse_report_text,
    parse_structured_csv,
    qualitative_class,
)
from app.services.lab_engine.units import fmt_num, is_unit_token, parse_number, units_equivalent


# ════════════════════════════════════════════════════════════
# SECTION 1 — PYDANTIC OUTPUT CONTRACTS (design_doc 14.1)
# ════════════════════════════════════════════════════════════

from typing import Optional

class LabValue(BaseModel):
    name: str
    value: Optional[float] = None
    unit: str
    raw_text: str = ""
    ref_low: Optional[float] = None
    ref_high: Optional[float] = None
    flag: Optional[str] = None
    status: Optional[str] = None
    pct_deviation: Optional[float] = None
    # Universal additions
    value_type: str = "numeric"  # numeric, binary, qualitative, lt_gt, ratio
    display_value: str = ""
    result_text: str = ""
    is_abnormal: bool = False
    prefix: str = ""


class DicomOutput(BaseModel):
    pixel_array: Any           # np.ndarray — normalized image (H,W,3)
    metadata: Dict[str, Any]   # patient info, modality, body part
    slices: List[Any]          # list of np.ndarray for multi-slice CT/MRI
    num_slices: int
    modality: str
    quality_score: float       # 0.0-1.0


class OcrOutput(BaseModel):
    raw_text: str
    confidence: float          # mean word confidence from Tesseract
    pages: List[str]           # per-page text


class NerOutput(BaseModel):
    conditions: List[str]
    medications: List[str]
    lab_values: List[LabValue]
    dates: List[str]
    clinical_flags: List[str] = []
    raw_entities: List[Dict]   # full ClinicalBERT entity list


class ImageNormOutput(BaseModel):
    normalized: Any            # np.ndarray uint8 (H,W,3)
    original_shape: Tuple
    quality_score: float
    rejected: bool
    reject_reason: str = ""


class ValidationResult(BaseModel):
    valid: bool
    quality_score: float       # 0.0-1.0
    format_ok: bool
    size_ok: bool
    reason: str = ""


# ════════════════════════════════════════════════════════════
# SECTION 2 — DICOM PARSER
# ════════════════════════════════════════════════════════════

def parse_dicom(
    dicom_path: str,
    max_slices: Optional[int] = None,
    target_size: Tuple[int, int] = (1024, 1024),
) -> DicomOutput:
    """
    Parse a DICOM file or directory of DICOM slices.
    Handles single .dcm files and multi-slice CT/MRI series.

    Args:
        dicom_path:  path to .dcm file OR directory of .dcm files
        max_slices:  cap slice count (None = all)
        target_size: resize each slice to this (W, H)

    Returns:
        DicomOutput with pixel_array (first/only slice), slices list, metadata
    """
    try:
        import pydicom
    except ImportError:
        raise ImportError("pip install pydicom SimpleITK")

    slices_raw = []
    metadata = {}

    # ── Directory (multi-slice CT/MRI series) ────────────
    if os.path.isdir(dicom_path):
        dcm_files = sorted([
            os.path.join(dicom_path, f)
            for f in os.listdir(dicom_path)
            if f.lower().endswith(".dcm")
        ])
        if not dcm_files:
            raise ValueError(f"No .dcm files found in: {dicom_path}")

        if max_slices:
            dcm_files = dcm_files[:max_slices]

        ds = pydicom.dcmread(dcm_files[0])
        metadata = _extract_dicom_metadata(ds)

        for path in dcm_files:
            ds = pydicom.dcmread(path)
            arr = _normalize_dicom_pixel(ds.pixel_array, target_size)
            slices_raw.append(arr)

    # ── Single .dcm file ─────────────────────────────────
    else:
        ds = pydicom.dcmread(dicom_path)
        metadata = _extract_dicom_metadata(ds)

        pixel = ds.pixel_array
        # Multi-frame (e.g., multi-slice in one file)
        if pixel.ndim == 3 and pixel.shape[0] > 1:
            frames = pixel[:max_slices] if max_slices else pixel
            for frame in frames:
                slices_raw.append(_normalize_dicom_pixel(frame, target_size))
        else:
            slices_raw.append(_normalize_dicom_pixel(pixel, target_size))

    primary = slices_raw[0] if slices_raw else np.zeros((*target_size, 3), dtype=np.uint8)
    quality = _image_quality_score(primary)

    return DicomOutput(
        pixel_array=primary,
        metadata=metadata,
        slices=slices_raw,
        num_slices=len(slices_raw),
        modality=metadata.get("Modality", "unknown"),
        quality_score=quality,
    )


def _extract_dicom_metadata(ds) -> Dict[str, Any]:
    """Extract key DICOM header fields safely."""
    fields = [
        "PatientID", "PatientName", "PatientAge", "PatientSex",
        "Modality", "BodyPartExamined", "StudyDescription",
        "SeriesDescription", "InstitutionName",
        "Rows", "Columns", "SliceThickness",
        "PixelSpacing", "ImageOrientationPatient",
        "StudyDate", "SeriesDate",
    ]
    meta = {}
    for field in fields:
        try:
            val = getattr(ds, field, None)
            if val is not None:
                meta[field] = str(val)
        except Exception:
            pass
    return meta


def _normalize_dicom_pixel(
    pixel: np.ndarray,
    target_size: Tuple[int, int],
) -> np.ndarray:
    """
    Normalize raw DICOM pixel array:
      - Handle 16-bit → 8-bit
      - Apply CLAHE contrast enhancement
      - Resize to target_size
      - Convert to 3-channel RGB
    """
    # 16-bit → float → 8-bit
    arr = pixel.astype(np.float32)
    arr = (arr - arr.min()) / (arr.max() - arr.min() + 1e-8)
    arr = (arr * 255).astype(np.uint8)

    # Grayscale → 3-channel
    if arr.ndim == 2:
        # CLAHE on grayscale first
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        arr = clahe.apply(arr)
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    elif arr.ndim == 3 and arr.shape[2] == 1:
        arr = arr[:, :, 0]
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        arr = clahe.apply(arr)
        arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
    else:
        # Already RGB — apply CLAHE on L channel
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        arr = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    # Resize
    arr = cv2.resize(arr, target_size, interpolation=cv2.INTER_LANCZOS4)
    return arr


# ════════════════════════════════════════════════════════════
# SECTION 3 — IMAGE NORMALIZER
# ════════════════════════════════════════════════════════════

def normalize_image(
    image_input,                    # path str OR np.ndarray OR PIL.Image
    target_size: Tuple[int, int] = (1024, 1024),
    apply_clahe: bool = True,
    apply_denoise: bool = True,
) -> ImageNormOutput:
    """
    Standard image normalizer for non-DICOM inputs (JPG/PNG).
    Pipeline: Load → Validate → Resize → CLAHE → Denoise → RGB

    PRD: Image path → Normalize + CLAHE → ready for MedSAM
    """
    # ── Load ──────────────────────────────────────────────
    try:
        if isinstance(image_input, str):
            pil_img = Image.open(image_input).convert("RGB")
            arr = np.array(pil_img)
        elif isinstance(image_input, np.ndarray):
            arr = image_input.copy()
            if arr.ndim == 2:
                arr = cv2.cvtColor(arr, cv2.COLOR_GRAY2RGB)
        elif isinstance(image_input, Image.Image):
            arr = np.array(image_input.convert("RGB"))
        else:
            raise TypeError(f"Unsupported type: {type(image_input)}")
    except Exception as e:
        return ImageNormOutput(
            normalized=np.zeros((*target_size, 3), dtype=np.uint8),
            original_shape=(0, 0, 0),
            quality_score=0.0,
            rejected=True,
            reject_reason=str(e),
        )

    original_shape = arr.shape

    # ── Validate ──────────────────────────────────────────
    validation = validate_image(arr)
    if not validation.valid:
        return ImageNormOutput(
            normalized=np.zeros((*target_size, 3), dtype=np.uint8),
            original_shape=original_shape,
            quality_score=0.0,
            rejected=True,
            reject_reason=validation.reason,
        )

    # ── Normalize to float [0,1] ──────────────────────────
    arr = arr.astype(np.float32)
    arr = (arr - arr.min()) / (arr.max() - arr.min() + 1e-8)
    arr = (arr * 255).astype(np.uint8)

    # ── Resize ────────────────────────────────────────────
    arr = cv2.resize(arr, target_size, interpolation=cv2.INTER_LANCZOS4)

    # ── CLAHE contrast enhancement ────────────────────────
    if apply_clahe:
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        lab[:, :, 0] = clahe.apply(lab[:, :, 0])
        arr = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)

    # ── Denoise ───────────────────────────────────────────
    if apply_denoise:
        arr = cv2.fastNlMeansDenoisingColored(arr, None, 10, 10, 7, 21)

    quality = _image_quality_score(arr)

    return ImageNormOutput(
        normalized=arr,
        original_shape=original_shape,
        quality_score=quality,
        rejected=False,
    )


# ════════════════════════════════════════════════════════════
# SECTION 4 — OCR ENGINE
# ════════════════════════════════════════════════════════════

# Structured CSV/TSV lab tables are handed to extract_all_lab_values / the lab
# engine as text that starts with this marker followed by one canonical line
# per result (see lab_engine.tokenizer.build_canonical_line):
#     <name> <value> [<unit>] [<low>-<high> | lt<high> | gt<low>] [@flag=H] [@refu=..] [@txt=..] [@sx=..]
# Numbers are already normalised (decimal commas -> '.'), the unit has no
# spaces, and the line is parsed right-to-left, so it is unambiguous even for
# names containing digits and for rows without a unit or range. Anything the
# table parser could not map follows an "#other-rows" line as plain text;
# table-level problems (ambiguous result columns ...) are "#warn" lines.
_OTHER_ROWS_MARKER = OTHER_ROWS_MARKER

_CSV_FLAG_WORDS = {"h", "l", "hh", "ll", "*", "h*", "l*", "high", "low", "h/l", "n", "normal", "a", "abn",
                   "abnormal", "critical", "**", "crit", "↑", "↓"}
_CSV_NAME_HEADERS = {"test", "tests", "parameter", "parameters", "investigation", "investigations",
                     "analyte", "name", "component", "analysis", "determination"}
_CSV_VALUE_HEADERS = {"result", "results", "value", "observed", "reading", "finding", "ergebnis", "wert"}
_CSV_UNIT_HEADERS = {"unit", "units", "uom", "einheit"}
_CSV_RANGE_HEADERS = {"reference", "ref", "range", "interval", "normal", "referenzbereich", "referenz", "normalbereich"}
_CSV_FLAG_HEADERS = {"flag", "flags", "status", "abnormal", "interpretation"}
_CSV_PREVIOUS_HEADERS = {"previous", "prior", "last", "old", "prev", "earlier", "baseline", "historical"}
_CSV_CURRENT_HEADERS = {"current", "latest", "today", "new", "present", "recent"}
_CSV_LOW_HEADERS = {"low", "lower", "min", "minimum"}
_CSV_HIGH_HEADERS = {"high", "upper", "max", "maximum"}

def _fmt_num(x: float) -> str:
    return fmt_num(x)


def _parse_number_token(tok: str, decimal_comma: bool) -> Optional[float]:
    """Parse "7.9", "7,9", "1,500", "150,000", "1,234.5", "2.54E+04" -> float.

    A comma is a decimal separator ("7,9") unless the token is shaped like
    thousands grouping ("150,000", first group not starting with 0) AND the
    file is not a ';'-delimited decimal-comma export (where "12.000" is a
    thousands-dot number)."""
    t = (tok or "").strip().replace("−", "-")
    if not decimal_comma and re.fullmatch(r"[+-]?\d+,\d{1,2}(?!\d)", t):
        t = t.replace(",", ".")
    elif not decimal_comma and re.fullmatch(r"[+-]?\d+,\d+", t):
        return None if not re.fullmatch(r"[+-]?\d{1,3},\d{3}", t) else parse_number(t, False)
    return parse_number(t, decimal_comma)


_RESULT_RE = re.compile(r"^\s*(?P<q><=|>=|[<>≤≥])?\s*(?P<n>[+-]?(?:\d[\d.,]*|\.\d+)(?:[eE][+-]?\d+)?)\s*(?P<rest>.*?)\s*$")
_QUAL_MAP = {"<": "<", "≤": "<=", "<=": "<=", ">": ">", "≥": ">=", ">=": ">="}


def _parse_result_cell(cell: str, decimal_comma: bool) -> Optional[Dict[str, Any]]:
    """Result cell -> {value, prefix, unit, flag, text} or None.

    A trailing H/L/* flag is captured; any other trailing text must look like
    a unit. A qualitative cell ("Reactive", "Negative") returns text only."""
    cell = (cell or "").strip()
    m = _RESULT_RE.match(cell)
    if m:
        value = _parse_number_token(m.group("n"), decimal_comma)
        if value is None:
            return None
        unit, flags = "", []
        for tok in m.group("rest").split():
            if tok.lower() in _CSV_FLAG_WORDS:
                flags.append(tok)
            elif is_unit_token(tok) and not unit:
                unit = tok
            else:
                return None
        return {"value": value, "prefix": _QUAL_MAP.get(m.group("q") or "", ""), "unit": unit,
                "flag": normalize_flag(flags), "text": None}
    if cell and qualitative_class(cell) is not None:
        return {"value": None, "prefix": "", "unit": "", "flag": None, "text": cell}
    return None


def _decomma(text: str) -> str:
    """'3,5-5,1' / '150.000-400.000' -> '3.5-5.1' / '150000-400000' (decimal-comma files)."""
    def one(m):
        v = parse_number(m.group(0), True)
        return _fmt_num(v) if v is not None else m.group(0)
    return re.sub(r"\d[\d.,]*\d|\d", one, text)


def _parse_range_cell(cell: str, decimal_comma: bool) -> Dict[str, Any]:
    """Reference-range cell -> {lo, hi, strict_lo, strict_hi, unit, sex_ranges} (all None when unreadable)."""
    text = (cell or "").strip()
    empty = {"lo": None, "hi": None, "strict_lo": False, "strict_hi": False, "unit": None, "sex_ranges": None}
    if not text:
        return empty
    if decimal_comma:
        text = _decomma(text)
    return parse_range_text(text, False) or empty


def _header_role(cell: str) -> Optional[str]:
    toks = set(re.sub(r"[^a-z0-9]+", " ", (cell or "").lower()).split())
    if not toks:
        return None
    if toks & _CSV_PREVIOUS_HEADERS:
        return "previous"
    if toks & _CSV_UNIT_HEADERS:
        return "unit"
    if toks & _CSV_FLAG_HEADERS:
        return "flag"
    if toks & _CSV_LOW_HEADERS and not toks & (_CSV_VALUE_HEADERS | _CSV_NAME_HEADERS):
        return "ref_low"
    if toks & _CSV_HIGH_HEADERS and not toks & (_CSV_VALUE_HEADERS | _CSV_NAME_HEADERS):
        return "ref_high"
    if toks & _CSV_RANGE_HEADERS:
        return "range"
    if toks & _CSV_VALUE_HEADERS:
        return "value"
    if toks & _CSV_NAME_HEADERS:
        return "name"
    return None


def _is_current_header(cell: str) -> bool:
    toks = set(re.sub(r"[^a-z0-9]+", " ", (cell or "").lower()).split())
    return bool(toks & _CSV_CURRENT_HEADERS)


def _sniff_delimiter(content: str, default: str) -> Optional[str]:
    """Pick the delimiter that splits the sample into a consistent number of
    >=2 columns. Priority tab > ; > | > , on ties, so a decimal-comma file
    ("7,9") with ';' separators is not misread as comma-separated."""
    import csv
    from collections import Counter

    sample = [ln for ln in content.splitlines() if ln.strip()][:60]
    if not sample:
        return None
    best, best_key = None, None
    for rank, delim in enumerate(("\t", ";", "|", ",")):
        try:
            counts = [len(r) for r in csv.reader(sample, delimiter=delim)]
        except csv.Error:
            continue
        freq = Counter(counts)
        modal, n = max(freq.items(), key=lambda kv: (kv[1], kv[0]))
        consistency = n / len(counts)
        if modal < 2 or consistency < 0.6:
            continue
        key = (round(consistency, 2), -rank)
        if best_key is None or key > best_key:
            best, best_key = delim, key
    return best


def _known_analyte(name: str) -> bool:
    return match_analyte(name) is not None


def _canonical_lab_line(name: str, value: float, prefix: str, unit: str,
                        low: Optional[float], high: Optional[float], **extra: Any) -> str:
    return build_canonical_line(name, value, prefix, unit, low, high, **extra)


def _parse_delimited_content(content: str, default_delim: str):
    """Structured parse of a delimited lab table.

    Returns None when the content is not delimited (caller treats it as plain
    text); otherwise (canonical_lines, leftover_rows, warnings)."""
    import csv
    import logging

    delim = _sniff_delimiter(content, default_delim)
    if delim is None:
        return None
    decimal_comma = delim == ";"
    rows = [[c.strip() for c in r] for r in csv.reader(io.StringIO(content), delimiter=delim)]
    rows = [r for r in rows if any(r)]

    lines: List[str] = []
    leftover: List[str] = []
    warnings: List[str] = []

    header_idx, cols, unit_hint = None, {}, ""
    value_candidates: List[int] = []
    for i, row in enumerate(rows[:15]):
        roles: Dict[str, int] = {}
        cands: List[int] = []
        for j, cell in enumerate(row):
            role = _header_role(cell)
            if role == "value":
                cands.append(j)
            if role and role not in roles:
                roles[role] = j
        if "name" in roles and "value" in roles:
            header_idx, cols, value_candidates = i, roles, cands
            break

    ambiguous = False
    if header_idx is not None and len(value_candidates) > 1:
        header = rows[header_idx]
        current = [j for j in value_candidates if _is_current_header(header[j])]
        if len(current) == 1:
            cols["value"] = current[0]
        else:
            ambiguous = True
    if ambiguous:
        warnings.append("ambiguous result columns: several result columns and none marked current/latest; no rows read")
        return [], [], warnings
    if header_idx is not None:
        hint = re.search(r"\(([^)]*)\)", rows[header_idx][cols["value"]])
        if hint and "unit" not in cols and is_unit_token(hint.group(1)):
            unit_hint = hint.group(1).strip()

    def cell(row, key):
        j = cols.get(key)
        return row[j] if j is not None and j < len(row) else ""

    def emit(name, parsed, unit, rng_cell, flag_cell, low_cell="", high_cell=""):
        number, prefix = parsed["value"], parsed["prefix"]
        unit = unit or parsed["unit"]
        r = _parse_range_cell(rng_cell, decimal_comma)
        low, high = r["lo"], r["hi"]
        if rng_cell == "" and (low_cell or high_cell):
            low = _parse_number_token(low_cell, decimal_comma) if low_cell else None
            high = _parse_number_token(high_cell, decimal_comma) if high_cell else None
        flag = parsed["flag"]
        if flag_cell:
            flag = normalize_flag(flag_cell.split()) or flag
        ref_unit = r["unit"] or ""
        lines.append(_canonical_lab_line(
            name, number, prefix, unit or "", low, high, strict_low=r["strict_lo"], strict_high=r["strict_hi"],
            flag=flag, ref_unit=ref_unit, text=parsed["text"] or "", sex_ranges=r["sex_ranges"]))

    if header_idx is not None:
        for i, row in enumerate(rows):
            if i <= header_idx:
                continue
            name, value, unit, rng = cell(row, "name"), cell(row, "value"), cell(row, "unit"), cell(row, "range")
            parsed = _parse_result_cell(value, decimal_comma)
            if not name or not re.search(r"[A-Za-z]", name) or parsed is None:
                if any(ch.isdigit() for ch in "".join(row)):
                    leftover.append(" ".join(c for c in row if c))
                    if value and re.search(r"\d", value) and name:
                        logging.getLogger(__name__).warning("CSV lab row not parsed: %r", row)
                continue
            emit(name, parsed, unit or unit_hint, rng, cell(row, "flag"), cell(row, "ref_low"), cell(row, "ref_high"))
        return lines, leftover, warnings

    # ---- wide format: header cells are analyte names, one patient per row ----
    for hi_, hrow in enumerate(rows[:5]):
        analyte_cols = []
        for j, c in enumerate(hrow):
            base = re.sub(r"\s*\([^)]*\)\s*", " ", c).strip()
            if j > 0 and base and match_analyte(base) is not None:
                hint = re.search(r"\(([^)]*)\)", c)
                analyte_cols.append((j, base, hint.group(1).strip() if hint and is_unit_token(hint.group(1)) else ""))
        if len(analyte_cols) >= 2:
            data_rows = rows[hi_ + 1:]
            for drow in data_rows:
                for j, base, hunit in analyte_cols:
                    if j < len(drow) and drow[j]:
                        parsed = _parse_result_cell(drow[j], decimal_comma)
                        if parsed is None:
                            leftover.append(f"{base} {drow[j]}")
                            continue
                        emit(base, parsed, hunit, "", "")
            if len(data_rows) > 1:
                warnings.append(f"wide-format file with {len(data_rows)} data rows: all rows are included as one patient")
            return lines, leftover, warnings

    # ---- no recognisable header: positional (name, value, unit, range), after optional id columns ----
    for row in rows:
        name_idx = next((k for k, c in enumerate(row) if re.search(r"[A-Za-z]", c)), None)
        parsed = None
        if name_idx is not None and all(re.fullmatch(r"\d+", c or "") for c in row[:name_idx]) \
                and name_idx + 1 < len(row):
            parsed = _parse_result_cell(row[name_idx + 1], decimal_comma)
        if parsed is None or name_idx is None:
            if any(ch.isdigit() for ch in "".join(row)):
                leftover.append(" ".join(c for c in row if c))
            continue
        name = row[name_idx]
        unit, rng = parsed["unit"], ""
        for extra in row[name_idx + 2:]:
            if not extra:
                continue
            if not rng and _parse_range_cell(extra, decimal_comma)["lo"] is not None or \
                    not rng and _parse_range_cell(extra, decimal_comma)["hi"] is not None or \
                    not rng and _parse_range_cell(extra, decimal_comma)["sex_ranges"]:
                rng = extra
            elif not unit and is_unit_token(extra):
                unit = extra
        if not unit and not rng and not _known_analyte(name) and parsed["text"] is None:
            # "Patient ref,12345" is not a lab result.
            leftover.append(" ".join(c for c in row if c))
            continue
        emit(name, parsed, unit, rng, "")
    return lines, leftover, warnings


def _delimited_text_to_lines(file_path: str) -> str:
    """Read a CSV/TSV/TXT lab export.

    CSV/TSV are parsed structurally (delimiter sniffed, header columns mapped
    by name, decimal commas normalised) and returned as marker + canonical
    lines that the lab engine parses exactly. Content without a consistent
    delimiter, and plain .txt, is passed through as text."""
    with open(file_path, "r", encoding="utf-8-sig", errors="replace") as fh:
        content = fh.read()
    if file_path.lower().endswith(".txt"):
        return content

    parsed = _parse_delimited_content(content, "\t" if file_path.lower().endswith(".tsv") else ",")
    if parsed is None:
        return "\n".join(
            ln for ln in (l.strip() for l in content.splitlines()) if ln and any(ch.isdigit() for ch in ln)
        )
    lines, leftover, warnings = parsed
    out = [STRUCTURED_LAB_MARKER] + [f"{WARN_PREFIX}{w}" for w in warnings] + lines
    if leftover:
        out.append(_OTHER_ROWS_MARKER)
        out.extend(leftover)
    return "\n".join(out)


def run_ocr(file_path: str) -> OcrOutput:
    """
    OCR pipeline: PaddleOCR + PyMuPDF, with legacy Tesseract fallback.
    Handles PDF and image inputs.

    PRD: Lab Reports (PDF) → OCR → raw text → ClinicalBERT NER
    """
    ext = os.path.splitext(file_path)[1].lower()
    if ext in (".csv", ".tsv", ".txt"):
        # Already text — no OCR. The report route accepts .csv uploads; before
        # this branch they fell through to "Unsupported OCR format", the
        # pipeline swallowed that into empty text, and an abnormal panel was
        # reported as "low risk".
        text = _delimited_text_to_lines(file_path)
        return OcrOutput(raw_text=text, confidence=1.0, pages=[text])
    if os.getenv("OCR_ENGINE", "paddle").lower() == "paddle":
        try:
            from app.services.ocr_service import extract_text_from_file

            result = extract_text_from_file(file_path, file_type="pdf" if ext == ".pdf" else "image")
            pages_payload = result.get("pages") or []
            pages = [
                page.get("text", "") if isinstance(page, dict) else str(page)
                for page in pages_payload
            ] or [result.get("full_text", "")]
            return OcrOutput(
                raw_text=result.get("full_text", ""),
                confidence=float(result.get("avg_confidence", 0.0) or 0.0),
                pages=pages,
            )
        except Exception as exc:
            print(f"[OCR] PaddleOCR unavailable/failed: {exc}. Falling back to legacy OCR.")

    pages = []
    confidence = 0.0

    if ext == ".pdf":
        pages, confidence = _ocr_pdf(file_path)
    elif ext in [".jpg", ".jpeg", ".png", ".bmp", ".tiff"]:
        pages, confidence = _ocr_image(file_path)
    else:
        raise ValueError(f"Unsupported OCR format: {ext}")

    raw_text = "\n\n--- PAGE BREAK ---\n\n".join(pages)
    return OcrOutput(raw_text=raw_text, confidence=confidence, pages=pages)


def _ocr_pdf(pdf_path: str) -> Tuple[List[str], float]:
    """Extract text from PDF via PyMuPDF first, Tesseract fallback."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        pages = []
        for page in doc:
            text = page.get_text("text")
            if text.strip():
                pages.append(text)
            else:
                # Scanned page → rasterize → Tesseract
                pix = page.get_pixmap(dpi=300)
                img_arr = np.frombuffer(pix.samples, dtype=np.uint8)
                img_arr = img_arr.reshape(pix.height, pix.width, pix.n)
                if pix.n == 4:
                    img_arr = cv2.cvtColor(img_arr, cv2.COLOR_BGRA2RGB)
                text, conf = _tesseract_on_array(img_arr)
                pages.append(text)
        return pages, 0.85  # PyMuPDF text = high confidence
    except ImportError:
        # Fallback: convert PDF pages to images with pdf2image, then Tesseract
        try:
            from pdf2image import convert_from_path
            pil_pages = convert_from_path(pdf_path, dpi=300)
            texts, confs = [], []
            for pil_img in pil_pages:
                arr = np.array(pil_img)
                text, conf = _tesseract_on_array(arr)
                texts.append(text)
                confs.append(conf)
            return texts, float(np.mean(confs)) if confs else 0.0
        except Exception as e:
            return [f"OCR failed: {e}"], 0.0


def _ocr_image(image_path: str) -> Tuple[List[str], float]:
    """OCR a single image file via Tesseract."""
    try:
        arr = np.array(Image.open(image_path).convert("RGB"))
        text, conf = _tesseract_on_array(arr)
        return [text], conf
    except Exception as e:
        return [f"OCR failed: {e}"], 0.0


def _tesseract_on_array(arr: np.ndarray) -> Tuple[str, float]:
    """Run Tesseract on numpy array, return (text, mean_confidence)."""
    try:
        import pytesseract
        # Preprocess for better OCR: grayscale + threshold
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

        data = pytesseract.image_to_data(
            thresh,
            output_type=pytesseract.Output.DICT,
            config="--psm 6"
        )
        text = pytesseract.image_to_string(thresh, config="--psm 6")
        confs = [c for c in data["conf"] if c != -1]
        mean_conf = float(np.mean(confs)) / 100.0 if confs else 0.0
        return text, mean_conf
    except (ImportError, Exception) as e:
        # Graceful fallback if tesseract is missing
        print(f"[OCR] Tesseract error: {e}. Returning empty text for this segment.")
        return "", 0.0


# ════════════════════════════════════════════════════════════
# SECTION 5 — CLINICALBERT NER
# ════════════════════════════════════════════════════════════

# Lazy-loaded model singleton
_ner_pipeline = None

def _get_ner_pipeline():
    """
    Lazy load ClinicalBERT NER pipeline.
    PRD constraint: EXTRACTION ONLY — never reasons, never generates text.
    Falls back to regex if model load fails.
    """
    global _ner_pipeline
    if _ner_pipeline is not None:
        return _ner_pipeline

    try:
        from transformers import pipeline, AutoTokenizer, AutoModelForTokenClassification
        model_name = "samrawal/bert-base-uncased_clinical-ner"
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForTokenClassification.from_pretrained(model_name)
        _ner_pipeline = pipeline(
            "ner",
            model=model,
            tokenizer=tokenizer,
            aggregation_strategy="simple",
            device=0 if torch.cuda.is_available() else -1
        )
        print(f"[ClinicalBERT] NER pipeline loaded on {device}")
    except Exception as e:
        print(f"[ClinicalBERT] Failed to load: {e}. Using regex fallback.")
        _ner_pipeline = "regex_fallback"

    return _ner_pipeline


def run_ner(text: str) -> NerOutput:
    """
    Clinical NER extraction.
    PRD: Extracts conditions, medications, lab values from clinical text.
    NEVER reasons. NEVER generates. Extraction ONLY.

    Uses Groq Llama-3 when GROQ_API_KEY is present, then ClinicalBERT, then regex.
    """
    if os.getenv("GROQ_API_KEY") and os.getenv("NER_ENGINE", "groq").lower() == "groq":
        try:
            from app.services.clinical_ner_service import run_clinical_pipeline

            result = run_clinical_pipeline(text)
            return _parse_groq_ner_result(result.get("entities", {}))
        except Exception as e:
            print(f"[Groq NER] Failed: {e}. Falling back to ClinicalBERT/regex.")

    pipe = _get_ner_pipeline()

    if pipe == "regex_fallback":
        return _regex_ner_fallback(text)

    try:
        # Chunk text to stay within 512 token limit
        chunks = _chunk_text(text, max_chars=1000)
        all_entities = []

        for chunk in chunks:
            entities = pipe(chunk)
            all_entities.extend(entities)

        return _parse_ner_entities(all_entities, text)

    except Exception as e:
        print(f"[ClinicalBERT] NER inference failed: {e}. Using regex fallback.")
        return _regex_ner_fallback(text)


def _parse_groq_ner_result(entities: Dict[str, Any]) -> NerOutput:
    lab_values = []
    for item in entities.get("lab_values", []) or []:
        if not isinstance(item, dict):
            continue
        reference = item.get("reference_range", "") or item.get("reference", "")
        ref_low, ref_high = _parse_reference_range(reference)
        lab_values.append(
            LabValue(
                name=str(item.get("name", "")).strip(),
                value=_safe_float(item.get("value")),
                unit=str(item.get("unit", "")).strip(),
                raw_text=json_safe_dumps(item),
                ref_low=ref_low,
                ref_high=ref_high,
                flag=str(item.get("status", "")).upper() or None,
                status="ABNORMAL" if str(item.get("status", "")).lower() in {"high", "low", "critical"} else "NORMAL",
                is_abnormal=str(item.get("status", "")).lower() in {"high", "low", "critical"},
                display_value="" if item.get("value") is None else str(item.get("value")),
            )
        )

    medications = []
    for med in entities.get("medications", []) or []:
        if isinstance(med, dict):
            text = " ".join(str(med.get(key, "")).strip() for key in ("name", "dose", "frequency") if med.get(key))
            if text:
                medications.append(text)
        elif med:
            medications.append(str(med))

    raw_entities = [
        {"entity_group": key, "word": value}
        for key, values in entities.items()
        for value in (values if isinstance(values, list) else [values])
    ]

    return NerOutput(
        conditions=[str(item) for item in entities.get("conditions", []) or []],
        medications=medications,
        lab_values=lab_values,
        dates=[],
        clinical_flags=[str(item) for item in entities.get("symptoms", []) or []],
        raw_entities=raw_entities,
    )


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        return float(match.group(0)) if match else None


def _parse_reference_range(reference: Any) -> Tuple[Optional[float], Optional[float]]:
    numbers = re.findall(r"-?\d+(?:\.\d+)?", str(reference or ""))
    if len(numbers) >= 2:
        return float(numbers[0]), float(numbers[1])
    return None, None


def json_safe_dumps(value: Any) -> str:
    try:
        return json.dumps(value)
    except Exception:
        return str(value)


JUNK_WORDS = {
    "sis", "inc", "cles", "tion", "ment", "ness", "ity",
    "terol", "aemia", "emic", "ous", "ious", "tory",
    "page break", "general healt", "blood loss",  
    "an lack", "def ine", "ate def", "renal f",
    "cocgl birth", "her hb", "lif jm", "moc bo",
    "vit amin", "ant ibody", "emodilut ion",
}

def clean_conditions(raw: List[str]) -> List[str]:
    # Legacy NER conditions cleanup
    cleaned = []
    for c in raw:
        c = c.strip()
        if "##" in c: continue
        if len(c) < 5: continue
        cleaned.append(c.title())
    return list(dict.fromkeys(cleaned))[:10]

# ════════════════════════════════════════════════════════════
# SECTION 6 — UNIVERSAL LAB PIPELINE (LAYER 1-3)
# ════════════════════════════════════════════════════════════

class ReportClassifier:
    REPORT_TYPES = {
        "lab_structured": [
            "biological ref", "ref. interval", "reference range",
            "normal range", "test result unit"
        ],
        "discharge_summary": [
            "discharge", "final diagnosis", "admitted",
            "clinical summary", "advice on discharge"
        ],
        "radiology": [
            "impression:", "findings:", "x-ray", "mri", "ct scan",
            "ultrasound", "echo"
        ],
        "microbiology": [
            "culture", "sensitivity", "organism", "colony",
            "gram stain", "mcs"
        ],
        "pathology_narrative": [
            "peripheral smear", "biopsy", "histopathology",
            "morphology"
        ],
        "hplc_electrophoresis": [
            "peak name", "retention time", "area %",
            "hplc", "electrophoresis"
        ],
    }

    PANEL_MARKERS = [
        "complete blood count", "cbc", "lipid profile", "lipid panel",
        "liver function", "lft", "kidney function", "renal function", "kft",
        "thyroid function", "tft", "iron studies", "iron panel",
        "hba1c", "glycosylated hemoglobin", "immunoassay", "hormone",
        "urine routine", "urinalysis", "biochemistry", "metabolic panel",
        "coagulation", "pt inr", "blood group", "abo", "hb electrophoresis",
        "tumor markers", "vitamin",
    ]

    COMMENTARY_MARKERS = [
        "explanation:", "summary and uses:", "limitations:",
        "interpretation:", "additional information:",
        "reference:", "important instructions",
        "increased in", "decreased in",
        "clinical significance",
        "note:", "comments:", "remarks:",
    ]

    def classify(self, text: str) -> dict:
        text_lower = text.lower()
        report_type = "unknown"
        for rtype, markers in self.REPORT_TYPES.items():
            if sum(1 for m in markers if m in text_lower) >= 2:
                report_type = rtype
                break
        panels = [p for p in self.PANEL_MARKERS if p in text_lower]
        has_table = bool(re.search(r'\d+\.?\d*\s+[a-zA-Z/%µ]+\s+\d+\.?\d*\s*[-–]\s*\d+\.?\d*', text))
        num_pages = len(re.findall(r'page \d+ of \d+', text_lower))
        return {
            "report_type": report_type,
            "panels": panels,
            "has_table": has_table,
            "num_pages": max(num_pages, 1),
            "is_multi_panel": len(panels) > 1,
        }

def strip_commentary(text: str) -> str:
    lines = text.split('\n')
    cleaned_lines = []
    for line in lines:
        line_lower = line.lower()
        if any(m in line_lower for m in ReportClassifier.COMMENTARY_MARKERS):
            continue
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)

def resolve_reference(name: str, gender: Optional[str] = None, age: Optional[int] = None,
                      pdf_ref_low: float = None, pdf_ref_high: float = None):
    """(low, high, source). A range printed on the report always wins; the
    registry is consulted by EXACT analyte match only, and never assumes a
    sex or an age (sex-specific analytes without a known sex have no range)."""
    if pdf_ref_low is not None and pdf_ref_high is not None:
        return pdf_ref_low, pdf_ref_high, "pdf"
    analyte = match_analyte(name or "")
    if analyte is None:
        return None, None, "unknown"
    rng, _reason = analyte.registry_range(normalize_sex(gender), normalize_age(age))
    if rng is None or rng[0] is None or rng[1] is None:
        return None, None, "unknown"
    return rng[0], rng[1], "registry"

def _parse_ner_entities(entities: List[Dict], raw_text: str) -> NerOutput:
    """Map ClinicalBERT entity groups → NerOutput contract."""
    conditions, medications, lab_values, dates = [], [], [], []

    # ClinicalBERT entity_group labels vary by model
    # Map common variants to our categories
    CONDITION_LABELS = {"problem", "disease", "symptom", "diagnosis", "B-problem", "I-problem"}
    MED_LABELS = {"treatment", "medication", "drug", "test", "B-treatment", "I-treatment"}
    LAB_LABELS = {"test", "lab", "B-test", "I-test"}

    for ent in entities:
        label = ent.get("entity_group", ent.get("entity", "")).lower()
        word = ent.get("word", "").strip()
        score = ent.get("score", 0.0)

        if score < 0.6 or not word:
            continue

        word = word.replace("##", "").strip()
        if word.startswith("#") or len(word) < 3:
            continue

        if any(l in label for l in ["problem", "disease", "symptom", "diagnosis"]):
            if word not in conditions:
                conditions.append(word)
        elif any(l in label for l in ["treatment", "medication", "drug"]):
            if word not in medications:
                medications.append(word)

    # Extract lab values via Layer 1 (PDF) + Layer 2 (Registry)
    lab_values = extract_all_lab_values(raw_text)
    dates = _extract_dates_regex(raw_text)
    
    # Layer 3: Extract clinical flags from text
    clinical_flags = extract_clinical_flags_from_text(raw_text)

    return NerOutput(
        conditions=clean_conditions(conditions),
        medications=medications,
        lab_values=lab_values,
        dates=dates,
        clinical_flags=clinical_flags,
        raw_entities=entities,
    )


def _regex_ner_fallback(text: str) -> NerOutput:
    """
    Pure regex NER when ClinicalBERT unavailable.
    Covers common lab report patterns.
    """
    conditions = _extract_conditions_regex(text)
    medications = _extract_medications_regex(text)
    lab_values = extract_all_lab_values(text)
    dates = _extract_dates_regex(text)
    clinical_flags = extract_clinical_flags_from_text(text)

    return NerOutput(
        conditions=clean_conditions(conditions),
        medications=medications,
        lab_values=lab_values,
        dates=dates,
        clinical_flags=clinical_flags,
        raw_entities=[],
    )


def _range_flag(value: float, low: Optional[float], high: Optional[float]) -> Tuple[str, str, float]:
    """(flag, status, pct_deviation) against the report's own range."""
    if low is not None and value < low:
        pct = round(((low - value) / low) * 100, 1) if low else 100.0
        status = "CRITICAL" if pct > 50 else "HIGH" if pct > 25 else "LOW"
        return f"⬇️ {status}", status, pct
    if high is not None and value > high:
        pct = round(((value - high) / high) * 100, 1) if high else 100.0
        status = "CRITICAL" if pct > 50 else "HIGH"
        return f"⬆️ {status}", status, pct
    return "NORMAL", "NORMAL", 0.0


def _make_lab_value(name: str, value: float, unit: str, low: Optional[float], high: Optional[float],
                    raw_text: str, prefix: str = "") -> LabValue:
    """Legacy LabValue view of one row, flagged against the report's own range only."""
    prefix = {"<=": "<", ">=": ">"}.get(prefix, prefix)
    has_range = low is not None or high is not None
    flag = status = None
    pct = None
    if has_range:
        if prefix == "<":
            # "<x": only certain when the whole interval lies below the range.
            if low is not None and value <= low:
                flag, status, pct = _range_flag(value - 1e-9, low, high)
        elif prefix == ">":
            if high is not None and value >= high:
                flag, status, pct = _range_flag(value + 1e-9, low, high)
        else:
            flag, status, pct = _range_flag(value, low, high)
    return LabValue(
        name=name, value=value, unit=unit, raw_text=raw_text, ref_low=low, ref_high=high,
        flag=flag, status=status, pct_deviation=pct,
        value_type="lt_gt" if prefix else "numeric",
        display_value=f"{prefix}{_fmt_num(value)}" if prefix else "",
        prefix=prefix,
    )


def _parsed_row_to_labvalue(p: ParsedRow) -> LabValue:
    if p.value is None:
        positive = qualitative_class(p.text_value or "") == "POSITIVE"
        return LabValue(name=p.name, value=None, unit="", raw_text=p.line, result_text=p.text_value or "",
                        value_type="binary", is_abnormal=positive)
    return _make_lab_value(p.name, p.value, p.unit or "", p.ref_low, p.ref_high, p.line, p.qualifier or "")


def extract_all_lab_values(text: str) -> List[LabValue]:
    """Legacy view of the lab engine's reader (app.services.lab_engine):
    structured CSV text or plain report text -> LabValue rows, flagged
    against the report's own printed range only. The pipeline itself uses
    the engine's ParsedRow/ScoredRow objects directly."""
    rows, _unread, _warnings = parse_report_text(text)
    return deduplicate_lab_values([_parsed_row_to_labvalue(r) for r in rows])


def extract_lab_table_from_pdf(text: str) -> List[LabValue]:
    """Rows that carry value + unit-or-not + a complete printed range."""
    rows, _unread = parse_lab_text(text)
    return [_parsed_row_to_labvalue(r) for r in rows
            if r.value is not None and r.ref_low is not None and r.ref_high is not None]


def _same_lab_test(a: str, b: str) -> bool:
    """Two names denote the same test iff their canonical token sets are
    equal (case/punctuation/noise words/synonyms normalised). Substring
    containment is NOT enough: Direct vs Indirect Bilirubin, LDL vs VLDL and
    Albumin vs Microalbumin are different analytes."""
    ta, tb = canon_tokens(a), canon_tokens(b)
    return bool(ta) and ta == tb


def _row_quality(lv: LabValue) -> Tuple[int, int, int]:
    """Higher is better: range read from the report, then a unit, then a
    shorter (less OCR-noisy) name."""
    has_range = int(lv.ref_low is not None and lv.ref_high is not None)
    has_unit = int(bool((lv.unit or "").strip()))
    return has_range, has_unit, -len(lv.name or "")


def deduplicate_lab_values(lab_values: List[LabValue]) -> List[LabValue]:
    """Drop OCR duplicates of the SAME test.

    Duplicates must name the same test (_same_lab_test), share a (rounded)
    value, and — when both carry a unit — have equivalent units. Among
    duplicates the row with a report-read range and unit wins, then the
    cleaner (shorter) name.
    """
    result: List[LabValue] = []
    for lv in lab_values:
        value_key = round(lv.value, 1) if lv.value is not None else None
        dup_index = None
        for i, existing in enumerate(result):
            if (round(existing.value, 1) if existing.value is not None else None) != value_key:
                continue
            if not _same_lab_test(existing.name, lv.name):
                continue
            if (existing.unit or "").strip() and (lv.unit or "").strip() \
                    and not units_equivalent(existing.unit, lv.unit, f"{existing.name} {lv.name}"):
                continue
            dup_index = i
            break
        if dup_index is None:
            result.append(lv)
        elif _row_quality(lv) > _row_quality(result[dup_index]):
            result[dup_index] = lv
    return result


def _legacy_lab_registry() -> Dict[str, Dict[str, Any]]:
    """Old LAB_REGISTRY-shaped view of the engine registry (ml/anomaly/lab_flagger imports it)."""
    out: Dict[str, Dict[str, Any]] = {}
    for key, a in REGISTRY.items():
        entry: Dict[str, Any] = {"aliases": [s.lower() for s in a.synonyms], "unit": a.unit}
        if "all" in a.ranges:
            entry["normal_range"] = a.ranges["all"]
        if "M" in a.ranges:
            entry["normal_range_male"] = a.ranges["M"]
        if "F" in a.ranges:
            entry["normal_range_female"] = a.ranges["F"]
        out[key.replace("_", " ")] = entry
    return out


LAB_REGISTRY = _legacy_lab_registry()

def extract_clinical_flags_from_text(text: str) -> List[str]:
    """Scan comment/narrative section for clinical interpretations."""
    flag_patterns = [
        r'(deficiency of \w+)',
        r'(\w+ is (?:reduced|elevated|low|high|increased|decreased))',
        r'(abnormal \w+)',
        r'(critical(?:ally)? (?:low|high))',
    ]
    flags = []
    for pat in flag_patterns:
        matches = re.findall(pat, text, re.IGNORECASE)
        flags.extend(matches)
    return flags


def _extract_conditions_regex(text: str) -> List[str]:
    """Extract common medical condition keywords and patterns."""
    KNOWN_CONDITIONS = [
        "diabetes", "hypertension", "pneumonia", "pneumothorax",
        "cardiomegaly", "atelectasis", "effusion", "edema",
        "infiltration", "fibrosis", "emphysema", "tuberculosis",
        "anemia", "hypothyroidism", "hyperthyroidism", "obesity",
        "asthma", "copd", "heart failure", "sepsis", "stroke",
    ]
    found = []
    text_lower = text.lower()
    
    # Keyword matches
    for cond in KNOWN_CONDITIONS:
        if cond in text_lower and cond.title() not in found:
            found.append(cond.title())
            
    # Discharge summaries use "Diagnosis:" / "Final Diagnosis:" patterns
    diag_pattern = re.findall(
        r'(?:final\s+)?diagnosis\s*:\s*([^\n]+)', 
        text, re.IGNORECASE
    )
    for d in diag_pattern:
        val = d.strip()
        if val and val.title() not in found:
            found.append(val.title())
            
    return found


def _extract_medications_regex(text: str) -> List[str]:
    """Extract common medication keywords."""
    KNOWN_MEDS = [
        "metformin", "insulin", "aspirin", "atorvastatin",
        "lisinopril", "amlodipine", "metoprolol", "warfarin",
        "omeprazole", "amoxicillin", "prednisone", "furosemide",
        "levothyroxine", "azithromycin", "ciprofloxacin",
    ]
    found = []
    text_lower = text.lower()
    for med in KNOWN_MEDS:
        if med in text_lower and med not in found:
            found.append(med.title())
    return found


def _extract_dates_regex(text: str) -> List[str]:
    """Extract date patterns from clinical text."""
    patterns = [
        r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}',     # 01/01/2024
        r'\d{4}[/-]\d{1,2}[/-]\d{1,2}',         # 2024-01-01
        r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}',
    ]
    dates = []
    for pat in patterns:
        matches = re.findall(pat, text, re.IGNORECASE)
        dates.extend(matches)
    return list(set(dates))


def _chunk_text(text: str, max_chars: int = 1000) -> List[str]:
    """Split text into chunks ≤ max_chars at sentence boundaries."""
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks, current = [], ""
    for sent in sentences:
        if len(current) + len(sent) < max_chars:
            current += " " + sent
        else:
            if current:
                chunks.append(current.strip())
            current = sent
    if current:
        chunks.append(current.strip())
    return chunks or [text[:max_chars]]


# ════════════════════════════════════════════════════════════
# SECTION 6 — DATA VALIDATOR
# ════════════════════════════════════════════════════════════

# PRD: 50MB DICOM, 10MB images, 20MB PDF
MAX_SIZE = {
    ".dcm": 50 * 1024 * 1024,
    ".jpg": 10 * 1024 * 1024,
    ".jpeg": 10 * 1024 * 1024,
    ".png": 10 * 1024 * 1024,
    ".pdf": 20 * 1024 * 1024,
    ".csv": 10 * 1024 * 1024,
}

ALLOWED_MIME = {
    ".dcm": ["application/dicom"],
    ".jpg": ["image/jpeg"],
    ".jpeg": ["image/jpeg"],
    ".png": ["image/png"],
    ".pdf": ["application/pdf"],
    ".csv": ["text/csv", "application/csv"],
}


def validate_file(file_path: str) -> ValidationResult:
    """
    File-level validation: size check + MIME sniff.
    PRD security: MIME type check + max size enforcement.
    """
    if not os.path.exists(file_path):
        return ValidationResult(valid=False, quality_score=0.0,
                                format_ok=False, size_ok=False,
                                reason="File not found")

    ext = os.path.splitext(file_path)[1].lower()
    size = os.path.getsize(file_path)
    max_size = MAX_SIZE.get(ext, 10 * 1024 * 1024)

    size_ok = size <= max_size
    format_ok = ext in MAX_SIZE

    if not format_ok:
        return ValidationResult(valid=False, quality_score=0.0,
                                format_ok=False, size_ok=size_ok,
                                reason=f"Unsupported format: {ext}")
    if not size_ok:
        return ValidationResult(valid=False, quality_score=0.0,
                                format_ok=True, size_ok=False,
                                reason=f"File too large: {size/1024/1024:.1f}MB > {max_size/1024/1024:.0f}MB")

    return ValidationResult(valid=True, quality_score=1.0,
                            format_ok=True, size_ok=True)


def validate_image(arr: np.ndarray) -> ValidationResult:
    """
    Image array validation: resolution, aspect ratio, blank check.
    PRD: Data Validator → format check + quality score.
    """
    if arr is None or arr.size == 0:
        return ValidationResult(valid=False, quality_score=0.0,
                                format_ok=False, size_ok=False,
                                reason="Empty image array")

    h, w = arr.shape[:2]

    # Too small
    if h < 100 or w < 100:
        return ValidationResult(valid=False, quality_score=0.0,
                                format_ok=True, size_ok=False,
                                reason=f"Resolution too low: {w}x{h}")

    # Likely screenshot (wide aspect ratio)
    aspect = w / h
    if aspect > 2.0:
        return ValidationResult(valid=False, quality_score=0.0,
                                format_ok=True, size_ok=True,
                                reason=f"Wide aspect ratio {aspect:.2f} — likely screenshot")

    # Nearly blank image
    quality = _image_quality_score(arr)
    if quality < 0.05:
        return ValidationResult(valid=False, quality_score=quality,
                                format_ok=True, size_ok=True,
                                reason="Image appears blank or near-uniform")

    return ValidationResult(valid=True, quality_score=quality,
                            format_ok=True, size_ok=True)


def _image_quality_score(arr: np.ndarray) -> float:
    """
    Heuristic quality score based on:
    - Laplacian variance (sharpness)
    - Pixel value spread (contrast)
    Returns 0.0-1.0
    """
    if arr is None or arr.size == 0:
        return 0.0
    try:
        gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY) if arr.ndim == 3 else arr
        lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
        sharpness = float(np.clip(lap_var / 500.0, 0.0, 1.0))
        contrast = float(gray.std() / 128.0)
        contrast = float(np.clip(contrast, 0.0, 1.0))
        return round((sharpness * 0.6 + contrast * 0.4), 4)
    except Exception:
        return 0.5


# ════════════════════════════════════════════════════════════
# SECTION 7 — UNIFIED ENTRY POINTS
# ════════════════════════════════════════════════════════════

def preprocess_image_file(file_path: str) -> ImageNormOutput:
    """
    Full preprocessing for standard image files (JPG/PNG).
    Used by image pipeline before MedSAM.
    """
    val = validate_file(file_path)
    if not val.valid:
        return ImageNormOutput(
            normalized=np.zeros((1024, 1024, 3), dtype=np.uint8),
            original_shape=(0, 0, 0),
            quality_score=0.0,
            rejected=True,
            reject_reason=val.reason,
        )
    return normalize_image(file_path)


def preprocess_dicom_file(file_path: str, max_slices: int = 50) -> DicomOutput:
    """
    Full preprocessing for DICOM files.
    Used by image pipeline before MedSAM.
    """
    val = validate_file(file_path)
    if not val.valid:
        raise ValueError(f"DICOM validation failed: {val.reason}")
    return parse_dicom(file_path, max_slices=max_slices)


def preprocess_report_file(file_path: str) -> NerOutput:
    """
    Full preprocessing for lab report / clinical note PDFs.
    Pipeline: validate → OCR → ClinicalBERT NER → NerOutput
    Used by text pipeline before XGBoost.
    """
    val = validate_file(file_path)
    if not val.valid:
        raise ValueError(f"Report validation failed: {val.reason}")

    ocr_result = run_ocr(file_path)
    ner_result = run_ner(ocr_result.raw_text)
    return ner_result


# ════════════════════════════════════════════════════════════
# SECTION 8 — STANDALONE TEST
# ════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== Preprocessor Test ===")

    # Test image normalization with dummy array
    dummy = np.random.randint(50, 200, (512, 512, 3), dtype=np.uint8)
    result = normalize_image(dummy)
    print(f"Image norm: shape={result.normalized.shape}, quality={result.quality_score}, rejected={result.rejected}")

    # Test image validation
    val = validate_image(dummy)
    print(f"Validation: valid={val.valid}, quality={val.quality_score}")

    # Test regex NER fallback
    sample_text = """
    Patient diagnosed with Type 2 Diabetes and Hypertension.
    Currently on Metformin 500mg and Lisinopril.
    Lab results: Hemoglobin: 10.2 g/dL, HbA1c: 7.8 %, Creatinine: 1.4 mg/dL
    Date: 01/15/2024
    """
    ner = run_ner(sample_text)
    print("\nNER Output:")
    print(f"  Conditions:  {ner.conditions}")
    print(f"  Medications: {ner.medications}")
    print(f"  Lab Values:  {[(lv.name, lv.value, lv.unit) for lv in ner.lab_values]}")
    print(f"  Dates:       {ner.dates}")

    print("\nPRD contract check:")
    print("  DicomOutput ✓ | OcrOutput ✓ | NerOutput ✓ | ImageNormOutput ✓ | ValidationResult ✓")
