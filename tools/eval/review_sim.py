"""The conservative simulated clinician (P1 plan 6.5).

Judge-side module: imports nothing from ``app`` or ``tools.eval.adapters`` (GN-11). It cannot use the adapter's
``ConfirmedRow``, so it builds ``SimRow``s with the same attribute names; every ``Scorer`` reads rows by duck typing
and accepts them (``EngineFloorScorerV0`` reuses an accepted row's ``ParsedRow`` payload and builds fresh rows for
edited and added ones).

The clinician acts only on what the product shows:

| situation                                   | action                                                       |
|---------------------------------------------|--------------------------------------------------------------|
| matched row, needs_review                   | edited: replaced by the GT true values                       |
| matched row, not flagged                    | accepted as extracted, wrong or not                          |
| unmatched extracted row, flagged            | deleted                                                      |
| unmatched extracted row, not flagged        | accepted: the invented row persists                          |
| unread line covering an unmatched GT row    | added from the GT true values                                |
| unread line covering nothing                | dismissed                                                    |
| GT row unmatched and not covered            | missing: the clinician does not notice                       |
| review_all job                              | nothing added; the confirmed set is empty (headline unknown) |
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Sequence

from tools.eval.match import Alignment, field

ACTIONS = ("edited", "accepted", "deleted", "kept_invented", "added", "dismissed", "missing")


@dataclass
class SimRow:
    """A confirmed row; the attribute names are those of the adapter's ``ConfirmedRow``."""

    name_raw: str = ""
    analyte_key: str | None = None
    value_raw: str = ""
    value_num: float | None = None
    qualifier: str | None = None
    text_value: str | None = None
    unit_raw: str = ""
    ref_low: float | None = None
    ref_high: float | None = None
    ref_text: str | None = None
    printed_flag: str | None = None
    specimen: str = "unknown"
    origin: str = "accepted"  # accepted | edited | added
    extracted_index: int | None = None
    payload: Any = dc_field(default=None, repr=False, compare=False)


def accepted_row(e: Any) -> SimRow:
    """The extracted row as it stands (``ConfirmedRow.from_extracted`` semantics)."""
    return SimRow(
        name_raw=field(e, "name_raw") or "", analyte_key=field(e, "analyte_key"),
        value_raw=field(e, "value_raw") or "", value_num=field(e, "value_num"), qualifier=field(e, "qualifier"),
        text_value=field(e, "text_value"), unit_raw=field(e, "unit_raw") or "", ref_low=field(e, "ref_low"),
        ref_high=field(e, "ref_high"), ref_text=field(e, "ref_text"), printed_flag=field(e, "printed_flag"),
        specimen=field(e, "specimen") or "unknown", origin="accepted", extracted_index=field(e, "row_index"),
        payload=field(e, "payload"),
    )


def true_row(g: Any, origin: str = "edited", extracted_index: int | None = None) -> SimRow:
    """The GT true values: name, key, value, qualifier, text, ``unit_true``, ``effective_range_true``, specimen.

    The printed flag is not carried (the confirmed value is right by construction)."""
    rng = field(g, "effective_range_true") or {}
    printed_range = field(g, "range") or {}
    value_text = field(g, "value_text") or ""
    qualifier = field(g, "qualifier") or None
    return SimRow(
        name_raw=field(g, "name") or "", analyte_key=field(g, "analyte_key"),
        value_raw=f"{qualifier or ''}{value_text}" if field(g, "value") is not None else value_text,
        value_num=field(g, "value"), qualifier=qualifier, text_value=field(g, "text_value"),
        unit_raw=field(g, "unit_true") or "", ref_low=rng.get("low"), ref_high=rng.get("high"),
        ref_text=field(printed_range, "text") or None, printed_flag=None, specimen=field(g, "specimen") or "unknown",
        origin=origin, extracted_index=extracted_index,
    )


@dataclass
class Review:
    rows: list[SimRow]
    sources: list[int | None]  # per confirmed row: the GT index it stands for (None: an accepted invention)
    actions: dict[str, int]
    review_all: bool = False

    def row_for_gt(self, gi: int) -> int | None:
        """Index of the confirmed row that stands for GT row ``gi`` (None when it is missing or deleted)."""
        return next((i for i, s in enumerate(self.sources) if s == gi), None)


def simulate(gt_rows: Sequence[Any], extracted: Sequence[Any], unread: Sequence[Any], alignment: Alignment,
             review_all: bool = False) -> Review:
    """Apply the conservative clinician to one job. ``alignment`` is ``match.align(gt_rows, extracted, unread)``.

    Confirmed rows keep the extracted order (accepted and edited rows), followed by added rows in GT order."""
    actions = dict.fromkeys(ACTIONS, 0)
    if review_all:
        actions["missing"] = len(gt_rows)
        actions["dismissed"] = len(unread)
        return Review([], [], actions, True)
    g_of = {p.e: p.g for p in alignment.pairs}
    rows: list[SimRow] = []
    sources: list[int | None] = []
    for ei, e in enumerate(extracted):
        gi = g_of.get(ei)
        flagged = bool(field(e, "needs_review"))
        if gi is not None:
            if flagged:
                rows.append(true_row(gt_rows[gi], "edited", field(e, "row_index")))
                actions["edited"] += 1
            else:
                rows.append(accepted_row(e))
                actions["accepted"] += 1
            sources.append(gi)
        elif flagged:
            actions["deleted"] += 1
        else:
            rows.append(accepted_row(e))
            sources.append(None)
            actions["kept_invented"] += 1
    for gi in sorted(alignment.covered):
        rows.append(true_row(gt_rows[gi], "added"))
        sources.append(gi)
        actions["added"] += 1
    actions["dismissed"] = len(unread) - len(alignment.unread_used)
    actions["missing"] = len(alignment.unmatched_gt) - len(alignment.covered)
    return Review(rows, sources, actions, False)


def corrected_rows(gt_rows: Sequence[Any]) -> list[SimRow]:
    """Every GT row with its true values (M5-oracle: isolates scoring errors from extraction errors)."""
    return [true_row(g, "edited") for g in gt_rows]


__all__ = ["ACTIONS", "Review", "SimRow", "accepted_row", "corrected_rows", "simulate", "true_row"]
