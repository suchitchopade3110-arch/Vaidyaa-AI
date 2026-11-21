"""Row alignment and field comparators (P1 plan 6.6, 6.7).

Judge-side module: imports nothing from ``app``, ``clinical_reference`` or ``tools.eval.adapters`` (GN-11, plan
2.2). GT rows are dicts (as loaded from the GT JSON) or ``GTRow``-like objects; extracted rows are any object with
the ``ExtractedRow`` attribute names (duck typing, read through ``field``).

What counts as wrong (6.7): a matched pair is *correct* only if name, value, unit, range and flag are all right.
Nothing here is tuned to the engine; the comparators follow the plan text, taking the stricter reading where it is
silent (documented inline).
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, Sequence

from tools.eval import units
from tools.eval.catalogue import CATALOGUE
from tools.eval.clinical import qualitative_class

FIELDS = ("name", "value", "unit", "range", "flag")
NOISE_TOKENS = frozenset({"serum", "plasma", "blood", "level", "test", "count", "value", "result", "s", "sr"})
CRITICAL_FLAGS = frozenset({"CRITICAL_HIGH", "CRITICAL_LOW"})

ELIGIBLE_COST = 1.2
COST_T = 0.8
VALUE_COST_EQUAL, VALUE_COST_NEAR, VALUE_COST_FAR = 0.0, 0.3, 0.6
ORDER_WEIGHT = 0.01

_NO_UNIT_TYPES = ("qualitative", "info", "titre")  # unit_ok and range_ok are not applicable (true)


def field(obj: Any, name: str, default: Any = None) -> Any:
    """Attribute or key access, so GT dicts, GT dataclasses and extracted-row objects read alike."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ---------------------------------------------------------------- names and tokens (6.6.1)

_SPELLING = (
    (re.compile(r"haem"), "hem"),  # haemo -> hemo (and haema -> hema: Haematocrit)
    (re.compile(r"leuco"), "leuko"),
    (re.compile(r"colour"), "color"),
    (re.compile(r"\boe(?=[a-z])"), "e"),  # oedema, oestradiol, oesophageal
)
# a number keeps its decimal point and digit grouping ("4.2", "2,50,000"); everything else that is not a letter or
# digit is a separator ("punctuation to spaces")
_TOKEN = re.compile(r"\d+(?:[.,]\d+)*|[a-z0-9]+")


def tokens(text: Any) -> list[str]:
    """Lower-cased, spelling-normalised tokens of a text, in order (noise tokens kept)."""
    s = str(text or "").lower().replace("µ", "u").replace("μ", "u").replace("×", "x")
    for pat, rep in _SPELLING:
        s = pat.sub(rep, s)
    return _TOKEN.findall(s)


_CSV_DELIMITERS = ("\t", ";", ",")


def _csv_delimiter(lines: Sequence[str]) -> str | None:
    """The delimiter found on the most lines (tab, then semicolon, then comma on ties); None when there is none."""
    best, best_n = None, 0
    for d in _CSV_DELIMITERS:
        n = sum(1 for ln in lines if d in ln)
        if n > best_n:
            best, best_n = d, n
    return best


def csv_tokens(text: Any) -> list[str]:
    """Tokens of delimited text (a GEN-CSV export), field by field and in order.

    ``tokens`` keeps digit grouping ("2,50,000"), so on comma-delimited text it glues neighbouring numeric fields:
    "WBC,7.2,10^3/uL,4.0,11.0" gave "7.2,10" and "4.0,11.0", and a field "250" next to "150,450" read as
    "250,150,450" (P1 review LOW-4). Splitting on the file's delimiter first keeps every field's number whole and
    still reads decimal-comma values ("12,2") inside a semicolon file as one token."""
    s = str(text or "")
    lines = [ln for ln in s.splitlines() if ln.strip()]
    delim = _csv_delimiter(lines)
    if delim is None:
        return tokens(s)
    out: list[str] = []
    for row in csv.reader(lines, delimiter=delim):
        for cell in row:
            out += tokens(cell)
    return out


def name_tokens(text: Any) -> frozenset[str]:
    """Name tokens without the noise words. A name made only of noise words (urine "Blood") keeps its tokens;
    otherwise it could never be matched or covered at all."""
    toks = tokens(text)
    kept = [t for t in toks if t not in NOISE_TOKENS]
    return frozenset(kept or toks)


