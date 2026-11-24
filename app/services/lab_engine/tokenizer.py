"""Tokenising lab-report lines into ParsedRow objects.

parse_line_rows() reads ONE logical line by tokenising it (name tokens, then a
value, then flag/unit/range tokens in any order) rather than matching one big
regex, so every printed layout the reviewers threw at the old regex (flag
between value and unit, glued units, range before unit, one-sided ranges,
qualifiers, bracketed ranges, bare "K"/"Na" names, cell-per-line PDF text ...)
goes through the same code path. The rules that keep it honest:

  * a line can hold SEVERAL tests ("Sodium 138 mmol/L 135-145  Potassium 6.9
    mmol/L 3.5-5.1"): after one row's value/unit/range the remainder is parsed
    again as further rows; nothing after the first test is dropped;
  * the result is the number directly followed by a unit, a flag, a range or
    the end of the line; a number followed by name words ("2 hr post prandial",
    "25 Hydroxy", "B 12") is part of the NAME;
  * two bare numbers before the unit (cumulative "Previous Current" layout) are
    resolved only from a column header (previous/prior vs current/latest, or
    two dates: the latest is current); otherwise the row is ambiguous and the
    engine reports UNKNOWN with a warning;
  * every printed range keeps its LABEL: only unlabelled / neutral / normal-like
    ("Ref", "Normal", "Desirable", "Sufficiency") ranges, or sex / age labelled
    ranges that match the patient, are reference ranges. "Deficiency: <20",
    "Low: <40", "Kidney failure: <15", "0-14 yrs:" are interpretation, not range;
  * lines that are only interpretation prose / category lists are not rows and
    are not "unread results"; a line that does look like a result but cannot be
    read IS counted by parse_lab_text() so it is reported, never swallowed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional, Tuple

from .models import ParsedRow, RangeOption
from .units import NUM_RE, fmt_num, is_unit_token, parse_number

# ── vocabularies ─────────────────────────────────────────────────────────────

# Header / administrative words: a line carrying one of them is not a result unless a real unit,
# a qualitative result word or a known analyte says otherwise.
STRONG_DEMOGRAPHIC = {
    "age", "yrs", "yr", "years", "year", "id", "uhid", "ward", "bed", "date", "page", "pages", "dr", "mobile",
    "mob", "phone", "tel", "telephone", "collected", "reported", "visit", "lab", "mrn", "reg", "registration",
    "registered", "barcode", "sid", "refd", "order", "bill", "invoice", "receipt", "address", "pin", "pincode",
    "fax", "email", "nabl", "accreditation", "license", "licence", "printed", "received", "dob", "birth", "sex",
    "gender", "doctor", "physician", "consultant", "referred", "accession", "episode", "room", "floor",
    "specimen", "sampled", "drawn", "centre", "center", "branch", "hospital", "clinic", "laboratory", "labs",
    "signed", "verified", "authorised", "authorized", "checked", "pathologist", "technician", "version",
    "revision", "history", "impression", "diagnosis", "advice", "mr", "mrs", "ms", "street", "road", "www",
    "http", "https", "total_pages", "since", "batch", "lot", "serial", "sheet",
}
# Words that also occur inside real test names ("HBsAg BY ELISA", "HIV Status", "Patient INR", "eGFR by CKD-EPI"):
# they only mark a line as administrative when it has no qualitative result, no unit and no known analyte.
WEAK_DEMOGRAPHIC = {
    "by", "status", "method", "sample", "patient", "no", "number", "name", "test", "tests", "report", "type",
    "ref", "reference", "result", "results", "comment", "comments", "remark", "remarks", "note", "notes",
    "interpretation", "refd",
}
DEMOGRAPHIC = STRONG_DEMOGRAPHIC | WEAK_DEMOGRAPHIC

# Section / header words: a dropped line carrying one ("Page 1 of 2", "LIPID PROFILE (8 parameters)",
# "Complete Blood Count (CBC) - 2 tests", "Test 3 of 5") is a heading, not an unread result.
HEADER_LIKE = {"page", "pages", "of", "test", "tests", "panel", "panels", "profile", "profiles", "parameter",
               "parameters", "section", "group", "batch", "set", "sheet", "report", "summary", "results",
               "investigations", "investigation", "items", "item", "pg", "copy"}

PROSE_START = {"interpretation", "interpretations", "note", "notes", "comment", "comments", "remark", "remarks",
               "kindly", "please", "advice", "impression", "disclaimer", "instructions", "clinical", "criteria",
               "guideline", "guidelines", "method", "methodology", "instrument", "analyzer", "analyser", "kit",
               "reagent", "principle", "technique", "limitations", "limitation", "recommendation", "recommendations"}
PROSE_WORDS = {"is", "are", "was", "were", "the", "this", "that", "these", "those", "indicates", "indicate",
               "indicating", "suggests", "suggest", "suggestive", "denotes", "means", "represents", "consistent",
               "considered", "defined", "recommended", "should", "may", "might", "must", "therefore", "however",
               "because", "whereas", "associated", "advised", "please", "kindly", "if", "shown"}

# Words of interpretive category lists ("Deficiency: <20", "Borderline high 130-159", "Kidney failure: <15").
CATEGORY_VOCAB = {
    "deficient", "deficiency", "insufficient", "insufficiency", "sufficient", "sufficiency", "adequate",
    "inadequate", "normal", "abnormal", "borderline", "high", "low", "very", "near", "optimal", "suboptimal",
    "desirable", "undesirable", "mild", "mildly", "moderate", "moderately", "severe", "severely", "critical",
    "toxic", "toxicity", "kidney", "failure", "renal", "impairment", "impaired", "prediabetes", "pre", "diabetes",
    "diabetic", "non", "nondiabetic", "hypothyroid", "hyperthyroid", "hypothyroidism", "hyperthyroidism",
    "euthyroid", "subclinical", "overt", "increased", "decreased", "elevated", "reduced", "risk", "cutoff", "cut",
    "off", "stage", "stages", "ckd", "anemia", "anaemia", "obese", "obesity", "overweight", "underweight",
    "healthy", "target", "goal", "expected", "acceptable", "positive", "negative", "equivocal", "reactive",
    "nonreactive", "immune", "protective", "level", "levels", "range", "ranges", "reference", "ref",
    "interval", "intervals", "above", "below", "upto", "up", "to", "than", "less", "more", "greater", "equal",
    "and", "or", "of", "for", "in", "the", "a", "hyper", "hypo", "glycemic", "control", "poor", "good", "fair",
    "excellent", "ideal", "lipid", "intermediate", "indeterminate", "borderline", "ng", "ml", "mg", "dl", "bio",
    "biological", "value", "values", "limit", "limits", "diagnostic", "diagnosis", "hypoglycemia",
    "hyperglycemia", "hyperglycaemia", "hypoglycaemia", "fasting", "random", "postprandial", "pp", "g1", "g2",
    "g3a", "g3b", "g4", "g5", "grade", "category", "unit", "units", "adult", "adults", "children", "child",
    "pediatric", "paediatric", "infant", "infants", "neonate", "neonates", "newborn", "men", "women", "male",
    "female", "males", "females", "man", "woman", "pregnancy", "pregnant", "trimester", "first", "second",
    "third", "st", "nd", "rd", "th", "geriatric", "elderly", "adolescent", "teen", "general", "population",
}
# Labels that make a printed range THE reference range (neutral / normal-like).
NEUTRAL_LABEL_WORDS = {
    "ref", "reference", "refs", "range", "ranges", "interval", "intervals", "bio", "biological", "biol", "value",
    "values", "level", "levels", "expected", "normal", "desirable", "optimal", "sufficient", "sufficiency",
    "adequate", "acceptable", "healthy", "target", "euthyroid", "general", "population", "lab", "laboratory",
    "limit", "limits", "is", "for", "the", "of", "and", "a", "to", "in", "all", "ages", "age", "any",
}
_SEX_WORDS_M = {"male", "males", "men", "man", "m", "gents"}
_SEX_WORDS_F = {"female", "females", "women", "woman", "f", "ladies"}
_AGE_WORDS = {
    "adult": (18.0, 200.0), "adults": (18.0, 200.0), "child": (0.0, 17.99), "children": (0.0, 17.99),
    "pediatric": (0.0, 17.99), "paediatric": (0.0, 17.99), "infant": (0.0, 1.99), "infants": (0.0, 1.99),
    "neonate": (0.0, 0.08), "neonates": (0.0, 0.08), "newborn": (0.0, 0.08), "newborns": (0.0, 0.08),
    "kid": (0.0, 17.99), "kids": (0.0, 17.99), "geriatric": (60.0, 200.0), "elderly": (60.0, 200.0),
    "adolescent": (12.0, 17.99), "adolescents": (12.0, 17.99), "teen": (12.0, 17.99), "teens": (12.0, 17.99),
}

_QUAL_POSITIVE = {"reactive", "positive", "present", "detected", "trace", "+", "++", "+++", "++++"}
_QUAL_NEGATIVE = {"non-reactive", "nonreactive", "negative", "absent", "nil", "not detected", "non reactive"}
_QUAL_WORDS = ("non-reactive", "non reactive", "nonreactive", "reactive", "not detected", "detected", "positive",
               "negative", "present", "absent", "nil", "trace", "+", "++", "+++", "++++")

_FLAG_WORDS = {"h": "H", "l": "L", "hh": "HH", "ll": "LL", "high": "H", "low": "L", "*": "ABN", "**": "ABN",
               "abnormal": "ABN", "abn": "ABN", "critical": "CRIT", "↑": "H", "↓": "L"}
_NORMAL_WORDS = {"normal", "wnl", "n", "nrml"}

_VALUE_RE = re.compile(rf"^(?P<q><=|>=|<|>|=)?(?P<n>[+-]?(?:{NUM_RE}))$")
_NUMERICISH_RE = re.compile(r"^[<>=]*[+-]?\d[\d.,]*(?:[eE][+-]?\d+)?$")
_RANGE_LABEL = (r"(?:(?:bio\.?\s*)?(?:ref(?:erence)?\.?)(?:\s*(?:range|interval|value|values))?"
                r"|normal(?:\s+(?:range|values?))?|range|desirable|optimal|expected|adult)")
_NUM = NUM_RE
_TWO_SIDED = re.compile(
    rf"(?P<lo>[+-]?{_NUM})\s*(?:(?P<dash>-|~)\s*(?P<hi>{_NUM})|\s+to\s+(?P<hi2>[+-]?{_NUM}))(?![\d/]|-\d|\.\d)", re.I)
_ONE_SIDED_HIGH = re.compile(
    rf"(?:(?P<op><=|<)\s*|(?P<w>up\s*to|upto|less\s+than|below|max(?:imum)?\.?|not\s+more\s+than)\s*)(?P<n>{_NUM})", re.I)
_ONE_SIDED_LOW = re.compile(
    rf"(?:(?P<op>>=|>)\s*|(?P<w>more\s+than|above|greater\s+than|min(?:imum)?\.?|not\s+less\s+than)\s*)(?P<n>{_NUM})", re.I)
_SEX_LABEL = re.compile(r"(?P<s>male|female|males|females|men|women)\s*[:\-]?\s*|(?P<s2>[mf])\s*:\s*", re.I)
_LABEL = re.compile(rf"{_RANGE_LABEL}\s*[:=\-]?\s*", re.I)
_DATEISH = re.compile(r"\d{1,4}[-/]\d{1,2}[-/]\d{2,4}")
_FLAG_TOKEN = re.compile(
    r"(?:HH|LL|H|L|N|\*{1,2}|↑|↓|(?i:high|low|abnormal|abn|critical(?:ly)?(?:\s+(?:high|low))?|normal|wnl))(?=\s|$)")
_AGE_UNITS = r"(?:years?|yrs?|y|months?|mos?|days?|weeks?|wks?)"
_AGE_LABEL = re.compile(
    rf"(?:age\s*[:\-]?\s*)?(?P<op>[<>]=?)?\s*(?P<a>{_NUM})(?:\s*-\s*(?P<b>{_NUM}))?\s*(?P<u>{_AGE_UNITS})\b\.?\s*[:\-]?", re.I)
_RANGE_TOKEN = re.compile(rf"^[+-]?{_NUM}-{_NUM}$")
_RANGE_TOKEN_G = re.compile(rf"^(?P<lo>[+-]?{_NUM})-(?P<hi>{_NUM})$")


def _is_range_token(tok: str) -> bool:
    """"13.0-17.0" is a range; "19-9" (CA 19-9), "15-3" are name fragments: a range never runs downhill."""
    m = _RANGE_TOKEN_G.match(tok)
    if not m:
        return False
    lo, hi = parse_number(m.group("lo")), parse_number(m.group("hi"))
    return lo is not None and hi is not None and lo <= hi
_HPF_UNIT = re.compile(r"^/\s*(?:hpf|lpf|hp|lp|field|hpf\.)", re.I)


def normalize_line(line: str) -> str:
    s = (line or "").replace(" ", " ").replace("\t", " ")
    for a, b in (("–", "-"), ("—", "-"), ("−", "-"), ("‒", "-"), ("≤", "<="), ("≥", ">="), ("×", "x")):
        s = s.replace(a, b)
    sup = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9"}
    s = re.sub(r"10([⁰¹²³⁴⁵⁶⁷⁸⁹]+)", lambda m: "10^" + "".join(sup[c] for c in m.group(1)), s)
    # "x 10^3/uL" / "X 10³ / µL" / "x10E3/uL" / "x 10*9/L" -> "x10^3/uL". The replacement MUST use \g<n>:
    # "\1" followed by a digit is read as an octal escape ("\110" == "H"), which turned "7.2 x 10^3/uL" into "7.2 H^3/uL".
    s = re.sub(r"(?i)\b[x×]\s*10\s*(?:\^|\*|e)?\s*(\d{1,2})\b", r"x10^\g<1>", s)
    s = re.sub(r"10\s*\^\s*(\d)", r"10^\g<1>", s)
    s = re.sub(r"(10\^\d{1,2})\s*/\s*(?=[A-Za-zµμ])", r"\g<1>/", s)
    s = re.sub(r"(?i)(10\^\d{1,2})\s+cells?\s*/", r"\g<1>/", s)          # "x10^3 cells/uL" -> "x10^3/uL"
    s = s.replace("|", " ")                                                    # table rules are column breaks
    s = re.sub(r";(?=\s|$)", " ", s)                                           # "Hemoglobin; 6.1; g/dL"
    s = re.sub(r"(?<=\d),(?=\s|$)", " ", s)                                   # "6.1, g/dL"
    s = re.sub(r"cu\.?\s*mm", "cumm", s, flags=re.I)
    s = re.sub(r"(?<=[A-Za-zµμ%])\s+/\s*(?=[A-Za-zµμ])|(?<=[A-Za-zµμ%])\s*/\s+(?=[A-Za-zµμ])", "/", s)
    # ESR: "mm/1st hr", "mm at 1 hr", "mm in 1st hour" -> mm/hr
    s = re.sub(r"(?i)\bmm\s*(?:/|at|in|after)\s*(?:the\s+)?(?:1\s*(?:st)?|first|one)\s*(?:hr|hrs|hour|h)\b", "mm/hr", s)
    # Quest style "> OR = 40" / "< OR = 5"
    s = re.sub(r"(?i)([<>])\s*or\s*=", r"\g<1>=", s)
    s = re.sub(r"(?<=[A-Za-z]):(?=[<>=]|\d)", ": ", s)
    s = re.sub(r"(↑|↓)", r" \g<1> ", s)
    return re.sub(r"[ ]+", " ", s).strip()


def _split_glued(tok: str) -> List[str]:
    """'6.8mmol/L' -> ['6.8', 'mmol/L']; '9.2*' -> ['9.2', '*']; '85%' -> ['85', '%'].
    Only splits when the remainder is a known unit or flag, so 'HbA1c' / 'B12' stay whole."""
    m = re.match(rf"^(?P<q><=|>=|<|>)?(?P<n>[+-]?(?:{_NUM}))(?P<rest>.+)$", tok)
    if m and not re.match(r"^[-/\d.,]", m.group("rest")):
        rest = m.group("rest")
        if rest in ("*", "**", "H", "L", "HH", "LL", "↑", "↓") or rest.lower() in ("high", "low") or is_unit_token(rest):
            return [(m.group("q") or "") + m.group("n"), rest]
    m = re.match(rf"^(?P<r>[+-]?{_NUM}-{_NUM})(?P<rest>[A-Za-zµμ%/].*)$", tok)
    if m and is_unit_token(m.group("rest")):
        return [m.group("r"), m.group("rest")]
    return [tok]


def _tokens(line: str) -> List[str]:
    out: List[str] = []
    for tok in line.split(" "):
        if tok:
            out.extend(_split_glued(tok))
    return out


def normalize_flag(words: List[str]) -> Optional[str]:
    """Printed flag words -> 'H' | 'L' | 'HH' | 'LL' | 'ABN' | None."""
    direction, critical, abnormal = None, False, False
    for w in words:
        wl = w.lower()
        mapped = _FLAG_WORDS.get(wl) or _FLAG_WORDS.get(w)
        if wl.startswith("critical"):
            critical = True
            if wl.endswith("high"):
                direction = "H"
            elif wl.endswith("low"):
                direction = "L"
            continue
        if mapped in ("H", "L"):
            direction = mapped
        elif mapped in ("HH", "LL"):
            direction, critical = mapped[0], True
        elif mapped == "CRIT":
            critical = True
        elif mapped == "ABN":
            abnormal = True
    if direction and critical:
        return direction * 2
    if direction:
        return direction
    if critical or abnormal:
        return "ABN"
    return None


def _clean_name(tokens: List[str]) -> str:
    name = " ".join(tokens).strip()
    name = re.sub(r"^[\s:;,\-|.*#•]+|[\s:;,\-|]+$", "", name).strip()
    return name


def _word_set(tokens: List[str]) -> set:
    """Lower-case alphabetic words of `tokens`, ignoring parentheticals ("Glucose (Method: GOD-POD)")."""
    text = re.sub(r"\([^)]*\)?", " ", " ".join(tokens))
    return set(re.findall(r"[a-z]+", text.lower()))


def _is_demographic(tokens: List[str]) -> bool:
    """True when the name part reads like a header/demographic line. A
    parenthetical ("Glucose (Method: GOD-POD)") is not part of the test name."""
    return bool(_word_set(tokens) & DEMOGRAPHIC)


def _known_name(tokens: List[str]) -> bool:
    """Does the name (ignoring weak administrative words) denote a known analyte?"""
    words = _word_set(tokens)
    if words & STRONG_DEMOGRAPHIC:
        return False
    from .analytes import match_analyte  # local import: analytes imports nothing from here

    kept = [t for t in tokens if re.sub(r"[^a-z]", "", t.lower()) not in WEAK_DEMOGRAPHIC]
    return bool(kept) and match_analyte(" ".join(kept)) is not None


def _strip_method(tokens: List[str]) -> List[str]:
    """"eGFR by CKD-EPI", "Hemoglobin (Hb) Method: Cyanmethemoglobin": the method phrase is not part of the name."""
    for i in range(1, len(tokens)):
        if re.sub(r"[^a-z]", "", tokens[i].lower()) in ("by", "using", "via") or tokens[i].lower().rstrip(":") == "method":
            return tokens[:i]
    return tokens


def _strip_serial(tokens: List[str]) -> List[str]:
    """"1. Hemoglobin", "12) Potassium", "3 Platelet Count": a leading serial number is not part of the name
    (a bare number is only dropped when the rest is a known analyte and the whole is not: "25 OH Vitamin D")."""
    if len(tokens) < 2 or not re.search(r"[A-Za-z]", tokens[1]):
        return tokens
    first = tokens[0]
    if re.fullmatch(r"\d{1,3}[.):]|\(\d{1,3}\)|\d{1,3}\.\)", first):
        return tokens[1:]
    if re.fullmatch(r"\d{1,3}", first):
        from .analytes import match_analyte  # local import: analytes imports nothing from here

        rest = tokens[1:]
        if match_analyte(" ".join(rest)) is not None and match_analyte(" ".join(tokens)) is None:
            return rest
    return tokens


def _is_prose(tokens: List[str]) -> bool:
    words = [re.sub(r"[^a-z]", "", t.lower()) for t in tokens]
    words = [w for w in words if w]
    if not words:
        return False
    if words[0] in PROSE_START:
        return True
    return any(w in PROSE_WORDS for w in words)


# ── ranges ───────────────────────────────────────────────────────────────────

def _match_range(text: str, pos: int, decimal_comma: bool = False):
    """Try to read a reference range at text[pos:]. Returns
    (lo, hi, strict_lo, strict_hi, end) or None."""
    if _DATEISH.match(text, pos):
        return None
    m = _TWO_SIDED.match(text, pos)
    if m:
        lo = parse_number(m.group("lo"), decimal_comma)
        hi = parse_number(m.group("hi") or m.group("hi2"), decimal_comma)
        if lo is None or hi is None or lo > hi:
            return None
        return lo, hi, False, False, m.end()
    m = _ONE_SIDED_HIGH.match(text, pos)
    if m:
        hi = parse_number(m.group("n"), decimal_comma)
        if hi is None:
            return None
        strict = m.group("op") == "<" or bool(m.group("w") and m.group("w").lower().startswith(("less", "below")))
        return None, hi, False, strict, m.end()
    m = _ONE_SIDED_LOW.match(text, pos)
    if m:
        lo = parse_number(m.group("n"), decimal_comma)
        if lo is None:
            return None
        strict = m.group("op") == ">" or bool(m.group("w") and m.group("w").lower().startswith(("more", "above", "greater")))
        return lo, None, strict, False, m.end()
    return None


def parse_range_text(text: str, decimal_comma: bool = False):
    """Parse a stand-alone reference-range cell/text.
    Returns dict(lo, hi, strict_lo, strict_hi, unit, sex_ranges) or None."""
    t = normalize_line(text or "")
    t = re.sub(r"[()\[\]]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if not t:
        return None
    t = _LABEL.sub("", t, count=1).strip() if _LABEL.match(t) else t
    sex_ranges: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
    pos = 0
    while True:
        sm = _SEX_LABEL.match(t, pos)
        if not sm:
            break
        r = _match_range(t, sm.end(), decimal_comma)
        if r is None:
            break
        sex = ((sm.group("s") or sm.group("s2") or "")[0]).upper()
        sex_ranges[sex] = (r[0], r[1])
        pos = r[4]
        while pos < len(t) and t[pos] in " ,;|":
            pos += 1
    if sex_ranges:
        return {"lo": None, "hi": None, "strict_lo": False, "strict_hi": False, "unit": None, "sex_ranges": sex_ranges}
    r = _match_range(t, 0, decimal_comma)
    if r is None:
        return None
    rest = t[r[4]:].strip()
    unit = None
    if rest:
        first = rest.split(" ")[0]
        if is_unit_token(first):
            unit = first
    return {"lo": r[0], "hi": r[1], "strict_lo": r[2], "strict_hi": r[3], "unit": unit, "sex_ranges": None}


def classify_label(label: str) -> Tuple[str, Optional[str], Optional[float], Optional[float]]:
    """(kind, sex, age_lo, age_hi) for the words printed in front of / after a range.
    kind: "plain" (no label or neutral / normal-like), "sex", "age" or "category"."""
    words = re.findall(r"[a-z]+", (label or "").lower())
    if not words:
        return "plain", None, None, None
    wset = set(words)
    if wset & (_SEX_WORDS_M | _SEX_WORDS_F):
        sex = "F" if wset & _SEX_WORDS_F else "M"
        return "sex", sex, None, None
    for w in words:
        if w in _AGE_WORDS:
            lo, hi = _AGE_WORDS[w]
            return "age", None, lo, hi
    if wset <= NEUTRAL_LABEL_WORDS:
        return "plain", None, None, None
    return "category", None, None, None


def _age_years(n: float, unit: str) -> float:
    u = unit.lower()
    if u.startswith("m"):
        return n / 12.0
    if u.startswith("d"):
        return n / 365.0
    if u.startswith("w"):
        return n / 52.0
    return n


def finalize_ranges(row: ParsedRow) -> ParsedRow:
    """Derive the row's usable printed range from its labelled range options (idempotent)."""
    opts = row.range_options
    if not opts:
        return row
    plain = next((o for o in opts if o.kind == "plain"), None)
    sexes = [o for o in opts if o.kind == "sex"]
    ages = [o for o in opts if o.kind == "age"]
    row.ref_low = row.ref_high = None
    row.ref_strict_low = row.ref_strict_high = False
    row.ref_text = None
    if plain is not None:
        row.ref_low, row.ref_high = plain.lo, plain.hi
        row.ref_strict_low, row.ref_strict_high = plain.strict_lo, plain.strict_hi
        row.ref_text = plain.text
    row.sex_ranges = {o.sex: (o.lo, o.hi) for o in sexes} or None
    row.age_ranges = [(o.age_lo if o.age_lo is not None else 0.0, o.age_hi if o.age_hi is not None else 200.0,
                       o.lo, o.hi, o.strict_lo, o.strict_hi) for o in ages] or None
    row.interpretive_only = plain is None and not sexes and not ages
    return row


