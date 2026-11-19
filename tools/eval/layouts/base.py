"""Printed report specs, drawing kit, pagination and header/footer/decoy helpers (P1 plan sections 2.1, 3.7, 3.9, 5).

GT-side module: imports nothing from ``app``. Pure and deterministic: all randomness comes from
``random.Random`` instances passed in; no time or global state.

A single ``PrintedRow`` list feeds BOTH the drawn page (``DocBuilder.row`` records page and bbox on the row)
and the ground truth (``build_gt``), so the two cannot diverge.

Extension points for further families (T06 and later): add a ``FamilySpec`` in ``templates.py`` and register
it; reuse ``DocBuilder`` / ``Column`` / ``Identity`` / ``draw_standard_footer`` from here.
"""

from __future__ import annotations

import datetime
import random
import re
from dataclasses import dataclass, field
from typing import Any

from reportlab.lib.colors import Color
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas as rl_canvas

from tools.eval import clinical
from tools.eval import config as cfg
from tools.eval.catalogue import CATALOGUE
from tools.eval.clinical import PrintedRange
from tools.eval.gt_schema import GT_SCHEMA, GTRange, GTReport, GTRow
from tools.eval.scenarios import Patient

A4 = (595.28, 841.89)
LETTER = (612.0, 792.0)

FONT_REGULAR = "Helvetica"
FONT_BOLD = "Helvetica-Bold"
FONT_ITALIC = "Helvetica-Oblique"
FONT_SERIF = "Times-Roman"
FONT_SERIF_BOLD = "Times-Bold"
FONT_MONO = "Courier"
STANDARD_FONTS = frozenset({
    "Helvetica", "Helvetica-Bold", "Helvetica-Oblique", "Times-Roman", "Times-Bold", "Times-Italic",
    "Courier", "Courier-Bold",
})

# Section 3.9 deny-list (case-insensitive, word boundary). "CLIA" stays allowed (method word).
DENY_LIST = (
    "Lal", "PathLabs", "SRL", "Agilus", "Metropolis", "Thyrocare", "Quest", "LabCorp", "NHS", "Apollo", "Fortis",
    "Medanta", "Manipal", "Neuberg", "Vijaya", "Suburban", "Redcliffe", "Healthians", "Pathkind", "1mg", "Mayo",
    "ARUP", "BioReference", "Sonic", "Eurofins", "NABL", "CAP", "AIIMS",
)
_DENY_RE = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(re.escape(t) for t in DENY_LIST) + r")(?![A-Za-z0-9])", re.I)

# Fictional phone patterns (section 3.9)
PHONE_PATTERNS = (
    re.compile(r"\(\d{3}\) 555-01\d{2}"),  # US fiction range
    re.compile(r"01632 960\d{3}"),  # UK drama range
    re.compile(r"0000-2\d{6}"),  # India, invalid zero block
    re.compile(r"\+91 00000 0\d{4}"),
    re.compile(r"1800-000-0\d{3}"),
)


def deny_hits(text: str) -> list[str]:
    """Deny-list tokens present in ``text`` (empty when clean)."""
    return [m.group(0) for m in _DENY_RE.finditer(text)]


_REPLACEMENTS = {"≤": "<=", "≥": ">=", "−": "-", "→": "->", "•": "-", " ": " "}


def sanitize(text: str) -> str:
    """Standard-14 fonts only: map characters outside WinAnsi; raise when something unencodable remains."""
    for k, v in _REPLACEMENTS.items():
        text = text.replace(k, v)
    try:
        text.encode("cp1252")
    except UnicodeEncodeError as exc:  # pragma: no cover - defensive
        raise ValueError(f"character outside WinAnsi in {text!r}") from exc
    return text


def text_width(text: str, font: str, size: float) -> float:
    return pdfmetrics.stringWidth(text, font, size)


# ---------------------------------------------------------------- printed specs

