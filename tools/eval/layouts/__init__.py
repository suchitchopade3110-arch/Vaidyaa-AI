"""Layout family registry mapping family ids to kind, scope and builder (P1 plan section 2.1).

Only IN-A, IN-B, IN-C and US-A exist so far (T05). IN-D, IN-E, UK-A, GEN-CSV (T06), G01-G10 (T07) and the
heldout-only families (T08) register themselves here by adding an entry; a builder has the signature
``build(family, *, split, index, patient, scenario_class, target_pages, report_id=None) -> PrintedReport``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tools.eval.layouts import templates


@dataclass(frozen=True)
class FamilyEntry:
    kind: str  # template | generic
    scope: str  # shared | heldout_only
    builder: Callable[..., Any]


FAMILIES: dict[str, FamilyEntry] = {
    fid: FamilyEntry("template", "shared", templates.build_report) for fid in templates.SPECS
}
