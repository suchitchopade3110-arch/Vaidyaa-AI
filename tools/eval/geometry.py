"""Ground-truth bbox to input-file page space for the judge (P2 plan 9.4, T16 F2 completion).

GT-side module: imports nothing from ``app`` or the adapters (GN-11). Pure Python, no PyMuPDF.

GT bboxes are PDF points with a top-left origin, ``[x0, y0, x1, y1]`` (``render.py``). The product reports an
unread line's ``source_bbox`` in the space of the file it was given:

* native PDF: PDF points, the same space as the GT, so the mapping is the identity;
* scan and rot90 (image-only PDF at 150 dpi): the rendering's 3x3 homography ``H`` maps GT points to image pixels,
  and the image fills the PDF page, so points = pixels x 72 / dpi (rot90 pages are already landscape in the meta);
* photo (a PNG per page): pixels, so the homography result is used as is;
* csv: no geometry, ``None``.

Only the vertical extent matters to the judge (a printed line is one band of y), but the whole axis-aligned box is
returned so ``P-BOX`` and tests can see it.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any, Mapping, Sequence

POINTS_PER_INCH = 72.0
DEFAULT_SCAN_DPI = 150.0


def _hom_apply(h: Sequence[Sequence[float]], x: float, y: float) -> tuple[float, float]:
    u = h[0][0] * x + h[0][1] * y + h[0][2]
    v = h[1][0] * x + h[1][1] * y + h[1][2]
    w = h[2][0] * x + h[2][1] * y + h[2][2]
    return u / w, v / w


def _valid_box(b: Any) -> bool:
    return isinstance(b, (list, tuple)) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b)


def gt_bbox_in_input(bbox: Sequence[float] | None, rendering: str, page_meta: Mapping[str, Any] | None
                     ) -> list[float] | None:
    """The GT bbox (PDF points) in the page space of the job's input file; ``None`` when the rendering has none.

    ``page_meta`` is the ``render_meta`` entry of the GT row's page (``homography``, ``dpi``); not needed for native."""
    if not _valid_box(bbox):
        return None
    x0, y0, x1, y1 = (float(v) for v in bbox)
    if rendering == "native":
        return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
    if rendering not in ("scan", "rot90", "photo"):
        return None
    if not page_meta or "homography" not in page_meta:
        raise ValueError(f"no homography for a {rendering} page; the render meta is required")
    h = page_meta["homography"]
    pts = [_hom_apply(h, x, y) for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    k = POINTS_PER_INCH / float(page_meta.get("dpi") or DEFAULT_SCAN_DPI) if rendering in ("scan", "rot90") else 1.0
    return [min(xs) * k, min(ys) * k, max(xs) * k, max(ys) * k]


def vertical_overlap(a: Sequence[float] | None, b: Sequence[float] | None) -> float:
    """Length of the intersection of the y extents of two boxes (0.0 when disjoint, touching or either is absent)."""
    if not _valid_box(a) or not _valid_box(b):
        return 0.0
    lo = max(min(a[1], a[3]), min(b[1], b[3]))
    hi = min(max(a[1], a[3]), max(b[1], b[3]))
    return max(0.0, float(hi - lo))


def render_meta_path(file_path: str, report_id: str, rendering: str) -> pathlib.Path:
    """``<split dir>/render_meta/<report>.<rendering>.json`` for an input file at ``<split dir>/<kind>/<name>``."""
    return pathlib.Path(file_path).parent.parent / "render_meta" / f"{report_id}.{rendering}.json"


def load_render_meta(file_path: str, report_id: str, rendering: str) -> dict[str, Any] | None:
    """``{page_number: page_meta}`` for scan, rot90 and photo; ``None`` for native and csv. A missing file raises."""
    if rendering not in ("scan", "rot90", "photo"):
        return None
    doc = json.loads(render_meta_path(file_path, report_id, rendering).read_text(encoding="utf-8"))
    return {int(p["page"]): p for p in doc.get("pages") or []}


def map_gt_rows(rendering: str, rows: Sequence[Any], pages_meta: Mapping[int, Mapping[str, Any]] | None
                ) -> list[list[float] | None]:
    """One mapped bbox (or ``None``) per GT row, in input-file page space. ``rows`` are GT rows (dict or object)
    with ``page`` and ``bbox``."""
    out: list[list[float] | None] = []
    for g in rows:
        page = g.get("page") if isinstance(g, Mapping) else getattr(g, "page", None)
        bbox = g.get("bbox") if isinstance(g, Mapping) else getattr(g, "bbox", None)
        meta = (pages_meta or {}).get(int(page)) if page is not None else None
        out.append(gt_bbox_in_input(bbox, rendering, meta))
    return out


__all__ = ["gt_bbox_in_input", "load_render_meta", "map_gt_rows", "render_meta_path", "vertical_overlap"]