@dataclass
class PrintedRow:
    """One printed result row: the single source of truth for page and ground truth."""

    key: str
    name: str
    section: str
    value_type: str = "numeric"
    value: float | None = None
    value_text: str = ""
    qualifier: str | None = None
    text_value: str | None = None
    unit: str = ""
    unit_placement: str = "cell"
    unit_true: str = ""
    printed_range: PrintedRange = field(default_factory=PrintedRange)
    range_lines: list[str] = field(default_factory=list)
    printed_flag: str = ""
    method: str = ""
    state: str = "normal"  # scenario state the value was drawn for (normal/mild/severe/critical/derived)
    features: list[str] = field(default_factory=list)
    previous: list[dict[str, Any]] = field(default_factory=list)
    unlabelled_multivalue: bool = False
    titre_cutoff: int | None = None
    # filled by DocBuilder.row
    page: int = 0
    bbox: list[float] = field(default_factory=list)
    line_text: str = ""


@dataclass
class Decoy:
    kind: str
    text: str
    page: int
    bbox: list[float]


@dataclass
class TextOp:
    x: float
    y: float  # baseline, top-left origin
    text: str
    font: str
    size: float
    gray: float = 0.0


@dataclass
class ShapeOp:
    kind: str  # line | rect | fillrect | circle | fillcircle
    args: tuple[float, ...]
    width: float = 0.5
    gray: float = 0.0


@dataclass
class PageLayout:
    n: int
    ops: list[TextOp | ShapeOp] = field(default_factory=list)


@dataclass
class PrintedReport:
    report_id: str
    split: str
    family: str
    seed: int
    page_size: tuple[float, float]
    lab: dict[str, Any]
    patient: Patient
    scenario: dict[str, Any]
    unit_system: str
    rows: list[PrintedRow]
    decoys: list[Decoy]
    pages: list[PageLayout]
    features: list[str] = field(default_factory=list)
    template_id: str | None = None

    @property
    def n_pages(self) -> int:
        return len(self.pages)

    def all_text(self) -> str:
        return "\n".join(op.text for p in self.pages for op in p.ops if isinstance(op, TextOp))


# ---------------------------------------------------------------- drawing kit

@dataclass(frozen=True)
class Column:
    key: str
    header: str
    x: float  # left edge ('l'), right edge ('r') or centre ('c')
    align: str = "l"


@dataclass
class Cell:
    """One cell of a row: ``lines`` are (text, font, size) triples drawn top to bottom."""

    x: float
    lines: list[tuple[str, str, float]]
    align: str = "l"


def simple_cell(x: float, text: str | list[str], font: str, size: float, align: str = "l") -> Cell:
    texts = [text] if isinstance(text, str) else list(text)
    return Cell(x, [(t, font, size) for t in texts if t != ""], align)


def line_height(size: float) -> float:
    return size * 1.22


