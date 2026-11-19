"""GEN-CSV: LIS CSV export layout family (P1 plan sections 3.2, 3.7, 4.1, 5).

The rows are the same ``PrintedRow`` objects the PDF families use (so GT derivation is shared); this module only
serialises them as a text export. The CSV is carried in the report as ``pages[0]``: one ``TextOp`` per file line
(``x=0``, ``y`` = zero-based line number), so ``PrintedReport.all_text()`` is the file content and the deny-list
scan covers it. Row ``bbox`` is ``[0, line, len(line), line + 1]`` in CHARACTER units (there is no page geometry);
``page_size`` is ``(0, 0)`` and ``n_pages`` is 1 (plan 3.1: GEN-CSV counts as 1 page).

Dialects (5 headers, delimiters ``,`` ``;`` (with decimal comma) and tab, split low/high columns, a combined
"Result (unit)" column). 2-4 metadata lines precede the header and are GT decoys of kind ``csv_metadata``.
GT-side module: imports nothing from ``app``.
"""

from __future__ import annotations

import csv
import io
import random
from dataclasses import dataclass

from tools.eval import scenarios as sc
from tools.eval.layouts import base, templates
from tools.eval.layouts.base import PageLayout, PrintedReport, PrintedRow, TextOp
from tools.eval.scenarios import Patient


@dataclass(frozen=True)
class Dialect:
    name: str
    delimiter: str
    decimal_comma: bool
    header: tuple[str, ...]
    split_range: bool  # separate low / high columns
    combined_unit: bool  # "Result (unit)" column holds "13.5 g/dL"; no unit column
    has_flag: bool


DIALECTS: tuple[Dialect, ...] = (
    Dialect("comma_basic", ",", False, ("Test Name", "Result", "Unit", "Reference Range", "Flag"), False, False, True),
    Dialect("semicolon_decimal_comma", ";", True, ("Parameter", "Value", "Unit", "Normal Range", "Flag"), False, False,
            True),
    Dialect("tab_split", "\t", False, ("TestName", "ResultValue", "ResultUnit", "RefLow", "RefHigh", "AbnormalFlag"),
            True, False, True),
    Dialect("comma_result_unit_split", ",", False, ("Analyte", "Result (unit)", "Ref Low", "Ref High", "Flag"), True,
            True, True),
    Dialect("semicolon_result_unit", ";", True, ("Investigation", "Result (unit)", "Reference Interval"), False, True,
            False),
)


def _comma(text: str) -> str:
    return text.replace(".", ",")


def _range_cells(r: PrintedRow, d: Dialect) -> list[str]:
    """Range cell(s) for the dialect. Split columns: two-sided -> low, high; upper -> '', high; lower -> low, ''."""
    text = "; ".join(r.range_lines)
    if d.decimal_comma:
        text = _comma(text)
    if not d.split_range:
        return [text]
    kind = r.printed_range.kind
    if kind == "two_sided":
        lo, _, hi = text.partition(" - ")
        return [lo, hi]
    if kind == "upper":
        return ["", text.split(" ", 1)[1]]
    if kind == "lower":
        return [text.split(" ", 1)[1], ""]
    return [text, ""]  # sex- or age-labelled: the full labelled text sits in the low cell


def _row_cells(r: PrintedRow, d: Dialect) -> list[str]:
    val = f"{r.qualifier} {r.value_text}" if r.qualifier else r.value_text
    flag = [r.printed_flag] if d.has_flag else []
    rng = _range_cells(r, d)
    if d.combined_unit:
        return [r.name, f"{val} {r.unit}".strip(), *rng, *flag]
    return [r.name, val, r.unit, *rng, *flag]


def _metadata(rng: random.Random, lay: templates.Lay, pt: Patient, report_id: str) -> list[list[str]]:
    ident = lay.ident
    opts = [["Lab", ident.lab_name], ["Lab No", ident.lab_no], ["Patient ID", pt.patient_id],
            ["Collected", lay.collected], ["Export ID", f"EXP{rng.randint(100000, 999999)}"]]
    if pt.input_age is not None and pt.input_sex is not None:
        opts.append(["Age/Sex", f"{pt.input_age}/{pt.input_sex.capitalize()}"])
    keep = rng.sample(opts, rng.randint(2, 4))
    return [o for o in opts if o in keep]