# ── qualitative results ──────────────────────────────────────────────────────

def _find_qualitative(tokens: List[str], start: int) -> Optional[Tuple[int, int, str]]:
    """Index span (i, j) and canonical text of the first qualitative result token(s) at/after `start`."""
    for i in range(start, len(tokens)):
        for width in (2, 1):
            if i + width > len(tokens):
                continue
            cand = " ".join(tokens[i:i + width]).lower().strip(":,;")
            if cand in _QUAL_WORDS:
                return i, i + width, " ".join(tokens[i:i + width]).strip(":,;")
    return None


def qualitative_class(text: str) -> Optional[str]:
    t = (text or "").strip().lower().replace("_", " ")
    t = re.sub(r"\s+", " ", t)
    if t in _QUAL_NEGATIVE or t.replace("-", " ") in _QUAL_NEGATIVE:
        return "NEGATIVE"
    if t in _QUAL_POSITIVE:
        return "POSITIVE"
    return None


def _is_big_int(tok: str) -> bool:
    return bool(re.fullmatch(r"\d{8,}", tok))


# ── column headers (cumulative "Previous / Current" layouts) ─────────────────

_PREV_WORDS = {"previous", "prev", "prior", "last", "old", "earlier", "before", "baseline"}
_CUR_WORDS = {"current", "latest", "today", "recent", "new", "present"}
_RESULT_WORDS = {"result", "results", "value", "values", "observed"}
_HEADER_VOCAB = (_PREV_WORDS | (_CUR_WORDS - {"present", "new"}) | _RESULT_WORDS | {
    "test", "tests", "name", "parameter", "parameters", "investigation", "investigations", "analyte", "analytes",
    "unit", "units", "reference", "ref", "range", "ranges", "interval", "bio", "biological", "biol", "flag", "flags",
    "remark", "remarks", "status", "method", "date", "dates", "on", "and", "of", "sr", "no", "min", "max", "limits",
    "normal", "low", "high", "critical",
    # Column titles seen on cumulative reports ("Last Visit | Today", "Follow-up").
    "visit", "visits", "reading", "readings", "report", "reports", "sample", "run", "follow", "up", "followup",
    "day", "days", "week", "weeks", "month", "months", "ago", "admission", "discharge"})