class DocBuilder:
    """Page model with a flowing cursor (``y``, top-left origin), pagination and bbox capture."""

    def __init__(self, size: tuple[float, float], *, margin_x: float = 40.0, top: float = 36.0,
                 bottom: float = 84.0, on_new_page=None) -> None:
        self.size = size
        self.margin_x = margin_x
        self.top = top
        self.bottom = bottom
        self.on_new_page = on_new_page  # callable(doc, page_no, is_break) draws the repeated header
        self.pages: list[PageLayout] = []
        self.decoys: list[Decoy] = []
        self.rows: list[PrintedRow] = []
        self.y = top
        self.new_page(first=True)

    # -- page flow
    @property
    def cur(self) -> PageLayout:
        return self.pages[-1]

    @property
    def limit(self) -> float:
        return self.size[1] - self.bottom

    def new_page(self, first: bool = False) -> None:
        self.pages.append(PageLayout(len(self.pages) + 1))
        self.y = self.top
        if self.on_new_page is not None:
            self.on_new_page(self, len(self.pages), not first)

    def ensure(self, h: float) -> bool:
        """Start a new page when ``h`` points do not fit; True when a break happened."""
        if self.y + h > self.limit:
            self.new_page()
            return True
        return False

    # -- primitives (all coordinates top-left origin; ``y_top`` is the top of the text line box)
    def put(self, x: float, y_top: float, text: str, font: str, size: float, *, align: str = "l",
            decoy: str | None = None, gray: float = 0.0, page: PageLayout | None = None) -> list[float]:
        text = sanitize(text)
        pg = page or self.cur
        w = text_width(text, font, size)
        x0 = x if align == "l" else x - w if align == "r" else x - w / 2
        base = y_top + size * 0.96
        pg.ops.append(TextOp(round(x0, 2), round(base, 2), text, font, size, gray))
        bbox = [round(x0, 2), round(base - 0.96 * size, 2), round(x0 + w, 2), round(base + 0.26 * size, 2)]
        if decoy:
            self.decoys.append(Decoy(decoy, text, pg.n, list(bbox)))
        return bbox

    def line(self, x0: float, y0: float, x1: float, y1: float, width: float = 0.5, gray: float = 0.0,
             page: PageLayout | None = None) -> None:
        (page or self.cur).ops.append(ShapeOp("line", (x0, y0, x1, y1), width, gray))

    def rect(self, x: float, y: float, w: float, h: float, *, fill: float | None = None, width: float = 0.5,
             gray: float = 0.0, page: PageLayout | None = None) -> None:
        pg = page or self.cur
        if fill is not None:
            pg.ops.append(ShapeOp("fillrect", (x, y, w, h), width, fill))
        else:
            pg.ops.append(ShapeOp("rect", (x, y, w, h), width, gray))

    def circle(self, cx: float, cy: float, r: float, *, fill: float | None = None, gray: float = 0.0) -> None:
        if fill is not None:
            self.cur.ops.append(ShapeOp("fillcircle", (cx, cy, r), 0.5, fill))
        else:
            self.cur.ops.append(ShapeOp("circle", (cx, cy, r), 0.8, gray))

    # -- rows
    def row_height(self, cells: list[Cell], pad: float) -> float:
        return max((sum(line_height(s) for _, _, s in c.lines) for c in cells), default=0.0) + pad

    def row(self, prow: PrintedRow, cells: list[Cell], pad: float = 5.0) -> None:
        """Draw a result row, breaking the page first when it does not fit; record page, bbox and line text."""
        h = self.row_height(cells, pad)
        self.ensure(h)
        top = self.y + pad / 2
        boxes: list[list[float]] = []
        parts: list[str] = []
        for c in cells:
            y = top
            texts = []
            for text, font, size in c.lines:
                boxes.append(self.put(c.x, y, text, font, size, align=c.align))
                texts.append(sanitize(text))
                y += line_height(size)
            if texts:
                parts.append(" ".join(texts))
        prow.page = self.cur.n
        prow.bbox = [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes),
                     max(b[3] for b in boxes)]
        prow.line_text = " | ".join(parts)
        self.rows.append(prow)
        self.y += h

    def finish(self, footer_fn) -> None:
        total = len(self.pages)
        for pg in self.pages:
            footer_fn(self, pg, total)


# ---------------------------------------------------------------- fictional identity kit (section 3.9)

LAB_NAMES = (
    "Larkspur Diagnostics", "Brightwater Clinical Laboratory", "Orchid Valley Pathology", "Halcyon Diagnostics",
    "Cedarwood Medical Laboratory", "Riverbend Reference Laboratory", "Saffron Diagnostics",
    "Tamarind Lifecare Labs", "Juniper Clinical Diagnostics", "Bluegate Pathology Services",
    "Marigold Health Laboratory", "Windermere Diagnostics",
)
STREETS_IN = ("Bluebell Road", "Jasmine Lane", "Sunflower Marg", "Peacock Avenue", "Lotus Street", "Banyan Path")
CITIES_IN = ("Pune", "Hyderabad", "Chennai", "Kochi", "Jaipur", "Lucknow", "Bengaluru", "Nagpur")
STREETS_US = ("Elmgrove Avenue", "Willowbrook Drive", "Harvest Lane", "Foxglove Court", "Cobalt Street")
CITIES_US = ("Springfield", "Riverton", "Fairview", "Lakeside", "Oakdale")
STATES_US = ("IL", "OH", "TX", "CA", "NY")
DOCTOR_FIRST = ("Anil", "Sudha", "Rajan", "Meena", "Harish", "Leena", "Ravi", "Nandini", "Alan", "Judith", "Martin",
                "Clara")
