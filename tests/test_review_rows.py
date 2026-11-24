"""Review row tests RW-01..RW-14 (plans/P2.md sections 4.4, 7.2, 12.3).

Expectations are hand-derived: the accepted input forms come from the plan's API contract, the clinical outcomes
(NORMAL / UNKNOWN) from the printed ranges in each case.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pytest

from app.services import ocr_service
from app.services.lab_engine import analyze_lab_rows, parse_lab_text, parse_report_text
from app.services.lab_engine.models import ParsedRow
from app.services.review.rows import (
    UNSET, RowData, RowInputError, ValueInput, acceptance_errors, apply_row_edit, build_manual_row,
    deserialize_parsed, next_status, parse_value_input, serialize_parsed, value_raw_from_parsed,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = sorted((ROOT / "tests" / "fixtures" / "lab_cases").glob("*.txt"))
GOLDEN = sorted((ROOT / "tests" / "fixtures" / "eval_golden").glob("*.native.pdf"))


def _all_rows():
    mp = pytest.MonkeyPatch()
    mp.setattr(ocr_service, "ocr_image", lambda image: [])
    rows = []
    try:
        from app.services.preprocessor import run_ocr
        texts = [f.read_text(errors="replace") for f in FIXTURES] + [run_ocr(str(p)).raw_text for p in GOLDEN]
    finally:
        mp.undo()
    for t in texts:
        rows.extend(parse_report_text(t)[0])
    return rows


def test_rw01_payload_round_trip_scores_identically():
    rows = _all_rows()
    assert len(rows) > 100
    kinds = {bool(r.sex_ranges) for r in rows} | {bool(r.range_options) for r in rows} | {r.result_range is not None for r in rows}
    assert True in kinds                                   # the corpus exercises the structured fields
    for r in rows:
        again = deserialize_parsed(json.loads(json.dumps(serialize_parsed(r))))
        assert again == r
        a = analyze_lab_rows([r], "male", 45)
        b = analyze_lab_rows([again], "male", 45)
        assert [(x.flag, x.ref_low, x.ref_high, x.value, x.unit, x.analyte_key) for x in a.rows] == \
               [(x.flag, x.ref_low, x.ref_high, x.value, x.unit, x.analyte_key) for x in b.rows]


@pytest.mark.parametrize("text, num, qual, txt, rng", [
    ("6.9", 6.9, None, None, None),                      # RW-02
    ("<0.01", 0.01, "<", None, None),                    # RW-03
    ("≥ 5", 5.0, ">=", None, None),                      # RW-04
    ("2-3", 3.0, None, None, (2.0, 3.0)),                # RW-05: microscopy range, the upper bound is the value
    ("1:160", None, None, "1:160", None),                # RW-06
    ("Reactive", None, None, "Reactive", None),          # RW-07
    ("<= 7.5", 7.5, "<=", None, None),
    ("-4.2", -4.2, None, None, None),
])
def test_rw02_to_07_parse_value_input(text, num, qual, txt, rng):
    v = parse_value_input(text)
    assert (v.value_num, v.qualifier, v.text_value, v.result_range) == (num, qual, txt, rng)


@pytest.mark.parametrize("bad", ["4,2", "2,50,000", "1,5", "1e12", "inf", "---", "#"])
def test_rw08_invalid_values_are_422_with_a_code(bad):
    with pytest.raises(RowInputError) as e:
        parse_value_input(bad)
    assert e.value.code == "INVALID_VALUE"


def test_empty_value_is_value_required():
    with pytest.raises(RowInputError) as e:
        parse_value_input("  ")
    assert e.value.code == "VALUE_REQUIRED"


def _row(**kw):
    base = dict(name="Serum Potassium", value=4.2, unit="mmol/L", ref_low=3.5, ref_high=5.1, ref_text="3.5 - 5.1",
                line="Serum Potassium 4.2 mmol/L 3.5 - 5.1", warnings=["x: something"], note="current column")
    base.update(kw)
    return ParsedRow(**base)


def test_rw09_value_edit_clears_candidates_ambiguity_and_note():
    p = ParsedRow(name="Serum Potassium", value=None, candidates=[4.2, 5.1, 6.7], ambiguity="several numbers",
                  unit="mmol/L", ref_low=3.5, ref_high=5.1, note="n")
    q = apply_row_edit(p, value_raw="4.2")
    assert (q.value, q.candidates, q.ambiguity, q.note) == (4.2, None, None, "")
    assert p.candidates == [4.2, 5.1, 6.7]                 # the input is never mutated


def test_rw10_range_edit_clears_sex_age_ranges_and_options():
    # Hb 14.0 with printed "Male: 13.0-17.0 Female: 12.0-15.5" and the patient's sex unknown cannot be judged.
    rows, _ = parse_lab_text("Haemoglobin 14.0 g/dL Male: 13.0 - 17.0 Female: 12.0 - 15.5")
    p = rows[0]
    assert p.sex_ranges, "precondition: sex-specific range was read"
    assert analyze_lab_rows([p], None, 45).rows[0].flag == "UNKNOWN"
    q = apply_row_edit(p, ref_low=13.0, ref_high=17.0)
    assert q.sex_ranges is None and q.age_ranges is None and q.range_options == [] and not q.interpretive_only
    assert (q.ref_low, q.ref_high, q.ref_text) == (13.0, 17.0, "13 - 17")
    assert analyze_lab_rows([q], None, 45).rows[0].flag == "NORMAL"       # 14.0 is inside 13.0-17.0


def test_range_text_edit_derives_numbers_and_strictness():
    q = apply_row_edit(_row(ref_low=None, ref_high=None, ref_text=None), ref_text="< 5.7")
    assert (q.ref_low, q.ref_high, q.ref_strict_high, q.ref_text) == (None, 5.7, True, "< 5.7")
    q = apply_row_edit(_row(), ref_low=None, ref_high=5.7, ref_text=UNSET)
    assert (q.ref_low, q.ref_high) == (None, 5.7)


@pytest.mark.parametrize("lo, hi", [(5.0, 4.0), (float("nan"), 3.0), (1e12, None)])
def test_invalid_range_is_422(lo, hi):
    with pytest.raises(RowInputError) as e:
        apply_row_edit(_row(), ref_low=lo, ref_high=hi)
    assert e.value.code == "INVALID_RANGE"


def test_rw11_unit_edit_clears_ref_unit():
    p = _row(ref_unit="mg/dL")
    q = apply_row_edit(p, unit_raw="mmol/L")
    assert q.unit == "mmol/L" and q.ref_unit is None
    assert apply_row_edit(p, unit_raw="  ").unit is None


def test_rw12_any_edit_clears_warnings_and_untouched_keeps_them():
    p = _row()
    assert apply_row_edit(p, printed_flag="H").warnings == []
    assert apply_row_edit(p).warnings == ["x: something"]


def test_name_flag_specimen_validation():
    for bad_name in ("", "   ", "x" * 121):
        with pytest.raises(RowInputError) as e:
            apply_row_edit(_row(), name_raw=bad_name)
        assert e.value.code == "NAME_REQUIRED"
    with pytest.raises(RowInputError) as e:
        apply_row_edit(_row(), printed_flag="XX")
    assert e.value.code == "INVALID_FLAG"
    with pytest.raises(RowInputError) as e:
        apply_row_edit(_row(), specimen="plasma")
    assert e.value.code == "INVALID_SPECIMEN"
    with pytest.raises(RowInputError) as e:
        apply_row_edit(_row(), unit_raw="u" * 33)
    assert e.value.code == "INVALID_UNIT"
    assert apply_row_edit(_row(), printed_flag="").printed_flag is None


def test_structured_value_is_not_reparsed():
    # decimal-comma ground truth arrives as a number; "4,2" text would be refused
    q = apply_row_edit(_row(), value=ValueInput("4,2", value_num=4.2))
    assert q.value == 4.2


def test_manual_row_requires_specimen_and_value():
    p = build_manual_row(name_raw="Serum Calcium", value_raw="15.1", unit_raw="mg/dL", specimen="serum")
    assert (p.name, p.value, p.unit, p.source, p.specimen) == ("Serum Calcium", 15.1, "mg/dL", "manual", "serum")
    for spec in (None, "unknown", "bogus"):
        with pytest.raises(RowInputError) as e:
            build_manual_row(name_raw="Serum Calcium", value_raw="15.1", specimen=spec)
        assert e.value.code == "SPECIMEN_REQUIRED"
    with pytest.raises(RowInputError) as e:
        build_manual_row(name_raw="Serum Calcium", specimen="serum")
    assert e.value.code == "VALUE_REQUIRED"
    q = build_manual_row(name_raw="Hb", value_raw="9.1", ref_low=13.0, ref_high=17.0, specimen="serum")
    assert (q.ref_low, q.ref_high) == (13.0, 17.0)


def _rd(**kw):
    return RowData(**{**dict(name_raw="Total Leucocyte Count", value_num=7.2, review_codes=[], specimen="serum"), **kw})


def test_rw13_acceptance_errors():
    assert acceptance_errors(_rd()) == []
    # S6: 7.2 is normal in x10^3/uL and impossible in /uL, so a unit must be supplied before it is accepted
    assert acceptance_errors(_rd(review_codes=["NR-UNIT"], unit_raw=None)) == ["UNIT_REQUIRED"]
    assert acceptance_errors(_rd(review_codes=["NR-UNIT"], unit_raw="x10^3/uL")) == []
    assert acceptance_errors(_rd(review_codes=["NR-SPECIMEN"], specimen="unknown")) == ["SPECIMEN_REQUIRED"]
    assert acceptance_errors(_rd(review_codes=["NR-SPECIMEN"], specimen="urine")) == []
    assert acceptance_errors(_rd(value_num=None, text_value=None)) == ["VALUE_REQUIRED"]
    assert acceptance_errors(_rd(value_num=None, text_value="Reactive")) == []
    assert acceptance_errors(_rd(value_num=None, review_codes=["NR-UNIT"], specimen="unknown")) == ["VALUE_REQUIRED", "UNIT_REQUIRED"]


# The plan's 4.4 table, written out by hand: (kind, current) -> {requested: result}; absent = refused.
TABLE = {
    ("row", "pending"): {"accepted": "accepted", "deleted": "deleted"},
    ("unread_line", "pending"): {"deleted": "deleted"},
    ("row", "accepted"): {"deleted": "deleted", "pending": "pending"},
    ("row", "edited"): {"deleted": "deleted"},
    ("row", "added"): {"deleted": "deleted"},
    ("row", "deleted"): {"pending": "pending"},
    ("unread_line", "deleted"): {"pending": "pending"},
}


@pytest.mark.parametrize("kind, current, requested",
                         [(k, c, r) for (k, c), r in itertools.product(TABLE, ("accepted", "deleted", "pending", "edited", "bogus"))])
def test_rw14_every_cell_of_the_transition_table(kind, current, requested):
    expected = TABLE[(kind, current)].get(requested)
    if expected is None:
        with pytest.raises(RowInputError) as e:
            next_status(kind=kind, current=current, requested=requested)
        assert e.value.code == "INVALID_TRANSITION"
    else:
        assert next_status(kind=kind, current=current, requested=requested) == expected


def test_rw14_restore_destinations_and_acceptance_gate():
    assert next_status(kind="row", current="deleted", requested="pending", has_field_edits=True) == "edited"
    assert next_status(kind="row", current="deleted", requested="pending", manual=True) == "added"
    assert next_status(kind="row", current="deleted", requested="pending") == "pending"
    with pytest.raises(RowInputError) as e:
        next_status(kind="row", current="pending", requested="accepted", errors=["UNIT_REQUIRED"])
    assert e.value.code == "UNIT_REQUIRED"


def test_value_raw_from_parsed():
    assert value_raw_from_parsed(ParsedRow(name="x", value=0.01, qualifier="<")) == "<0.01"
    assert value_raw_from_parsed(ParsedRow(name="x", value=6.9)) == "6.9"
    assert value_raw_from_parsed(ParsedRow(name="x", value=3.0, result_range=(2.0, 3.0))) == "2-3"
    assert value_raw_from_parsed(ParsedRow(name="x", text_value="Reactive")) == "Reactive"