_DATE_TOKEN = re.compile(r"^(\d{1,4})[/.-](\d{1,2})[/.-](\d{2,4})$")
_TIME_TOKEN = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?(?:[ap]m)?$", re.I)
_HISTORY_WORDS = _PREV_WORDS | {"delta", "change", "trend"}
_MON = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"))}


def _date_readings(tok: str) -> Optional[List[date]]:
    """All calendar readings of a printed date (dd/mm and mm/dd both, when ambiguous)."""
    m = _DATE_TOKEN.match(tok)
    if m:
        a, b, c = int(m.group(1)), int(m.group(2)), int(m.group(3))
        out: List[date] = []
        if len(m.group(1)) == 4:
            combos = [(a, b, c)]                       # yyyy-mm-dd
        else:
            c = c + 2000 if c < 100 else c
            combos = [(c, b, a), (c, a, b)]            # dd/mm/yyyy, mm/dd/yyyy
        for y, mo, d in combos:
            try:
                out.append(date(y, mo, d))
            except ValueError:
                continue
        return out or None
    m = re.match(r"^(\d{1,2})[- ]?([A-Za-z]{3})[a-z]*[- ,]*(\d{2,4})$", tok)
    if m and m.group(2).lower() in _MON:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return [date(y, _MON[m.group(2).lower()], int(m.group(1)))]
        except ValueError:
            return None
    return None