DOCTOR_LAST = ("Varma", "Pillai", "Deshpande", "Kapoor", "Banerjee", "Naidu", "Ferreira", "Holloway", "Prescott",
               "Langford", "Rangan", "Sethi")


@dataclass(frozen=True)
class Identity:
    lab_name: str
    initials: str
    address: str
    address_street: str
    address_city: str
    phone: str
    tollfree: str
    email: str
    web: str
    accreditation: str
    pathologist: str
    reg_no: str
    referrer: str
    lab_no: str
    sample_id: str
    tat: str
    locale: str


def make_identity(rng: random.Random, locale: str) -> Identity:
    name = rng.choice(LAB_NAMES)
    initials = "".join(w[0] for w in name.split()[:2]).upper()
    slug = name.split()[0].lower()
    if locale == "US":
        street = f"{rng.randint(100, 9899)} {rng.choice(STREETS_US)}"
        city = f"{rng.choice(CITIES_US)}, {rng.choice(STATES_US)} {rng.randint(10000, 99999)}"
        phone = f"({rng.randint(201, 989)}) 555-01{rng.randint(0, 99):02d}"
        toll = f"(800) 555-01{rng.randint(0, 99):02d}"
    else:
        street = f"{rng.randint(2, 98)} {rng.choice(STREETS_IN)}"
        city = f"{rng.choice(CITIES_IN)} {rng.randint(110001, 695999)}"
        phone = f"0000-2{rng.randint(0, 999999):06d}" if rng.random() < 0.6 else f"+91 00000 0{rng.randint(0, 9999):04d}"
        toll = f"1800-000-0{rng.randint(0, 999):03d}"
    doc = f"Dr. {rng.choice(DOCTOR_FIRST)} {rng.choice(DOCTOR_LAST)}"
    ref = f"Dr. {rng.choice(DOCTOR_FIRST)} {rng.choice(DOCTOR_LAST)}"
    return Identity(
        lab_name=name, initials=initials, address=f"{street}, {city}", address_street=street, address_city=city,
        phone=phone, tollfree=toll, email=f"info@{slug}.example.org", web=f"www.{slug}.example.com",
        accreditation=f"Accreditation Cert. No. FA-{rng.randint(1000, 9999)}",
        pathologist=f"{doc}, MD (Pathology)", reg_no=f"FX-{rng.randint(10000, 99999)}", referrer=ref,
        lab_no=f"LN{rng.randint(10000000, 99999999)}", sample_id=f"S{rng.randint(1000000, 9999999)}",
        tat=rng.choice(("Reports within 24 hours", "Turnaround time: 6 hours", "TAT: 1 working day")), locale=locale,
    )


def fixed_dates(rng: random.Random) -> tuple[datetime.date, datetime.date]:
    """Collection and report dates from a fixed epoch (no clock)."""
    collected = datetime.date(2026, 1, 5) + datetime.timedelta(days=rng.randint(0, 280))
    return collected, collected + datetime.timedelta(days=rng.choice((0, 0, 1)))


def fmt_date(d: datetime.date, style: str) -> str:
    return d.strftime("%m/%d/%Y") if style == "us" else d.strftime("%d/%m/%Y")


def draw_logo(doc: DocBuilder, x: float, y: float, ident: Identity, r: float = 15.0) -> None:
    """Simple geometric logo with initials (no real marks)."""
    doc.circle(x + r, y + r, r, fill=0.82)
    doc.circle(x + r, y + r, r, gray=0.25)
    doc.rect(x + r * 0.45, y + r * 0.45, r * 1.1, r * 1.1, gray=0.25, width=0.8)
    doc.put(x + r, y + r - 4.2, ident.initials, FONT_BOLD, 8.5, align="c")