def jaccard(a: frozenset[str] | set[str], b: frozenset[str] | set[str]) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 0.0


def name_jaccard(g: Any, e: Any) -> float:
    """Token Jaccard of the GT name and the extracted ``name_raw``."""
    return jaccard(name_tokens(field(g, "name")), name_tokens(field(e, "name_raw")))


def contains_tokens(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    """True when ``needle`` occurs as a contiguous token run in ``haystack``."""
    n = len(needle)
    if n == 0:
        return False
    needle = list(needle)
    return any(list(haystack[i:i + n]) == needle for i in range(len(haystack) - n + 1))


# ---------------------------------------------------------------- numbers, qualifiers

_PLAIN_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")
_GROUPED_NUMBER = re.compile(r"[+-]?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?")  # 2,50,000 (Indian) and 250,000 (western)
_QUALIFIED = re.compile(r"^\s*(<=|>=|≤|≥|<|>)?\s*(.*?)\s*$")


def parse_number(x: Any) -> float | None:
    """A float from a number or a printed number string; digit grouping is normalised ("2,50,000" -> 250000).

    Anything else (text, several numbers, a decimal comma such as "4,5") is None."""
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x) if math.isfinite(x) else None
    s = re.sub(r"[\s  ]", "", str(x))
    if _GROUPED_NUMBER.fullmatch(s):
        s = s.replace(",", "")
    if _PLAIN_NUMBER.fullmatch(s):
        return float(s)
    return None


def norm_qualifier(q: Any) -> str | None:
    """None, "<", "<=", ">" or ">=". "<" and "<=" stay distinct (6.7)."""
    if q is None:
        return None
    s = str(q).replace("≤", "<=").replace("≥", ">=").replace(" ", "")
    return s or None


def parse_value_text(text: Any) -> tuple[str | None, float | None]:
    """("< 0.01") -> ("<", 0.01); ("2,50,000") -> (None, 250000.0)."""
    m = _QUALIFIED.match(str(text or ""))
    assert m is not None  # the pattern matches any string
    return norm_qualifier(m.group(1)), parse_number(m.group(2))


def numbers_equal(e: float, g: float) -> bool:
    """Printed-space equality: |e - g| <= 1e-9 + 1e-6 |g| (6.7)."""
    return abs(e - g) <= 1e-9 + 1e-6 * abs(g)


def _close_rel(a: float, b: float, rel: float) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b)) + 1e-12


# Plan 6.7: "mEq/L and mmol/L stay distinct". The GT side converts mEq/L 1:1 for the monovalent ions only to derive
# flags and criticality (P1 review LOW-5); the comparators never treat the two units as equivalent.
_NEVER_EQUIVALENT_UNITS = frozenset({"mEq/L"})


def _explicit_canonical(key: str | None, value: float, unit: Any) -> float | None:
    """``value`` in the analyte's conventional unit, or None when it cannot be converted *without assuming a unit*.

    A missing unit is only taken as the conventional one when the analyte has no unit at all (INR, pH, ratios).
    Otherwise an empty unit is unknown, never assumed (S6), so a unit-less value is never canonical-equivalent to
    a value with a unit. mEq/L is never converted here (plan 6.7 keeps it distinct from mmol/L)."""
    if key is None or key not in CATALOGUE:
        return None
    if units.canon_unit(unit) in _NEVER_EQUIVALENT_UNITS:
        return None
    if units.canon_unit(unit) == "" and units.canon_unit(CATALOGUE[key].unit) != "":
        return None
    try:
        return units.to_canonical(key, value, unit or "")[0]
    except (ValueError, KeyError, ZeroDivisionError):
        return None


def canonical_equal(key: str | None, ev: float, eu: Any, gv: float, gu: Any) -> bool:
    """(ev, eu) and (gv, gu) are the same quantity in canonical units, within 1e-4 relative (6.7)."""
    ce = _explicit_canonical(key, ev, eu)
    cg = _explicit_canonical(key, gv, gu)
    return ce is not None and cg is not None and _close_rel(ce, cg, 1e-4)


# ---------------------------------------------------------------- qualitative, titre, range results

_GRADE_DIGIT = re.compile(r"^([1-4])\s*\+$")
_GRADE_PLUS = re.compile(r"^\++$")