def _header_roles(tokens: List[str]) -> Optional[List[object]]:
    """Result-column roles of a header ('prev' | 'cur' | list-of-dates), in column order; None if not a header."""
    cols: List[object] = []
    i = 0
    n = len(tokens)
    while i < n:
        t = tokens[i].lower().strip(":,|")
        if t in _PREV_WORDS:
            cols.append("prev")
            if i + 1 < n and tokens[i + 1].lower().strip(":,|") in _RESULT_WORDS:
                i += 1
        elif t in _CUR_WORDS - {"present", "new"}:
            cols.append("cur")
            if i + 1 < n and tokens[i + 1].lower().strip(":,|") in _RESULT_WORDS:
                i += 1
        elif t in _RESULT_WORDS:
            cols.append("cur")
        else:
            d = _date_readings(tokens[i].strip(":,|"))
            if d:
                cols.append(d)
        i += 1
    return cols or None


def _is_header_line(line: str) -> bool:
    toks = _tokens(normalize_line(line))
    if not toks:
        return False
    for t in toks:
        tl = t.lower().strip(":,|().")  # "Bio. Ref." -> bio, ref
        if not tl or tl in ("|", "-", "/", "&"):
            continue
        if tl in _HEADER_VOCAB or _date_readings(t.strip(":,|()")):
            continue
        return False
    return True


def _pick_current(roles: Optional[List[object]], n: int) -> Optional[int]:
    """Index (0-based) of the CURRENT-result column among n bare numbers, or None when it cannot be told."""
    if not roles or len(roles) != n:
        return None
    if all(isinstance(r, list) for r in roles):
        picks = set()
        for variant in range(min(len(r) for r in roles)):
            try:
                ds = [r[min(variant, len(r) - 1)] for r in roles]
            except IndexError:
                return None
            if len(set(ds)) != len(ds):
                return None
            picks.add(ds.index(max(ds)))
        return picks.pop() if len(picks) == 1 else None
    if any(isinstance(r, list) for r in roles):
        return None
    curs = [i for i, r in enumerate(roles) if r == "cur"]
    if len(curs) == 1 and all(r in ("cur", "prev") for r in roles):
        return curs[0]
    return None


# ── the reader ───────────────────────────────────────────────────────────────

@dataclass
class _Ctx:
    roles: Optional[List[object]] = None   # result-column roles from the last header seen
    section: str = ""                      # last section heading ("URINE ROUTINE")
    decimal_comma: bool = False
    source: str = "text"


@dataclass
class _Line:
    rows: List[ParsedRow] = field(default_factory=list)
    options: List[RangeOption] = field(default_factory=list)   # a pure interpretation line: label + range(s)
    kind: str = "none"                                         # rows | interp | prose | dropped | none
    unread: int = 0                                            # result-like parts that could not be read


def _spacify(text: str) -> str:
    """Brackets -> spaces, SAME length (positions stay valid for the original text)."""
    return re.sub(r"[()\[\]]", " ", text)


def _numeric_tok(tok: str) -> bool:
    return bool(re.fullmatch(rf"[+-]?{_NUM}", tok.strip("()[],;")))


def _atom_at(tokens: List[str], i: int):
    """(end, qualifier, number_string) when a value atom starts at tokens[i]."""
    tok = tokens[i]
    if tok in ("<", ">", "<=", ">=") and i + 1 < len(tokens):
        m = _VALUE_RE.match(tokens[i + 1])
        if m and not m.group("q"):
            return i + 2, tok, m.group("n")
        return None
    m = _VALUE_RE.match(tok)
    if m:
        q = m.group("q")
        return i + 1, (q if q in ("<", ">", "<=", ">=") else None), m.group("n")
    return None


def _range_token_end(tokens: List[str], i: int) -> Optional[int]:
    """End index when a two-sided range ("13.0-17.0", "13.0 - 17.0", "13.0 to 17.0") starts at tokens[i]."""
    t = tokens[i].strip("()[],;")
    if _is_range_token(t) and not _DATEISH.match(t):
        return i + 1
    n = len(tokens)
    if _numeric_tok(t) and i + 2 < n and tokens[i + 1].lower() in ("-", "~", "to") and _numeric_tok(tokens[i + 2]):
        if not tokens[i + 2].startswith(("+", "-")) or tokens[i + 1].lower() == "to":
            return i + 3
    if re.fullmatch(rf"[+-]?{_NUM}-", t) and i + 1 < n and _numeric_tok(tokens[i + 1]):
        return i + 2
    return None


def _is_flag_tok(tok: str) -> bool:
    t = tok.strip("()[],;")
    return t in ("H", "L", "HH", "LL", "N", "*", "**", "↑", "↓") or t.lower() in (
        "high", "low", "abnormal", "abn", "critical", "normal", "wnl")


def _range_start(tokens: List[str], j: int) -> bool:
    """Does a printed range (or an age-band label such as "0-14 yrs:") start at tokens[j]?"""
    if j >= len(tokens):
        return False
    text = _spacify(" ".join(tokens[j:j + 6])).strip()
    if _match_range(text, 0) is not None:
        return True
    am = _AGE_LABEL.match(text)
    return bool(am and am.group("a") is not None)


def _follow_class(tokens: List[str], j: int) -> str:
    """What stands directly after a number: unit | flag | range | end | qual | word."""
    if j >= len(tokens):
        return "end"
    t = tokens[j]
    ts = t.strip("()[],;")
    if _is_flag_tok(t):
        return "flag"
    if ts and is_unit_token(ts) and not _numeric_tok(ts):
        return "unit"
    atom = _atom_at(tokens, j)
    if atom is not None and atom[1] is not None:       # "<5.7", ">= 40"
        return "range"
    if _range_start(tokens, j) or _range_token_end(tokens, j) is not None:
        return "range"
    hit = _find_qualitative(tokens[j:j + 2], 0)
    if hit is not None and hit[0] == 0:
        return "qual"
    return "word"


@dataclass
class _Found:
    start: int                       # index of the first value token (name = tokens[:start])
    after: int                       # index after the value token(s)
    atoms: List[Tuple[Optional[str], str]]   # (qualifier, number string) per bare number in the run
    result_range: Optional[Tuple[float, float]] = None


def _find_value(tokens: List[str], decimal_comma: bool, min_start: int = 0) -> Optional[_Found]:
    n = len(tokens)
    runs: List[Tuple[int, int, List[Tuple[Optional[str], str]], str, bool]] = []
    # numbers inside a parenthetical that is part of the NAME ("Prothrombin Time (Control 12 sec) 18 sec 11-13.5")
    # are only a fallback: the result is the number outside the brackets when there is one
    inside: List[bool] = []
    depth_p = 0
    for tk in tokens:
        opens, closes = tk.count("("), tk.count(")")
        inside.append(depth_p > 0 or opens > closes)
        depth_p = max(0, depth_p + opens - closes)
    name_has_letter = False
    i = 0
    if min_start > 0:
        # the first table cell (set off by 3+ spaces) is the NAME, digits and all: "CA 15.3   52   U/mL"
        name_has_letter = True
        i = min(min_start, n)
    while i < n:
        rt_end = _range_token_end(tokens, i) if name_has_letter else None
        if rt_end is not None:
            # a result printed as a range: only meaningful for microscopy counts ("2-3 /hpf")
            if rt_end < n and _HPF_UNIT.match(tokens[rt_end]):
                rm = re.match(rf"^\s*({_NUM})\s*(?:-|~|to)\s*({_NUM})\s*$", " ".join(tokens[i:rt_end]).strip("()[],;"))
                lo_hi = [parse_number(rm.group(1), decimal_comma), parse_number(rm.group(2), decimal_comma)] if rm else []
                if len(lo_hi) == 2 and None not in lo_hi:
                    return _Found(i, rt_end, [(None, fmt_num(max(lo_hi)))], result_range=(min(lo_hi), max(lo_hi)))
            i = rt_end
            continue
        atom = _atom_at(tokens, i) if name_has_letter else None
        if atom is not None:
            end, q, num = atom
            atoms = [(q, num)]
            if q is None:
                while end < n and _range_token_end(tokens, end) is None:
                    nxt = _atom_at(tokens, end)
                    if nxt is None or nxt[1] is not None:
                        break
                    atoms.append((None, nxt[2]))
                    end = nxt[0]
            start = i
            if len(atoms) > 1:
                from .analytes import match_analyte  # local import: analytes imports nothing from here

                for k in range(1, len(atoms)):
                    # "Vitamin B 12 500 pg/mL": the leading number(s) complete a known test name
                    if match_analyte(" ".join(tokens[:i + k])) is not None and match_analyte(" ".join(tokens[:i])) is None:
                        start, atoms = i + k, atoms[k:]
                        break
            runs.append((start, end, atoms, _follow_class(tokens, end), inside[i]))
            i = end
            continue
        if re.search(r"[A-Za-z]", tokens[i]):
            name_has_letter = True
        i += 1
    runs.sort(key=lambda r: r[4])                                # stable: numbers outside brackets first
    for start, end, atoms, follow, _inb in runs:                 # phase 1: number followed by unit / flag / range / end
        if follow in ("unit", "flag", "range", "end"):
            return _Found(start, end, atoms)
    for start, end, atoms, follow, _inb in runs:                 # phase 2: value followed by label words ("Children: 11-14")
        if follow == "word":
            k = end
            while k < n and _follow_class(tokens, k) == "word":
                k += 1
            if k >= n or _follow_class(tokens, k) == "range":
                return _Found(start, end, atoms)
    return None