def patient_lines(pt: Patient, ident: Identity, style: str) -> dict[str, tuple[str, str | None]]:
    """Patient-block fields as {label: (text, decoy kind)}; unknown demographics are simply not printed."""
    out: dict[str, tuple[str, str | None]] = {"name": (f"Patient Name: {pt.name}", None)}
    if pt.input_age is not None and pt.input_sex is not None:
        out["agesex"] = (f"Age / Sex: {pt.input_age} Years / {pt.input_sex.capitalize()}", "header_demographic")
    elif pt.input_age is not None:
        out["agesex"] = (f"Age: {pt.input_age} Years", "header_demographic")
    elif pt.input_sex is not None:
        out["agesex"] = (f"Sex: {pt.input_sex.capitalize()}", "header_demographic")
    out["pid"] = (f"Patient ID: {pt.patient_id}", "header_id")
    out["labno"] = (f"Lab No: {ident.lab_no}", "header_id")
    out["sample"] = (f"Sample ID: {ident.sample_id}", "header_id")
    out["ref"] = (f"Referred By: {ident.referrer}", None)
    return out


def draw_standard_footer(doc: DocBuilder, pg: PageLayout, total: int, ident: Identity, *, font: str = FONT_REGULAR,
                         size: float = 7.0, date_style: str = "in", right_edge: float | None = None,
                         signature: bool = True) -> None:
    """Footer on every page: page x of y, end-of-report, phone, toll-free, TAT, accreditation, address, signature.

    Every numeric or result-like footer line is recorded as a GT decoy (section 3.7).
    """
    W, H = doc.size
    mx = doc.margin_x
    rx = right_edge if right_edge is not None else W - mx
    y = H - doc.bottom + 6
    doc.line(mx, y - 3, rx, y - 3, 0.4, 0.4, page=pg)
    last = pg.n == total
    if last:
        doc.put((mx + rx) / 2, y, "*** End of Report ***", FONT_BOLD, size + 1, align="c", decoy="end_of_report", page=pg)
        y += size * 1.6
        if signature:
            doc.put(rx, y, f"{ident.pathologist}  |  Reg. No. {ident.reg_no}", font, size, align="r",
                    decoy="signature", page=pg)
            y += size * 1.4
    doc.put(mx, y, f"Tel: {ident.phone}", font, size, decoy="phone", page=pg)
    mid = mx + 130
    doc.put(mid, y, f"Toll Free: {ident.tollfree}", font, size, decoy="tollfree", page=pg)
    doc.put(rx, y, f"{ident.tat}", font, size, align="r", decoy="turnaround", page=pg)
    y += size * 1.4
    doc.put(mx, y, ident.accreditation, font, size, decoy="accreditation", page=pg)
    doc.put(rx, y, f"Page {pg.n} of {total}", FONT_BOLD, size, align="r", decoy="page_x_of_y", page=pg)
    y += size * 1.4
    doc.put(mx, y, f"{ident.lab_name}, {ident.address}  |  {ident.email}", font, size, decoy="address", page=pg)


# ---------------------------------------------------------------- native PDF writer

def write_pdf(pages: list[PageLayout], size: tuple[float, float], path: str, title: str = "Laboratory Report") -> None:
    """Write pages with reportlab (standard-14 fonts, ``invariant=1`` so the bytes are reproducible)."""
    c = rl_canvas.Canvas(path, pagesize=size, invariant=1, pageCompression=0)
    c.setTitle(title)
    c.setAuthor("")
    c.setCreator("vaidya-eval")
    H = size[1]
    for pg in pages:
        for op in pg.ops:
            g = Color(op.gray, op.gray, op.gray)
            if isinstance(op, TextOp):
                if op.font not in STANDARD_FONTS:
                    raise ValueError(f"non-standard font {op.font}")
                c.setFillColor(g)
                c.setFont(op.font, op.size)
                c.drawString(op.x, H - op.y, op.text)
                continue
            c.setLineWidth(op.width)
            c.setStrokeColor(g)
            c.setFillColor(g)
            if op.kind == "line":
                x0, y0, x1, y1 = op.args
                c.line(x0, H - y0, x1, H - y1)
            elif op.kind in ("rect", "fillrect"):
                x, y, w, h = op.args
                c.rect(x, H - y - h, w, h, stroke=int(op.kind == "rect"), fill=int(op.kind == "fillrect"))
            else:
                cx, cy, r = op.args
                c.circle(cx, H - cy, r, stroke=int(op.kind == "circle"), fill=int(op.kind == "fillcircle"))
        c.showPage()
    c.save()