def _line(cells: list[str], d: Dialect) -> str:
    buf = io.StringIO()
    csv.writer(buf, delimiter=d.delimiter, lineterminator="").writerow(cells)
    return buf.getvalue()


def build_report(family: str, *, split: str, index: int, patient: Patient, scenario_class: str, target_pages: int,
                 report_id: str | None = None) -> PrintedReport:
    """Build a GEN-CSV report. ``target_pages`` is ignored (a CSV is one page)."""
    spec = templates.SPECS[family]
    rid = report_id or f"{split[0].upper()}{index + 1:03d}"
    spec2, lay, seed = templates.setup(spec, split=split, index=index, target_pages=1)
    drng = random.Random(seed("layout") + 31337)
    d = DIALECTS[drng.randrange(len(DIALECTS))]
    ident = lay.ident
    for attempt in range(templates.MAX_ATTEMPTS):
        srng = random.Random(seed("scenario") + attempt)
        vrng = random.Random(seed("values") + attempt)
        gen0 = templates._Gen(spec2, lay, patient, scenario_class, vrng, sc.ScenarioSpec(scenario_class))
        plan = sc.plan_scenario(scenario_class, srng, patient, gen0.candidates())
        gen = templates._Gen(spec2, lay, patient, scenario_class, vrng, plan)
        rows = gen.run()
        if len(rows) < 8:
            continue
        n_min = len({id(r) for r in rows if r.state in ("mild", "severe", "critical")})
        sel = templates._select(rows, vrng.randint(max(n_min, 8), len(rows)))
        if not templates._headline_ok(scenario_class, sel, patient):
            continue
        break
    else:
        raise RuntimeError(f"{family}: could not build {rid} (class {scenario_class})")

    for r in sel:  # dialect-specific printing of the same rows
        if not d.has_flag:
            r.printed_flag = ""
            r.features = [f for f in r.features if f != "flag_column"]
        if d.decimal_comma:
            r.value_text = _comma(r.value_text)
            r.features.append("decimal_comma")
        if d.combined_unit and r.unit:
            r.unit_placement = "inline"
        if d.split_range:
            r.features.append("split_range_columns")

    lines: list[str] = []
    decoys: list[base.Decoy] = []
    for cells in _metadata(random.Random(seed("layout") + 4242), lay, patient, rid):
        text = _line(cells, d)
        decoys.append(base.Decoy("csv_metadata", text, 1, [0.0, float(len(lines)), float(len(text)),
                                                           float(len(lines) + 1)]))
        lines.append(text)
    lines.append(_line(list(d.header), d))
    for r in sel:
        cells = _row_cells(r, d)
        text = _line(cells, d)
        r.page = 1
        r.bbox = [0.0, float(len(lines)), float(len(text)), float(len(lines) + 1)]
        r.line_text = " | ".join(cells)
        lines.append(text)
    page = PageLayout(1, [TextOp(0.0, float(i), t, base.FONT_MONO, 10.0) for i, t in enumerate(lines)])
    feats = sorted({f for r in sel for f in r.features} | {f"csv_dialect_{d.name}"})
    return PrintedReport(
        report_id=rid, split=split, family=family, seed=seed("plan"), page_size=(0.0, 0.0),
        lab={"name": ident.lab_name, "address": ident.address, "phone": ident.phone, "fictional": True},
        patient=patient, scenario={"class": scenario_class, "clusters": list(plan.clusters)},
        unit_system="SI" if any("si" in r.features for r in sel) else "conventional", rows=sel, decoys=decoys,
        pages=[page], features=feats, template_id=spec.template_id,
    )


def csv_text(report: PrintedReport) -> str:
    """The exported file content (UTF-8 text, ``\\n`` line ends, trailing newline)."""
    return "\n".join(op.text for op in report.pages[0].ops if isinstance(op, TextOp)) + "\n"


def write_csv(report: PrintedReport, path: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(csv_text(report))