# ---- tail: flags, unit, labelled ranges, further tests --------------------------------

@dataclass
class _Tail:
    unit: Optional[str] = None
    ref_unit: Optional[str] = None
    options: List[RangeOption] = field(default_factory=list)
    flag_words: List[str] = field(default_factory=list)
    leftovers: List[str] = field(default_factory=list)
    extra: List[ParsedRow] = field(default_factory=list)
    unread: int = 0


def _next_token(t: str, pos: int) -> Tuple[str, int]:
    end = t.find(" ", pos)
    end = len(t) if end < 0 else end
    return t[pos:end], end


def _skip_sep(t: str, pos: int) -> int:
    while pos < len(t) and t[pos] in " ,;|:":
        pos += 1
    return pos


def _parse_tail(tail: str, ctx: _Ctx, depth: int, allow_rows: bool = True) -> _Tail:
    t = _spacify(tail)
    out = _Tail()
    pending: List[str] = []
    pending_age: Optional[Tuple[float, float]] = None
    last_opt: Optional[RangeOption] = None
    pos = 0
    dc = ctx.decimal_comma

    def add_option(r, text_start: int) -> None:
        nonlocal pending, pending_age, last_opt
        label = " ".join(pending).strip(" :")
        kind, sex, a_lo, a_hi = classify_label(label)
        if pending_age is not None:
            kind, sex, a_lo, a_hi = "age", None, pending_age[0], pending_age[1]
            label = (label + " age band").strip()
        opt = RangeOption(kind=kind, label=label, lo=r[0], hi=r[1], strict_lo=r[2], strict_hi=r[3],
                          text=t[text_start:r[4]].strip(), sex=sex, age_lo=a_lo, age_hi=a_hi)
        out.options.append(opt)
        last_opt = opt
        pending, pending_age = [], None

    while True:
        pos = _skip_sep(t, pos)
        if pos >= len(t):
            break
        # 1. an age band used as a label ("0-14 yrs:", ">14 yrs:", "Age 18-60 y")
        am = _AGE_LABEL.match(t, pos)
        if am and (am.group("a") is not None):
            a = parse_number(am.group("a"), dc)
            b = parse_number(am.group("b"), dc) if am.group("b") else None
            if a is not None:
                unit_w = am.group("u")
                if am.group("op") and am.group("op").startswith(">"):
                    pending_age = (_age_years(a, unit_w), 200.0)
                elif am.group("op"):
                    pending_age = (0.0, _age_years(a, unit_w))
                elif b is not None:
                    pending_age = (_age_years(a, unit_w), _age_years(b, unit_w))
                else:
                    pending_age = (_age_years(a, unit_w), _age_years(a, unit_w))
                pos = am.end()
                continue
        # 2. a range (with whatever label words were collected in front of it)
        r = _match_range(t, pos, dc)
        if r is not None:
            add_option(r, pos)
            pos = r[4]
            continue
        # 3. flags ("H", "Low", "Critical High", "Normal"); a flag WORD directly in front of a range is a label
        fm = _FLAG_TOKEN.match(t, pos)
        if fm:
            word = fm.group(0)
            nxt = _skip_sep(t, fm.end())
            before_range = _match_range(t, nxt, dc) is not None or (
                _AGE_LABEL.match(t, nxt) is not None and re.match(r"[<>]?=?\s*\d", t[nxt:nxt + 4]) is not None)
            if word.lower() in _NORMAL_WORDS:
                if before_range:
                    pending.append(word)
                pos = fm.end()
                continue
            if before_range and word.isalpha() and len(word) > 2 and (out.options or pending):
                pending.append(word)
                pos = fm.end()
                continue
            out.flag_words.append(word)
            pos = fm.end()
            continue
        word, end = _next_token(t, pos)
        stripped = word.strip(".,;")
        if stripped and is_unit_token(stripped) and not _NUMERICISH_RE.match(stripped):
            if out.unit is None:
                out.unit = stripped
            elif out.ref_unit is None and out.options:
                out.ref_unit = stripped
            pos = end
            continue
        if word in ("/", "&", "-", "~"):
            pos = end
            continue
        # 4. a group of words: label of the next range, annotation of the last range, or a further test
        if re.match(r"[A-Za-z]", word) or (word[:1] in "*#" and len(word) > 1):
            k = pos
            group: List[str] = []
            while True:
                k = _skip_sep(t, k)
                if k >= len(t):
                    break
                w, e = _next_token(t, k)
                ws = w.strip(".,;")
                if not (re.match(r"[A-Za-z*#]", w) or re.fullmatch(r"\d+(?:st|nd|rd|th):?", w)) \
                        or (ws and is_unit_token(ws)) or w in ("H", "L", "HH", "LL"):
                    break
                group.append(w)
                k = e
            if not group:
                out.leftovers.append(word)
                pos = end
                continue
            if group[0].strip(":.,;").lower() in _HISTORY_WORDS and len(group) <= 2:
                # annotation of an EARLIER result ("Previous: 4.2", "Prev 4.2 on 05/01/2026"): not a result of
                # this report, and never the range or a further test
                k2 = k
                while k2 < len(t):
                    k2 = _skip_sep(t, k2)
                    w2, e2 = _next_token(t, k2)
                    w2s = w2.strip(".,;()")
                    if w2s and (_numeric_tok(w2s) or _DATE_TOKEN.match(w2s) or _TIME_TOKEN.match(w2s)
                                or w2s.lower() in ("on", "at", "dated", "-", "@") or (is_unit_token(w2s) and not _RANGE_TOKEN.match(w2s))):
                        k2 = e2
                        continue
                    break
                pos = k2
                continue
            nxt = _skip_sep(t, k)
            label_follows = nxt < len(t) and (
                _match_range(t, nxt, dc) is not None
                or (_AGE_LABEL.match(t, nxt) is not None and re.match(r"[<>]?=?\s*\d", t[nxt:nxt + 4]) is not None))
            if label_follows:
                pending.extend(group)
                pos = k
                continue
            if allow_rows and depth < 4:
                sub = _parse_internal(tail[pos:], ctx, depth + 1)
                if sub.rows:
                    out.extra.extend(sub.rows)
                    out.unread += sub.unread
                    pos = len(t)
                    break
                out.unread += sub.unread
            # trailing annotation of the previous range: "0.5 - 1.5 (adult)"
            label_text = " ".join(group)
            if last_opt is not None and last_opt.kind == "plain" and not last_opt.label and len(group) <= 4 \
                    and set(re.findall(r"[a-z]+", label_text.lower())) <= (CATEGORY_VOCAB | NEUTRAL_LABEL_WORDS):
                kind, sex, a_lo, a_hi = classify_label(label_text)
                if kind in ("sex", "age"):
                    last_opt.kind, last_opt.sex, last_opt.age_lo, last_opt.age_hi = kind, sex, a_lo, a_hi
                    last_opt.label = label_text
            pending = []
            out.leftovers.extend(group)
            pos = k
            continue
        out.leftovers.append(word)
        pos = end
    return out


def _tail_is_interpretive(norm: str, tokens: List[str], ctx: _Ctx) -> Optional[List[RangeOption]]:
    """A pure category line ("Deficient : < 20 ng/mL", "Normal <5.7 / Prediabetes 5.7 - 6.4", "Kidney failure: <15"):
    only vocabulary words, units and ranges. Returns its range options, else None."""
    words = []
    for tok in tokens:
        ts = tok.strip("()[],;:.")
        if not ts or ts in ("/", "&", "-", "~", "<", ">", "<=", ">="):
            continue
        if _numeric_tok(ts) or _RANGE_TOKEN.match(ts) or re.fullmatch(r"[<>]=?[+-]?[\d.,]+", ts):
            continue
        if is_unit_token(ts) or tok in ("H", "L", "HH", "LL", "*", "**", "↑", "↓"):
            continue
        if re.fullmatch(r"\d+(?:st|nd|rd|th)", ts.lower()):
            continue
        w = re.sub(r"[^a-z0-9]", "", ts.lower())
        if w in ("percent",):
            continue
        words.append(w)
    if not words or not all(w in CATEGORY_VOCAB for w in words):
        return None
    tail = _parse_tail(norm, ctx, 0, allow_rows=False)
    if not tail.options or any(re.search(r"\d", x) for x in tail.leftovers):
        return None
    return tail.options


