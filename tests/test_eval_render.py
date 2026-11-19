"""GN-04 (rendering determinism) and GN-09 (rendering bounds) for tools/eval/render.py (P1 plan sections 5, 8, 11).

Expectations are hand-derived: A4 is 595.28 x 841.89 pt = 8.27 x 11.69 in, so 150 dpi gives about 1240 x 1754 px;
the PRD caps the photo tilt at 8 degrees, blur at sigma 1.2, JPEG quality 60-85, PNG < 10 MB, long side 1800 px;
rot90 is 5% of reports.
"""

from __future__ import annotations

import hashlib
import io
import math
import random

import fitz
import numpy as np
import pytest
from PIL import Image

from tools.eval import render as rd
from tools.eval import scenarios as sc
from tools.eval.layouts import templates as tpl

FAMILIES = ("IN-A", "IN-B", "IN-C", "US-A")
SEED = 4242


def _sha(path) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _patients():
    return sc.build_patients(80, random.Random(7))


def _report(fam: str, pages: int = 2):
    return tpl.build_report(fam, split="dev", index=0, patient=_patients()[0], scenario_class="mild", target_pages=pages)


def _render_all(rep, d, tag):
    nat = str(d / f"{tag}_native.pdf")
    rd.native_pdf(rep, nat)
    scan = str(d / f"{tag}_scan.pdf")
    rot = str(d / f"{tag}_rot90.pdf")
    meta = {"scan": rd.render_scan(nat, scan, SEED), "rot90": rd.render_scan(nat, rot, SEED + 1, rot90=90),
            "photo": rd.render_photo(nat, str(d / f"{tag}_photo"), SEED + 2)}
    files = [nat, scan, rot, *meta["photo"]["files"]]
    return nat, scan, rot, meta, files


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    d = tmp_path_factory.mktemp("render")
    rep = _report("IN-A", 2)
    return (rep, d, *_render_all(rep, d, "a"))


def _page_image(pdf, i):
    with fitz.open(pdf) as doc:
        xref = doc[i].get_images(full=True)[0][0]
        return np.asarray(Image.open(io.BytesIO(doc.extract_image(xref)["image"])).convert("L"))


# ------------------------------------------------------------------ GN-04

@pytest.mark.parametrize("fam", FAMILIES)
def test_gn04_all_renderings_are_byte_identical_across_runs(fam, tmp_path):
    rep = _report(fam, 2)
    (tmp_path / "r1").mkdir()
    (tmp_path / "r2").mkdir()
    *_, f1 = _render_all(rep, tmp_path / "r1", "x")
    *_, f2 = _render_all(rep, tmp_path / "r2", "x")
    assert len(f1) == len(f2) == 3 + 2  # native, scan, rot90, two photo pages
    assert [_sha(a) for a in f1] == [_sha(b) for b in f2]


def test_gn04_different_seed_changes_the_degradation(tmp_path, rendered):
    rep, d, nat, *_ = rendered
    rd.render_scan(nat, str(tmp_path / "s.pdf"), SEED + 99)
    assert _sha(tmp_path / "s.pdf") != _sha(rendered[3])


# ------------------------------------------------------------------ GN-09

def test_gn09_scan_is_image_only_at_150_dpi(rendered):
    rep, d, nat, scan, rot, meta, files = rendered
    with fitz.open(nat) as n, fitz.open(scan) as s:
        assert len(s) == len(n) == 2
        for pn, ps in zip(n, s, strict=True):
            assert ps.get_text().strip() == ""
            assert abs(ps.rect.width - pn.rect.width) < 0.01 and abs(ps.rect.height - pn.rect.height) < 0.01
            imgs = ps.get_images(full=True)
            assert len(imgs) == 1
            w, h = imgs[0][2], imgs[0][3]
            # hand derivation: px = inches x 150
            assert abs(w - pn.rect.width / 72 * 150) <= 1.0
            assert abs(h - pn.rect.height / 72 * 150) <= 1.0
    for pm in meta["scan"]["pages"]:
        p = pm["params"]
        assert abs(p["skew_deg"]) <= 1.0 and abs(p["brightness"]) <= 0.05 and abs(p["contrast"]) <= 0.05
        assert 2 <= p["noise_sigma"] <= 6 and 0.0002 <= p["speckle_density"] <= 0.001
        assert 0 <= p["blur_sigma"] <= 0.6 and 70 <= p["jpeg_q"] <= 90
        assert pm["dpi"] == 150