def qualitative_grade(text: Any) -> tuple[str, str] | None:
    """(class, grade) of a qualitative result, or None when it is not a recognised qualitative result.

    All negative words share one grade (Nil ≡ Negative ≡ Absent ≡ Non-Reactive). On the positive side Trace,
    1+ to 4+ ("++" ≡ "2+") and the ungraded words (Positive, Present, Reactive, Detected) are distinct grades,
    so Trace ≢ 1+ and Positive ≢ 1+."""
    t = re.sub(r"[-_]+", " ", str(text or "").strip().lower())
    t = re.sub(r"\s+", " ", t)
    if not t:
        return None
    try:
        cls = qualitative_class(t)
    except ValueError:
        return None
    if cls == "NEGATIVE":
        return cls, ""
    if t == "trace":
        return cls, "trace"
    m = _GRADE_DIGIT.match(t)
    if m:
        return cls, f"{m.group(1)}+"
    if _GRADE_PLUS.match(t):
        return cls, f"{len(t)}+"
    return cls, "ungraded"


def qualitative_equal(a: Any, b: Any) -> bool:
    ga, gb = qualitative_grade(a), qualitative_grade(b)
    return ga is not None and ga == gb


def _qualitative_class_or_none(text: Any) -> str | None:
    g = qualitative_grade(text)
    return None if g is None else g[0]


_TITRE = re.compile(r"^\s*1\s*[:/]\s*(\d+)\s*$")


def titre_value(text: Any) -> int | None:
    m = _TITRE.match(str(text or ""))
    return int(m.group(1)) if m else None


_NIL_WORDS = frozenset({"nil", "absent", "none", "not seen"})
_RANGE_TEXT = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*$")


def result_bounds(text: Any) -> tuple[float, float] | None:
    """Bounds of a range result ("2-3" -> (2, 3); "4" -> (4, 4); "Nil" -> (0, 0)); None when unreadable."""
    t = str(text or "").strip().lower()
    if not t:
        return None
    if t in _NIL_WORDS:
        return 0.0, 0.0
    m = _RANGE_TEXT.match(t)
    if m:
        lo, hi = sorted((float(m.group(1)), float(m.group(2))))
        return lo, hi
    v = parse_number(t)
    return (v, v) if v is not None else None


def _info_text(text: Any) -> str:
    return " ".join(tokens(text))


# ---------------------------------------------------------------- field comparators (6.7)

def _e_text(e: Any) -> Any:
    tv = field(e, "text_value")
    return tv if tv not in (None, "") else field(e, "value_raw")


def _g_text(g: Any) -> Any:
    tv = field(g, "text_value")
    return tv if tv not in (None, "") else field(g, "value_text")


def name_ok(g: Any, e: Any) -> bool:
    gk, ek = field(g, "analyte_key"), field(e, "analyte_key")
    if gk is not None and ek == gk:
        return True
    return bool(field(g, "key_optional")) and ek in (None, gk) and name_jaccard(g, e) >= 0.5


def value_ok(g: Any, e: Any) -> bool:
    vt = field(g, "value_type", "numeric")
    if vt == "numeric":
        if norm_qualifier(field(e, "qualifier")) != norm_qualifier(field(g, "qualifier")):
            return False
        ev, gv = parse_number(field(e, "value_num")), parse_number(field(g, "value"))
        if ev is None or gv is None:
            return False
        return numbers_equal(ev, gv) or canonical_equal(
            field(g, "analyte_key"), ev, field(e, "unit_raw"), gv, field(g, "unit"))
    if vt == "qualitative":
        return qualitative_equal(_e_text(e), _g_text(g))
    if vt == "titre":
        gt = titre_value(_g_text(g))
        return gt is not None and titre_value(_e_text(e)) == gt
    if vt == "range_result":
        gb = result_bounds(_g_text(g))
        eb = result_bounds(_e_text(e))
        if eb is None:
            ev = parse_number(field(e, "value_num"))
            eb = (ev, ev) if ev is not None else None
        return gb is not None and eb is not None and all(numbers_equal(a, b) for a, b in zip(eb, gb))
    if vt == "info":
        gv, ev = parse_number(field(g, "value")), parse_number(field(e, "value_num"))
        if gv is not None and ev is not None:
            return numbers_equal(ev, gv)
        gt, et = _info_text(_g_text(g)), _info_text(_e_text(e))
        return bool(gt) and gt == et
    return False