# ---------------------------------------------------------------- ground truth from the same rows

def _opt_dict(o: clinical.OptionRange) -> dict[str, Any]:
    return {"label": o.label, "low": o.low, "high": o.high, "kind": o.kind, "sex": o.sex, "age_min": o.age_min,
            "age_max": o.age_max}


def row_truth(r: PrintedRow, pt: Patient) -> clinical.RowTruth:
    """Derive page flag, true flag, criticality and ambiguity for a printed row (plan 3.8, 4.2)."""
    return clinical.derive_row(
        r.key, value=r.value, qualifier=r.qualifier, unit=r.unit, unit_true=r.unit_true, text_value=r.text_value,
        printed_range=r.printed_range, printed_flag=r.printed_flag, input_sex=pt.input_sex, input_age=pt.input_age,
        true_sex=pt.sex_true, true_age=pt.age_true, unlabelled_multivalue=r.unlabelled_multivalue,
        titre_cutoff=r.titre_cutoff,
    )


def build_gt(rep: PrintedReport) -> GTReport:
    pt = rep.patient
    rows: list[GTRow] = []
    for i, r in enumerate(rep.rows, 1):
        a = CATALOGUE[r.key]
        t = row_truth(r, pt)
        pr = r.printed_range
        rows.append(GTRow(
            row_id=f"{rep.report_id}-r{i:03d}", page=r.page, bbox=list(r.bbox), line_text=r.line_text, name=r.name,
            analyte_key=r.key, key_optional=a.key_optional, specimen=a.specimen, section=r.section,
            value_type=r.value_type, value=r.value, value_text=r.value_text, qualifier=r.qualifier,
            text_value=r.text_value, unit=r.unit, unit_placement=r.unit_placement, unit_true=r.unit_true,
            range=GTRange(pr.kind, pr.low, pr.high, " ".join(r.range_lines), [_opt_dict(o) for o in pr.options]),
            effective_range_page=t.effective_range_page, effective_range_true=t.effective_range_true,
            printed_flag=r.printed_flag, flag=t.flag, flag_true=t.flag_true, is_critical=t.is_critical,
            critical_rule=t.critical_rule, canonical=t.canonical or {"value": None, "unit": ""},
            expected_needs_review=t.expected_needs_review, ambiguity=list(t.ambiguity), features=list(r.features),
            previous=list(r.previous),
        ))
    page_rows: dict[int, list[tuple[bool, str]]] = {}
    for g in rows:
        page_rows.setdefault(g.page, []).append((g.is_critical, g.flag_true))
    sections: list[dict[str, Any]] = []
    for g in rows:
        s = next((x for x in sections if x["title"] == g.section), None)
        if s is None:
            s = {"title": g.section, "specimen": g.specimen, "pages": []}
            sections.append(s)
        if g.page not in s["pages"]:
            s["pages"].append(g.page)
    return GTReport(
        report_id=rep.report_id, split=rep.split, family=rep.family, template_id=rep.template_id, seed=rep.seed,
        generator_version=cfg.GENERATOR_VERSION,
        pages=[{"n": p.n, "width_pt": rep.page_size[0], "height_pt": rep.page_size[1]} for p in rep.pages],
        lab=dict(rep.lab),
        patient={"name": pt.name, "patient_id": pt.patient_id, "sex_true": pt.sex_true, "age_true": pt.age_true,
                 "input_sex": pt.input_sex, "input_age": pt.input_age, "age_group": pt.age_group},
        scenario=dict(rep.scenario), unit_system=rep.unit_system, sections=sections, rows=rows,
        decoys=[{"kind": d.kind, "text": d.text, "page": d.page, "bbox": list(d.bbox)} for d in rep.decoys],
        expected={
            "headline": clinical.headline([(g.is_critical, g.flag_true) for g in rows]),
            "per_page_headline": {str(n): clinical.headline(v) for n, v in sorted(page_rows.items())},
            "needs_review_min_set": [g.row_id for g in rows if g.expected_needs_review],
            "critical_rows": [g.row_id for g in rows if g.is_critical],
        },
        schema=GT_SCHEMA,
    )