def test_gn09_scan_is_actually_degraded_but_legible(rendered):
    rep, d, nat, scan, *_ = rendered
    img = _page_image(scan, 0)
    assert img.dtype == np.uint8 and img.mean() > 200  # mostly white paper
    with fitz.open(nat) as doc:
        clean = np.frombuffer(
            doc[0].get_pixmap(matrix=fitz.Matrix(150 / 72, 150 / 72), colorspace=fitz.csGRAY).samples, np.uint8)
    assert not np.array_equal(clean[: img.size], img.reshape(-1)[: clean.size])  # noise was applied


def test_gn09_rot90_pages_are_landscape_image_only(rendered):
    rep, d, nat, scan, rot, meta, files = rendered
    with fitz.open(nat) as n, fitz.open(rot) as r:
        for pn, pr in zip(n, r, strict=True):
            assert pr.rect.width > pr.rect.height  # A4 portrait became landscape
            assert abs(pr.rect.width - pn.rect.height) < 0.01
            assert pr.get_text().strip() == ""
            w, h = pr.get_images(full=True)[0][2:4]
            assert w > h and abs(w - pn.rect.height / 72 * 150) <= 1.0


def _my_tilt(hom, f, size):
    """Independent plane-tilt measurement: K^-1 H = s [r1 r2 t]; tilt = angle between r1 x r2 and the optical axis."""
    k = np.array([[f, 0, size[0] / 2], [0, f, size[1] / 2], [0, 0, 1.0]])
    m = np.linalg.inv(k) @ np.array(hom)
    a, b = m[:, 0], m[:, 1]
    assert abs(np.linalg.norm(a) - np.linalg.norm(b)) / np.linalg.norm(a) < 0.02  # similar scale: valid rotation
    assert abs(np.dot(a, b)) / (np.linalg.norm(a) * np.linalg.norm(b)) < 0.02  # orthogonal columns
    n = np.cross(a, b)
    return math.degrees(math.acos(abs(n[2]) / np.linalg.norm(n)))


def test_gn09_photo_bounds(rendered):
    rep, d, nat, scan, rot, meta, files = rendered
    pm = meta["photo"]["pages"]
    assert len(pm) == 2 and len(meta["photo"]["files"]) == 2
    for page, path in zip(pm, meta["photo"]["files"], strict=True):
        p = page["params"]
        im = Image.open(path)
        assert im.format == "PNG" and im.mode == "RGB"
        assert max(im.size) == 1800
        assert im.size == tuple(page["size_px"])
        import os

        assert os.path.getsize(path) < 10 * 1024 * 1024
        tilt = _my_tilt(page["homography"], p["focal_px"], page["size_px"])
        assert tilt <= 8.0 + 1e-6
        assert abs(tilt - p["tilt_deg"]) < 0.05  # recorded parameter agrees with the measured geometry
        assert abs(p["inplane_deg"]) <= 3.0 and 1.0 <= p["focal_mult"] <= 1.3
        assert 0.0 <= p["blur_sigma"] <= 1.2
        assert 60 <= p["jpeg_q"] <= 85
        assert 1.0 <= p["noise_sigma"] <= 4.0
        assert 0.65 <= p["gradient_min"] <= 0.9 and 0.8 <= p["vignette_min"] <= 1.0
        assert all(abs(c) <= 0.05 for c in p["colour_cast"])


def test_gn09_photo_parameter_bounds_over_many_seeds(rendered, tmp_path):
    rep, d, nat, *_ = rendered
    tilts, blurs, qs, shadow = [], [], [], 0
    for s in range(15):
        m = rd.render_photo(nat, str(tmp_path / f"p{s}"), 1000 + s)
        for page in m["pages"]:
            p = page["params"]
            tilts.append(rd.photo_tilt_deg(page["homography"], p["focal_px"], page["size_px"]))
            blurs.append(p["blur_sigma"])
            qs.append(p["jpeg_q"])
            shadow += p["shadow"]
    assert max(tilts) <= 8.0 and max(blurs) <= 1.2 and 60 <= min(qs) and max(qs) <= 85
    assert max(tilts) > 5.0 and max(blurs) > 0.8 and min(qs) < 68 and max(qs) > 77  # the ranges are really used
    assert 0.1 < shadow / 30 < 0.5  # about 30%