def unit_ok(g: Any, e: Any) -> bool:
    vt = field(g, "value_type", "numeric")
    if vt in _NO_UNIT_TYPES:
        return True
    if units.canon_unit(field(e, "unit_raw")) == units.canon_unit(field(g, "unit")):
        return True  # same canonical token, or both empty / None
    if vt != "numeric":
        return False
    ev, gv = parse_number(field(e, "value_num")), parse_number(field(g, "value"))
    return ev is not None and gv is not None and canonical_equal(
        field(g, "analyte_key"), ev, field(e, "unit_raw"), gv, field(g, "unit"))


def range_ok(g: Any, e: Any) -> bool:
    vt = field(g, "value_type", "numeric")
    if vt in _NO_UNIT_TYPES:
        return True
    gr = field(g, "effective_range_page") or {}
    gl, gh = gr.get("low"), gr.get("high")
    el, eh = parse_number(field(e, "ref_low")), parse_number(field(e, "ref_high"))
    gl, gh = parse_number(gl), parse_number(gh)
    if gl is None and gh is None and el is None and eh is None:
        return True  # both effective ranges null
    if (gl is None) != (el is None) or (gh is None) != (eh is None):
        return False  # the same sides must be present
    key, gu, eu = field(g, "analyte_key"), field(g, "unit"), field(e, "unit_raw")
    for eb, gb in ((el, gl), (eh, gh)):
        if gb is None:
            continue
        if not (_close_rel(eb, gb, 1e-6) or canonical_equal(key, eb, eu, gb, gu)):
            return False
    return True


def flag_ok(g: Any, e: Any) -> bool:
    """``e.flag == g.flag`` (the page flag). Legacy ABNORMAL never equals HIGH or LOW."""
    return str(field(e, "flag") or "").strip().upper() == str(field(g, "flag") or "").strip().upper()


@dataclass(frozen=True)
class FieldCheck:
    name: bool
    value: bool
    unit: bool
    range: bool
    flag: bool

    @property
    def correct(self) -> bool:
        return self.name and self.value and self.unit and self.range and self.flag

    @property
    def failed(self) -> tuple[str, ...]:
        """The error signature: the failed fields in the fixed order name, value, unit, range, flag."""
        return tuple(f for f in FIELDS if not getattr(self, f))

    @property
    def signature(self) -> str:
        return "+".join(self.failed) if self.failed else "ok"


def compare(g: Any, e: Any) -> FieldCheck:
    return FieldCheck(name_ok(g, e), value_ok(g, e), unit_ok(g, e), range_ok(g, e), flag_ok(g, e))


# ---------------------------------------------------------------- alignment (6.6)

def value_cost(g: Any, e: Any) -> float:
    """0 if equal under the value comparator; numeric: 0.3 within 10% (printed space), else 0.6; qualitative:
    0 for the same class, else 0.6; titre, info and range results: 0 if equal, else 0.6."""
    vt = field(g, "value_type", "numeric")
    if vt == "qualitative":
        gc = _qualitative_class_or_none(_g_text(g))
        return VALUE_COST_EQUAL if gc is not None and gc == _qualitative_class_or_none(_e_text(e)) else VALUE_COST_FAR
    if value_ok(g, e):
        return VALUE_COST_EQUAL
    if vt == "numeric":
        ev, gv = parse_number(field(e, "value_num")), parse_number(field(g, "value"))
        if ev is not None and gv is not None and abs(ev - gv) <= 0.1 * abs(gv):
            return VALUE_COST_NEAR
    return VALUE_COST_FAR


def name_rule(g: Any, e: Any) -> tuple[str, float] | None:
    """(rule, name cost) for a candidate pair, or None. K: same non-null key; N: name Jaccard >= 0.5;
    T: GT name tokens inside the extracted name and source text, with name Jaccard >= 0.25."""
    gk = field(g, "analyte_key")
    if gk is not None and field(e, "analyte_key") == gk:
        return "K", 0.0
    gt = name_tokens(field(g, "name"))
    et = name_tokens(field(e, "name_raw"))
    j = jaccard(gt, et)
    if j >= 0.5:
        return "N", 1.0 - j
    st = frozenset(tokens(field(e, "source_text")))
    if gt and gt <= (et | st) and j >= 0.25:
        return "T", COST_T
    return None


@dataclass(frozen=True)
class Pair:
    g: int  # index into the GT rows given to ``align``
    e: int  # index into the extracted rows given to ``align``
    rule: str  # K, N or T
    cost: float