def _result_like(norm: str, tokens: List[str]) -> bool:
    """A line that could not be read but reads like a RESULT: a qualitative word after a name, or a number after
    a name together with a unit / range / flag, or a known analyte, or a bare number on a line with no header,
    prose or administrative words. Headings ("Page 1 of 2"), prose and category lists are not results."""
    low = norm.lower()
    if any(w in low for w in ("@", "www.", "http")) or len(tokens) > 14:
        return False
    if _is_prose(tokens):
        return False
    words = _word_set(tokens)
    if words & STRONG_DEMOGRAPHIC or words & HEADER_LIKE:
        return False
    first_alpha = next((i for i, t in enumerate(tokens) if re.search(r"[A-Za-z]", t)), None)
    if first_alpha is None:
        return False
    if _find_qualitative(tokens, first_alpha + 1) is not None:
        return True
    num_idx = None
    name_toks: List[str] = []
    for i, t in enumerate(tokens):
        ts = t.strip("()[],;:")
        if i > first_alpha and (_NUMERICISH_RE.match(ts) or _VALUE_RE.match(ts) or _RANGE_TOKEN.match(ts)
                                or re.match(r"^[<>=~+-]*\d", ts)):
            if _is_big_int(ts):
                return False
            num_idx = i
            break
        if re.search(r"[A-Za-z]", t):
            name_toks.append(t)
    if num_idx is None:
        return False
    rest = tokens[num_idx:]
    if any(is_unit_token(t.strip("()[],;")) and not _numeric_tok(t) for t in rest) or _range_start(tokens, num_idx) \
            or any(_is_flag_tok(t) for t in rest):
        return True
    if _known_name(name_toks):
        return True
    return not (words & DEMOGRAPHIC)


def _looks_like_result_line(line: str) -> bool:
    """A line that reads like `name ... number` but could not be parsed."""
    norm = normalize_line(line)
    return _result_like(norm, _tokens(norm))


def _parse_qualitative(norm: str, tokens: List[str], line: str, ctx: _Ctx) -> Optional[ParsedRow]:
    hit = _find_qualitative(tokens, 1)
    if hit is None:
        return None
    i, j, text = hit
    name_tokens = _strip_method(tokens[:i])
    name = _clean_name(name_tokens)
    if not name or not re.search(r"[A-Za-z]", name) or len(name_tokens) > 7 or _word_set(name_tokens) & STRONG_DEMOGRAPHIC:
        return None
    tail = " ".join(tokens[j:]).strip(" :;,()[]")
    return ParsedRow(name=name, value=None, text_value=text, unit=None, ref_text=tail or None, source=ctx.source,
                     line=line, section=ctx.section)


def _split_known_pair(tokens: List[str], start: int, ctx: _Ctx, depth: int) -> Optional[_Line]:
    """"Glucose Fasting 92 Glucose PP 410", "Hb 6.1 K 6.9": a known analyte name, a bare number, then ANOTHER
    known analyte name and its value: two tests, the first number is NOT part of the second test's name."""
    from .analytes import match_analyte  # local import: analytes imports nothing from here

    for k in range(1, start):
        if not re.fullmatch(rf"[+-]?{_NUM}", tokens[k]) or k + 1 >= len(tokens) or not re.match(r"[A-Za-z]", tokens[k + 1]):
            continue
        if match_analyte(" ".join(tokens[:k])) is None:
            continue
        sub = _parse_internal(" ".join(tokens[k + 1:]), ctx, depth + 1)
        if not sub.rows or match_analyte(sub.rows[0].name) is None:
            continue
        first = _parse_internal(" ".join(tokens[:k + 1]), ctx, depth + 1)
        if not first.rows:
            continue
        return _Line(rows=first.rows + sub.rows, kind="rows", unread=first.unread + sub.unread)
    return None


def _parse_internal(line: str, ctx: _Ctx, depth: int = 0) -> _Line:
    res = _parse_internal_inner(line, ctx, depth)
    if not res.rows and res.unread == 0 and res.kind in ("dropped", "none") and depth == 0:
        norm = normalize_line(line)
        if " " not in norm and norm.count(",") + norm.count(";") >= 2 and re.search(r"[A-Za-z]", norm) and re.search(r"\d", norm):
            res.unread = 1                      # a delimited row that could not be read is never silently dropped
    return res


def _parse_internal_inner(line: str, ctx: _Ctx, depth: int = 0) -> _Line:
    norm = normalize_line(line)
    if not norm or not re.search(r"[A-Za-z]", norm):
        return _Line()
    tokens = _tokens(norm)
    if len(tokens) == 1 and re.search(r"[A-Za-z]", norm) and re.search(r"\d", norm) and norm.count(",") + norm.count(";") >= 2:
        # a comma / semicolon separated row ("Hemoglobin,6.1,g/dL,13.0-17.0") is a row, not one big word - but only
        # in the name,value,... order: with another column second (name,unit,value) the columns cannot be told
        # apart, so the line stays unread (counted by the wrapper) instead of being guessed
        cells = [c for c in re.split(r"[;,]+", norm) if c]
        if len(cells) >= 2 and re.match(r"[<>=~]*[-+]?\d", cells[1].strip()):
            norm = re.sub(r"[;,]+", " ", norm).strip()
            tokens = _tokens(norm)
    if len(tokens) < 2:
        return _Line()
    if _is_prose(tokens):
        return _Line(kind="prose")
    interp = _tail_is_interpretive(norm, tokens, ctx)
    if interp is not None:
        return _Line(kind="interp", options=interp)
    dc = ctx.decimal_comma

    min_start = 0
    if depth == 0:
        cells = [c for c in re.split(r"\s{3,}|\t+", line.strip()) if c]
        if len(cells) >= 3:
            c0 = _tokens(normalize_line(cells[0]))
            c1 = _tokens(normalize_line(cells[1]))
            if len(c0) >= 2 and re.search(r"[A-Za-z]", c0[0]) and _numeric_tok(c0[-1]) and c1 and _atom_at(c1, 0) is not None \
                    and tokens[:len(c0)] == c0:
                min_start = len(c0)
    found = _find_value(tokens, dc, min_start)
    if found is None:
        row = _parse_qualitative(norm, tokens, line, ctx)
        if row is not None:
            return _Line(rows=[row], kind="rows")
        return _Line(kind="dropped", unread=1 if _result_like(norm, tokens) else 0)

    def dropped(rows: Optional[List[ParsedRow]] = None, unread: int = 0) -> _Line:
        if rows:
            return _Line(rows=rows, kind="rows", unread=unread)
        return _Line(kind="dropped", unread=unread)

    # phone / id numbers are never results
    if any(_is_big_int(num.lstrip("+-")) for _q, num in found.atoms):
        return _Line(kind="dropped")
    # "HBsAg by ELISA Reactive   Hemoglobin 14.1 g/dL 13.0-17.0": a qualitative result BEFORE the first number
    # (with other words in between) ends that test; the rest of the line is read again as further tests. The
    # number must never be attached to the qualitative test.
    qh = _find_qualitative(tokens, 1)
    if qh is not None and qh[1] < found.start and depth < 4:
        first = tokens[:qh[1]]
        q_row = _parse_qualitative(" ".join(first), first, line, ctx)
        if q_row is not None:
            sub = _parse_internal(" ".join(tokens[qh[1]:]), ctx, depth + 1)
            return _Line(rows=[q_row] + sub.rows, kind="rows", unread=sub.unread)
    if depth < 4:
        pair = _split_known_pair(tokens, found.start, ctx, depth)
        if pair is not None:
            return pair
    name_tokens = tokens[:found.start]
    # "Date: 12/03/2026 Hemoglobin 6.1": a date / time in front of the name is not part of the name
    cut = max((i for i, tk in enumerate(name_tokens)
               if _DATE_TOKEN.match(tk.strip("()[],;:")) or _TIME_TOKEN.match(tk.strip("()[],;:"))), default=-1)
    if 0 <= cut and cut + 1 < len(name_tokens):
        name_tokens = name_tokens[cut + 1:]
    # qualitative words directly before the number belong to the result, not the name
    text_value = None
    qual_tail = _find_qualitative(name_tokens, max(1, len(name_tokens) - 2))
    if qual_tail and qual_tail[1] == len(name_tokens) and qual_tail[0] >= 1:
        text_value = qual_tail[2]
        name_tokens = name_tokens[:qual_tail[0]]
    name_tokens = _strip_serial(_strip_method(name_tokens))
    # "Hemoglobin (g/dL) 6.1 (13.0-17.0)": a unit in brackets after the name is the result's unit, not name
    bracket_unit: Optional[str] = None
    kept_tokens: List[str] = []
    for tk in name_tokens:
        inner = tk.strip("[]():,;")
        if kept_tokens and bracket_unit is None and re.fullmatch(r"[\[(][^\])]+[\])]:?", tk) and is_unit_token(inner) \
                and not _numeric_tok(inner):
            bracket_unit = inner
            continue
        kept_tokens.append(tk)
    name_tokens = kept_tokens
    # "Hemoglobin L 6.1" (flag cell between name and value): the flag is not part of the name when the name
    # without it is a known analyte ("Factor H" stays whole)
    pre_flag: List[str] = []
    if len(name_tokens) >= 2 and name_tokens[-1] in ("H", "L", "HH", "LL", "↑", "↓"):
        from .analytes import match_analyte  # local import: analytes imports nothing from here

        if match_analyte(" ".join(name_tokens[:-1])) is not None:
            pre_flag = [name_tokens[-1]]
            name_tokens = name_tokens[:-1]
    name = _clean_name(name_tokens)
    if (not name or not re.search(r"[A-Za-z]", name) or len(name_tokens) > 9 or len(name) > 70
            or re.fullmatch(r"[\W\d_]*", name)
            or any(_is_range_token(tk.strip("()[],;")) for tk in name_tokens)):
        return dropped(unread=1 if _result_like(norm, tokens) else 0)

    tail = _parse_tail(" ".join(tokens[found.after:]), ctx, depth)
    extra = tail.extra

    # ---- the result: one number, or several bare numbers (cumulative layout) -----------------------
    qualifier: Optional[str] = None
    value: Optional[float] = None
    candidates: Optional[List[float]] = None
    ambiguity: Optional[str] = None
    note = ""
    nums = [parse_number(num, dc) for _q, num in found.atoms]
    if any(x is None for x in nums):
        return dropped(extra, tail.unread + 1)
    if len(nums) == 1:
        value = nums[0]
        qualifier = found.atoms[0][0]
    else:
        pick = _pick_current(ctx.roles, len(nums))
        if pick is not None:
            value = nums[pick]
            note = f"{len(nums)} result columns on the report; read the current one (column {pick + 1}) from its header"
        else:
            candidates = [float(x) for x in nums]
            ambiguity = (f"{len(nums)} values ({', '.join(fmt_num(x) for x in nums)}) are printed before the unit "
                         "and no column header says which is the current result")

    # ---- administrative lines ------------------------------------------------------------------------
    if _is_demographic(name_tokens) and tail.unit is None and bracket_unit is None and text_value is None and not _known_name(name_tokens):
        return dropped(extra, tail.unread)
    # a row with neither unit nor range must be a known analyte (prevents ids/pages becoming results)
    if tail.unit is None and bracket_unit is None and not tail.options and text_value is None and found.result_range is None:
        from .analytes import match_analyte  # local import: analytes imports nothing from here

        if match_analyte(name) is None:
            return dropped(extra, tail.unread + (1 if _result_like(norm, tokens) else 0))

    warnings: List[str] = []
    if not tail.options:
        for w in tail.leftovers:
            if re.search(r"\d", w) and not re.fullmatch(r"[A-Za-z]+\d*", w.strip(".,;")):
                warnings.append(f"{name}: could not interpret '{w}' (reference range unreadable)")
                break
    unit = tail.unit if tail.unit is not None else bracket_unit
    ref_unit = tail.ref_unit
    if unit is not None and ref_unit is not None and ref_unit.lower() == unit.lower():
        ref_unit = None
    if found.result_range is not None:
        lo_r, hi_r = found.result_range
        note = f"result printed as a range {fmt_num(lo_r)}-{fmt_num(hi_r)}; scored on the upper end"

    row = ParsedRow(
        name=name, value=value, qualifier=qualifier, text_value=text_value, unit=unit,
        printed_flag=normalize_flag(pre_flag + tail.flag_words), source=ctx.source, line=line, ref_unit=ref_unit,
        warnings=warnings, range_options=list(tail.options), candidates=candidates, ambiguity=ambiguity,
        section=ctx.section, result_range=found.result_range, note=note,
    )
    finalize_ranges(row)
    return _Line(rows=[row] + extra, kind="rows", unread=tail.unread)