def test_gn09_rot90_fraction_is_five_percent_and_balanced():
    fams = [f"F{i:02d}" for i in range(18)]
    for n, want in ((220, 11), (80, 4), (76, 4), (60, 3)):
        items = [(f"R{i:03d}", fams[i % 18]) for i in range(n)]
        sel = rd.choose_rot90(items, seed=11)
        assert len(sel) == want == int(n * 0.05 + 0.5)
        assert set(sel.values()) <= {90, -90}
        assert len({dict(items)[r] for r in sel}) == len(sel)  # stratified: no family repeated while others remain
        assert sel == rd.choose_rot90(items, seed=11)  # deterministic
    dirs = {v for s in range(30) for v in rd.choose_rot90([(f"R{i}", fams[i % 18]) for i in range(220)], s).values()}
    assert dirs == {90, -90}
    assert rd.choose_rot90([(f"R{i}", fams[i % 18]) for i in range(220)], 1) != \
        rd.choose_rot90([(f"R{i}", fams[i % 18]) for i in range(220)], 2)


# ------------------------------------------------------------------ bbox mapping lands on printed text

def _dark_fraction(arr, box, rel=0.75):
    x0, y0, x1, y1 = (int(round(v)) for v in box)
    h, w = arr.shape[:2]
    assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h, box  # mapped box lies inside the image
    crop = arr[y0:y1, x0:x1]
    crop = crop.mean(axis=2) if crop.ndim == 3 else crop
    ref = np.percentile(crop, 90)
    return float((crop < rel * ref).mean())


def _scale_box_ok(rep, hom, arr, page):
    fr = []
    for row in rep.rows:
        if row.page != page:
            continue
        box = rd.map_bbox(row.bbox, hom)
        fr.append(_dark_fraction(arr, box))
    return fr


@pytest.mark.parametrize("kind", ["scan", "rot90", "photo"])
def test_bbox_mapping_lands_on_printed_text(rendered, kind):
    rep, d, nat, scan, rot, meta, files = rendered
    checked = 0
    for pi in (1, 2):
        pm = meta[kind]["pages"][pi - 1]
        if kind == "photo":
            arr = np.asarray(Image.open(meta["photo"]["files"][pi - 1]))
        else:
            arr = _page_image(scan if kind == "scan" else rot, pi - 1)
        fr = _scale_box_ok(rep, pm["homography"], arr, pi)
        checked += len(fr)
        assert fr and min(fr) > 0.01, (kind, pi, min(fr))  # every row box contains ink
        assert np.median(fr) > 0.02  # printed text is typically 3-8% dark pixels in a row box
        # the same boxes moved 20 px sideways-and-down (off the text) hold clearly less ink on average
        off = [_dark_fraction(arr, [b + 0 for b in rd.map_bbox([r.bbox[0], r.bbox[1], r.bbox[0] + 8, r.bbox[3]], pm['homography'])])
               for r in rep.rows if r.page == pi]
        assert np.mean(off) < np.mean(fr)
    assert checked == len(rep.rows)


def test_homography_scan_is_pt_to_150dpi_up_to_skew(rendered):
    rep, d, nat, scan, rot, meta, files = rendered
    h = np.array(meta["scan"]["pages"][0]["homography"])
    s = 150 / 72
    assert abs(math.hypot(h[0, 0], h[1, 0]) - s) < 1e-9  # pure scale + rotation, no shear or perspective
    assert abs(h[2, 0]) < 1e-12 and abs(h[2, 1]) < 1e-12 and h[2, 2] == 1.0
    rot_h = np.array(meta["rot90"]["pages"][0]["homography"])
    assert abs(abs(np.linalg.det(rot_h[:2, :2])) - s * s) < 1e-6