@dataclass
class Alignment:
    pairs: list[Pair]  # sorted by GT index
    unmatched_gt: list[int]
    unmatched_ext: list[int]
    covered: dict[int, list[int]] = dc_field(default_factory=dict)  # GT index -> unread lines covering it
    unread_used: list[int] = dc_field(default_factory=list)  # unread lines that cover at least one GT row

    def e_of(self, gi: int) -> int | None:
        return next((p.e for p in self.pairs if p.g == gi), None)

    def g_of(self, ei: int) -> int | None:
        return next((p.g for p in self.pairs if p.e == ei), None)


UNREAD_LINE_MAX_ROWS = 2  # GT rows one unread line can cover (6.6.7)


def covers(u: Any, g: Any) -> bool:
    """``u`` covers ``g`` if its normalised text contains g's printed value as a token (run) and at least one of
    g's name tokens (6.6.7)."""
    text = field(u, "source_text") or f"{field(u, 'name_raw') or ''} {field(u, 'value_raw') or ''}"
    ut = tokens(text)
    return contains_tokens(ut, tokens(field(g, "value_text"))) and bool(name_tokens(field(g, "name")) & set(ut))


def align(gt_rows: Sequence[Any], extracted: Sequence[Any], unread: Sequence[Any] = ()) -> Alignment:
    """Greedy minimum-cost alignment of GT rows to extracted rows, then unread-line coverage (6.6).

    ``gt_rows`` are the rows in the job's scope (one page for a photo job); ``extracted`` are rows of kind ``row``
    and ``unread`` the ``unread_line`` items. Candidates are stably sorted by (cost, g index, e index) and a pair is
    taken when both sides are still free. Deterministic; no scipy."""
    ng, ne = len(gt_rows), len(extracted)
    scale = max(ng, ne) or 1
    cands: list[tuple[float, int, int, str]] = []
    for gi, g in enumerate(gt_rows):
        for ei, e in enumerate(extracted):
            nr = name_rule(g, e)
            if nr is None:
                continue
            rule, ncost = nr
            cost = ncost + value_cost(g, e) + ORDER_WEIGHT * abs(gi - ei) / scale
            if cost <= ELIGIBLE_COST:
                cands.append((cost, gi, ei, rule))
    cands.sort(key=lambda c: (c[0], c[1], c[2]))
    g_free, e_free = [True] * ng, [True] * ne
    pairs: list[Pair] = []
    for cost, gi, ei, rule in cands:
        if g_free[gi] and e_free[ei]:
            g_free[gi] = e_free[ei] = False
            pairs.append(Pair(gi, ei, rule, cost))
    pairs.sort(key=lambda p: p.g)
    unmatched_gt = [i for i in range(ng) if g_free[i]]
    unmatched_ext = [i for i in range(ne) if e_free[i]]
    covered: dict[int, list[int]] = {}
    # One unread line is one printed line, which holds at most two results (plan 6.6.7, "two per line"). Without this
    # bound a single page-length ``source_text`` would cover every omission and critical (review P1 F2). The stricter
    # reading is taken (a hard cap of UNREAD_LINE_MAX_ROWS GT rows per line, granted in GT order) because bboxes are
    # not available before P2; a bbox/page overlap test may only narrow this further.
    load = [0] * len(unread)
    for gi in unmatched_gt:
        hit = [ui for ui, u in enumerate(unread) if load[ui] < UNREAD_LINE_MAX_ROWS and covers(u, gt_rows[gi])]
        if hit:
            covered[gi] = hit
            for ui in hit:
                load[ui] += 1
    used = sorted({ui for hit in covered.values() for ui in hit})
    return Alignment(pairs, unmatched_gt, unmatched_ext, covered, used)


__all__ = [
    "Alignment", "CRITICAL_FLAGS", "UNREAD_LINE_MAX_ROWS", "FIELDS", "FieldCheck", "NOISE_TOKENS", "Pair", "align",
    "canonical_equal", "compare", "contains_tokens", "covers", "csv_tokens", "field", "flag_ok", "jaccard", "name_jaccard", "name_ok", "name_rule",
    "name_tokens", "norm_qualifier", "numbers_equal", "parse_number", "parse_value_text", "qualitative_equal",
    "qualitative_grade", "range_ok", "result_bounds", "titre_value", "tokens", "unit_ok", "value_cost", "value_ok",
]