def parse_line_rows(line: str, decimal_comma: bool = False, source: str = "text",
                    roles: Optional[List[object]] = None) -> List[ParsedRow]:
    """Every test on ONE logical line (a line may hold several: "Sodium 138 mmol/L 135-145  Potassium 6.9 ...")."""
    res = _parse_internal(line, _Ctx(roles=roles, decimal_comma=decimal_comma, source=source))
    return res.rows


def parse_line(line: str, decimal_comma: bool = False, source: str = "text") -> Optional[ParsedRow]:
    """Parse ONE logical line. Returns None for headers, labels, demographic
    lines and anything that does not read as `name value [flag] [unit] [range]`.
    A line holding several tests returns the first; use parse_line_rows() for all."""
    rows = parse_line_rows(line, decimal_comma=decimal_comma, source=source)
    return rows[0] if rows else None


# ── cell-per-line PDF text ────────────────────────────────────────────────────

_TAIL_WORDS = {"to", "up", "upto", "less", "than", "more", "above", "below", "ref", "reference", "range", "normal",
               "interval", "bio", "male", "female", "m", "f", "-", "~", "desirable", "optimal", "max", "min"}


def _is_tail_cell(line: str) -> bool:
    norm = normalize_line(line)
    if not norm:
        return False
    toks = _tokens(norm)
    for tok in toks:
        t = tok.strip("()[],;:")
        if not t:
            continue
        if _NUMERICISH_RE.match(t) or re.fullmatch(rf"[<>=]*{_NUM}(?:-{_NUM})?", t):
            continue
        if t in ("<", ">", "<=", ">=", "-", "~", "*", "**", "↑", "↓", "H", "L", "HH", "LL", "+", "++", "+++"):
            continue
        tl = t.lower().rstrip(".")
        if tl in _TAIL_WORDS or tl in _FLAG_WORDS or tl in _NORMAL_WORDS:
            continue
        if any(tl == q or tl in q.split() for q in _QUAL_WORDS):
            continue
        if is_unit_token(t) and tl not in ("unit", "units"):
            continue
        return False
    return True


def _is_atomic(line: str) -> bool:
    toks = _tokens(normalize_line(line))
    return len(toks) <= 1 or _is_tail_cell(line)


def reassemble_cells_traced(lines: List[str]) -> List[Tuple[str, Tuple[int, ...]]]:
    """``reassemble_cells`` that also reports, for every output line, the indexes (into ``lines``) of the
    physical lines it was built from."""
    out: List[Tuple[str, Tuple[int, ...]]] = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if parse_line(line) is not None or _is_tail_cell(line) or not re.search(r"[A-Za-z]", line):
            out.append((line, (i,)))
            i += 1
            continue
        j = i + 1
        acc = line
        while j < n and j - i <= 9 and _is_tail_cell(lines[j]):
            acc += " " + lines[j]
            j += 1
        if j > i + 1 and parse_line(acc) is not None:
            out.append((acc, tuple(range(i, j))))
            i = j
        else:
            out.append((line, (i,)))
            i += 1
    return out


def reassemble_cells(lines: List[str]) -> List[str]:
    """Join PDF text emitted one table cell per line ("Hemoglobin", "6.5",
    "g/dL", "13.0 - 17.0") back into rows. A name cell absorbs the following
    tail cells only when the joined text actually parses as a row."""
    return [text for text, _ in reassemble_cells_traced(lines)]


_SECTION_WORDS = {"routine", "examination", "profile", "panel", "count", "function", "analysis", "microscopy",
                  "urinalysis", "gas", "culture", "serology", "haematology", "hematology", "biochemistry", "chemistry",
                  "physical", "chemical", "electrolytes", "lipid", "thyroid", "coagulation", "cbc", "lft", "kft", "rft"}


def _is_section_heading(line: str) -> bool:
    norm = normalize_line(line)
    toks = _tokens(norm)
    if not toks or len(toks) > 8 or not re.search(r"[A-Za-z]", norm):
        return False
    if re.search(r"\d", re.sub(r"\([^)]*\)", "", norm)) or _is_prose(toks):
        return False
    letters = re.sub(r"[^A-Za-z]", "", norm)
    words = {re.sub(r"[^a-z]", "", t.lower()) for t in toks}
    return letters.isupper() or bool(words & _SECTION_WORDS)


@dataclass
class TracedRow:
    row: ParsedRow
    line_ids: Tuple[int, ...]          # indexes into TracedParse.lines (or CSV file line numbers for @row meta)


@dataclass
class UnreadLine:
    line_ids: Tuple[int, ...]
    text: str                          # the logical line that held result-like parts that could not be read
    count: int                         # how many such parts


@dataclass
class TracedParse:
    rows: List[TracedRow] = field(default_factory=list)
    unread: List[UnreadLine] = field(default_factory=list)
    unread_count: int = 0
    lines: List[str] = field(default_factory=list)       # the non-empty stripped physical lines parsed
    warnings: List[str] = field(default_factory=list)
    structured: bool = False                              # True for the structured-CSV format


def parse_lab_text_traced(text: str, decimal_comma: bool = False) -> TracedParse:
    """``parse_lab_text_ex`` that also says which physical lines every row, and every unread part, came from.

    Physical lines are the non-empty stripped lines of ``text`` numbered from 0."""
    lines = [ln.strip() for ln in (text or "").splitlines()]
    lines = [ln for ln in lines if ln]
    atomic = sum(1 for ln in lines if _is_atomic(ln))
    if lines and atomic / len(lines) >= 0.3:
        logical = reassemble_cells_traced(lines)
    else:
        logical = [(ln, (i,)) for i, ln in enumerate(lines)]
    ctx = _Ctx(decimal_comma=decimal_comma)
    out = TracedParse(lines=lines)
    last: Optional[ParsedRow] = None       # the row a following category list ("Deficient: <20") belongs to
    last_tr: Optional[TracedRow] = None
    i = 0
    while i < len(logical):
        ln, ids = logical[i]
        if _is_header_line(ln):
            j = i
            toks: List[str] = []
            while j < len(logical) and _is_header_line(logical[j][0]):
                toks.extend(_tokens(normalize_line(logical[j][0])))
                j += 1
            if j - i >= 2 or len(toks) >= 2:
                ctx.roles = _header_roles(toks)
                last = None
                last_tr = None
                i = j
                continue
        res = _parse_internal(ln, ctx)
        if res.unread:
            out.unread_count += res.unread
            out.unread.append(UnreadLine(ids, ln, res.unread))
        if res.rows:
            trs = [TracedRow(r, ids) for r in res.rows]
            out.rows.extend(trs)
            last = res.rows[-1] if len(res.rows) == 1 else None
            last_tr = trs[-1] if len(res.rows) == 1 else None
        elif res.kind == "interp" and last is not None:
            last.range_options.extend(res.options)
            finalize_ranges(last)
            if last_tr is not None:
                last_tr.line_ids = last_tr.line_ids + ids
        else:
            if res.kind != "interp":
                last = None
                last_tr = None
            if _is_section_heading(ln):
                ctx.section = normalize_line(ln).lower()
        i += 1
    return out


def parse_lab_text_ex(text: str, decimal_comma: bool = False) -> Tuple[List[ParsedRow], int]:
    """Plain report text -> (rows, number of result-like lines that could not be read)."""
    traced = parse_lab_text_traced(text, decimal_comma)
    return [t.row for t in traced.rows], traced.unread_count


def parse_lab_text(text: str, decimal_comma: bool = False) -> Tuple[List[ParsedRow], int]:
    """Plain report text -> (rows, number of result-like lines that could not be read)."""
    return parse_lab_text_ex(text, decimal_comma)


# ── canonical CSV lines (preprocessor's structured CSV path) ─────────────────

def build_canonical_line(name: str, value: Optional[float], prefix: str, unit: str,
                         low: Optional[float], high: Optional[float], *, strict_low: bool = False,
                         strict_high: bool = False, flag: Optional[str] = None, ref_unit: str = "",
                         text: str = "", sex_ranges: Optional[dict] = None) -> str:
    parts = [re.sub(r"\s+", " ", name).strip()]
    if value is not None:
        parts.append(f"{prefix}{fmt_num(value)}")
    unit = re.sub(r"\s+", "", unit or "")
    if unit and value is not None:
        parts.append(unit)
    if low is not None and high is not None:
        parts.append(f"{fmt_num(low)}~{fmt_num(high)}" if low < 0 else f"{fmt_num(low)}-{fmt_num(high)}")
    elif high is not None:
        parts.append(f"{'lt' if strict_high else 'le'}{fmt_num(high)}")
    elif low is not None:
        parts.append(f"{'gt' if strict_low else 'ge'}{fmt_num(low)}")
    if flag:
        parts.append(f"@flag={flag}")
    if ref_unit:
        parts.append("@refu=" + re.sub(r"\s+", "", ref_unit))
    if text:
        parts.append("@txt=" + re.sub(r"\s+", "_", text.strip()))
    if sex_ranges:
        enc = ";".join(f"{s}:{'' if lo is None else fmt_num(lo)}:{'' if hi is None else fmt_num(hi)}"
                       for s, (lo, hi) in sex_ranges.items())
        parts.append("@sx=" + enc)
    return " ".join(parts)


_CANON_RANGE = re.compile(
    r"^(?:(?P<lo>[+-]?\d+(?:\.\d+)?)[-~](?P<hi>\d+(?:\.\d+)?)|(?P<k>lt|le|gt|ge)(?P<n>\d+(?:\.\d+)?))$")
_CANON_VALUE = re.compile(r"^(?P<q><=|>=|<|>)?(?P<n>[+-]?\d+(?:\.\d+)?)$")


def parse_canonical_line(line: str) -> Optional[ParsedRow]:
    """Parse one `#structured-lab-csv v1` line (parsed right to left, so it
    is unambiguous even for names containing digits)."""
    toks = line.strip().split(" ")
    meta: Dict[str, str] = {}
    while toks and toks[-1].startswith("@") and "=" in toks[-1]:
        k, _, v = toks.pop().partition("=")
        meta[k[1:]] = v
    low = high = None
    strict_low = strict_high = False
    if toks:
        rm = _CANON_RANGE.match(toks[-1])
        if rm and len(toks) > 1:
            if rm.group("lo") is not None:
                low, high = float(rm.group("lo")), float(rm.group("hi"))
                if low > high:
                    low = high = None
            else:
                n = float(rm.group("n"))
                if rm.group("k") in ("lt", "le"):
                    high, strict_high = n, rm.group("k") == "lt"
                else:
                    low, strict_low = n, rm.group("k") == "gt"
            toks.pop()
    unit = ""
    value = None
    qualifier = None
    text_value = meta.get("txt", "").replace("_", " ") or None
    if toks and not _CANON_VALUE.match(toks[-1]) and len(toks) >= 3 and _CANON_VALUE.match(toks[-2]):
        unit = toks.pop()
    if toks and _CANON_VALUE.match(toks[-1]) and len(toks) >= 2:
        vm = _CANON_VALUE.match(toks.pop())
        value = float(vm.group("n"))
        qualifier = vm.group("q") or None
    elif text_value is None:
        return None
    name = " ".join(toks).strip()
    if not re.search(r"[A-Za-z]", name):
        return None
    sex_ranges = None
    if meta.get("sx"):
        sex_ranges = {}
        for item in meta["sx"].split(";"):
            s, lo_s, hi_s = (item.split(":") + ["", ""])[:3]
            sex_ranges[s] = (float(lo_s) if lo_s else None, float(hi_s) if hi_s else None)
    return ParsedRow(
        name=name, value=value, qualifier=qualifier, text_value=text_value, unit=unit or None,
        ref_low=low, ref_high=high, printed_flag=normalize_flag([meta["flag"]]) if meta.get("flag") else None,
        source="csv", line=line, ref_strict_low=strict_low, ref_strict_high=strict_high,
        ref_unit=meta.get("refu") or None, sex_ranges=sex_ranges,
    )


# ── whole-report entry points ─────────────────────────────────────────────────

STRUCTURED_LAB_MARKER = "#structured-lab-csv v1"
OTHER_ROWS_MARKER = "#other-rows"
WARN_PREFIX = "#warn "


_ROW_META_RE = re.compile(r"\s+@row=(\d+)\s*$")


def _split_row_meta(line: str) -> Tuple[str, Tuple[int, ...]]:
    m = _ROW_META_RE.search(line)
    if not m:
        return line, ()
    return line[:m.start()], (int(m.group(1)),)


def parse_structured_csv_traced(text: str) -> Optional[TracedParse]:
    """Structured CSV output of run_ocr -> TracedParse, or None if `text` is not that format.

    Line ids are the 1-based physical file lines named by ``@row=N`` meta (empty when the meta is absent)."""
    lines = (text or "").lstrip().splitlines()
    if not lines or lines[0].strip() != STRUCTURED_LAB_MARKER:
        return None
    out = TracedParse(structured=True)
    in_other = False
    for line in lines[1:]:
        s = line.strip()
        if not s:
            continue
        if s == OTHER_ROWS_MARKER:
            in_other = True
            continue
        if s.startswith(WARN_PREFIX):
            out.warnings.append(s[len(WARN_PREFIX):].strip())
            continue
        s, ids = _split_row_meta(s)
        if in_other:
            if _looks_like_result_line(s):
                out.unread_count += 1
                out.unread.append(UnreadLine(ids, s, 1))
            continue
        row = parse_canonical_line(s)
        if row is not None:
            out.rows.append(TracedRow(row, ids))
    return out


def parse_structured_csv(text: str) -> Optional[Tuple[List[ParsedRow], int, List[str]]]:
    """(rows, unread_numeric_lines, warnings) for run_ocr's structured CSV
    output, or None if `text` is not that format."""
    traced = parse_structured_csv_traced(text)
    if traced is None:
        return None
    return [t.row for t in traced.rows], traced.unread_count, traced.warnings


def parse_report_text_traced(text: str) -> TracedParse:
    """Structured CSV text or plain report text -> TracedParse (rows with line ids, unread lines, warnings)."""
    structured = parse_structured_csv_traced(text)
    if structured is not None:
        return structured
    return parse_lab_text_traced(text)


def parse_report_text(text: str) -> Tuple[List[ParsedRow], int, List[str]]:
    """Structured CSV text or plain report text -> (rows, unread lines, warnings)."""
    traced = parse_report_text_traced(text)
    return [t.row for t in traced.rows], traced.unread_count, traced.warnings
