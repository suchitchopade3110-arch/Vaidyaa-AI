"""Renderings of generated reports: native, scan, photo and rot90 (P1 plan sections 5 and 8).

GT-side module: imports nothing from ``app``. Degradations use numpy + Pillow only (no cv2); PyMuPDF only
rasterises the native PDF and wraps the scan image into an image-only PDF. All randomness comes from
``numpy.random.Generator(PCG64(SeedSequence([seed, page])))`` or ``random.Random(seed)``; nothing reads the
clock, so every output is byte-deterministic for a given seed in one environment.

Coordinates: GT bboxes are PDF points with a top-left origin (``[x0, y0, x1, y1]``). ``render_meta`` stores, per
output page, a 3x3 homography ``H`` mapping homogeneous point coordinates ``(x_pt, y_pt, 1)`` to the pixel space
of the output image (pixel edges at integers, so pixel ``i`` covers ``[i, i+1)``). ``map_bbox`` applies it.

Deliberate tightening versus the plan text: the photo tilt is sampled as ONE out-of-plane angle ``m`` in
[0, 8] degrees about an in-plane axis of random direction (instead of two independent angles in [-8, 8]), so the
true plane tilt, measured from the homography, never exceeds the PRD's 8 degrees.
"""

from __future__ import annotations

import io
import math
import random
from typing import Any

import fitz  # PyMuPDF: rasterise native PDFs and write the image-only scan PDF
import numpy as np
from PIL import Image, ImageFilter

from tools.eval.layouts import base

SCAN_DPI = 150
PHOTO_RASTER_DPI = 200
PHOTO_LONG_SIDE = 1800
PHOTO_PAGE_FRACTION = 0.85
MAX_TILT_DEG = 8.0
MAX_PHOTO_BLUR = 1.2
PHOTO_JPEG_Q = (60, 85)
SCAN_JPEG_Q = (70, 90)
PHOTO_MAX_BYTES = 10 * 1024 * 1024
ROT90_FRACTION = 0.05
_FIXED_DATE = "D:20260101000000+00'00'"


# ---------------------------------------------------------------- native

def native_pdf(report: base.PrintedReport, path: str) -> None:
    """Native rendering: reportlab, standard-14 fonts, ``invariant=1`` (byte-deterministic)."""
    base.write_pdf(report.pages, report.page_size, path, title=f"{report.lab['name']} - Laboratory Report")


# ---------------------------------------------------------------- helpers

def _rng(seed: int, page: int, salt: int = 0) -> np.random.Generator:
    return np.random.Generator(np.random.PCG64(np.random.SeedSequence([int(seed), int(page), int(salt)])))


def _rasterise(pdf_path: str, dpi: int, gray: bool) -> list[tuple[Image.Image, tuple[float, float]]]:
    out = []
    with fitz.open(pdf_path) as doc:
        for page in doc:
            m = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            pix = page.get_pixmap(matrix=m, colorspace=fitz.csGRAY if gray else fitz.csRGB, alpha=False)
            img = Image.frombytes("L" if gray else "RGB", (pix.width, pix.height), pix.samples)
            out.append((img, (page.rect.width, page.rect.height)))
    return out


def _jpeg_roundtrip(arr: np.ndarray, q: int) -> tuple[np.ndarray, bytes]:
    img = Image.fromarray(np.clip(np.rint(arr), 0, 255).astype(np.uint8))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=int(q), optimize=False)
    data = buf.getvalue()
    return np.asarray(Image.open(io.BytesIO(data))), data


def _inv_coeffs(h_src_to_dst: np.ndarray) -> tuple[float, ...]:
    """Pillow PERSPECTIVE coefficients (output -> input) for a source-to-output homography."""
    inv = np.linalg.inv(h_src_to_dst)
    inv = inv / inv[2, 2]
    return tuple(float(v) for v in inv.reshape(-1)[:8])


def map_points(h: Any, pts: list[tuple[float, float]]) -> list[tuple[float, float]]:
    hm = np.asarray(h, dtype=float)
    out = []
    for x, y in pts:
        v = hm @ np.array([x, y, 1.0])
        out.append((float(v[0] / v[2]), float(v[1] / v[2])))
    return out


def map_bbox(bbox: list[float], h: Any) -> list[float]:
    """Axis-aligned bounding box, in output pixels, of a PDF-point bbox mapped through homography ``h``."""
    x0, y0, x1, y1 = bbox
    q = map_points(h, [(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
    xs, ys = [p[0] for p in q], [p[1] for p in q]
    return [min(xs), min(ys), max(xs), max(ys)]


def _hom_list(h: np.ndarray) -> list[list[float]]:
    return [[float(v) for v in row] for row in np.asarray(h, dtype=float)]


def render_meta(rendering: str, seed: int, pages: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """Assemble the per-report rendering record (parameters + homographies) stored beside the image files."""
    meta: dict[str, Any] = {"rendering": rendering, "seed": int(seed), "pages": pages}
    meta.update(extra)
    return meta


# ---------------------------------------------------------------- scan (+ rot90)

def _scan_page(img: Image.Image, rng: np.random.Generator) -> tuple[np.ndarray, bytes, dict[str, Any], np.ndarray]:
    w, h = img.size
    skew = float(rng.uniform(-1.0, 1.0))
    p = {
        "skew_deg": skew,
        "brightness": float(rng.uniform(-0.05, 0.05)),
        "contrast": float(rng.uniform(-0.05, 0.05)),
        "noise_sigma": float(rng.uniform(2.0, 6.0)),
        "speckle_density": float(rng.uniform(0.0002, 0.001)),
        "blur_sigma": float(rng.uniform(0.0, 0.6)),
        "streak": bool(rng.random() < 0.10),
        "jpeg_q": int(rng.integers(SCAN_JPEG_Q[0], SCAN_JPEG_Q[1] + 1)),
    }
    rot = img.rotate(skew, resample=Image.BICUBIC, fillcolor=255)
    a = rot.getchannel(0) if rot.mode != "L" else rot
    if p["blur_sigma"] > 0.05:
        a = a.filter(ImageFilter.GaussianBlur(p["blur_sigma"]))
    x = np.asarray(a, dtype=np.float32)
    x = (x - 128.0) * (1.0 + p["contrast"]) + 128.0
    x = x * (1.0 + p["brightness"])
    x = x + rng.normal(0.0, p["noise_sigma"], x.shape).astype(np.float32)
    spk = rng.random(x.shape) < p["speckle_density"]
    x[spk] = rng.uniform(0, 90, int(spk.sum())).astype(np.float32)
    if p["streak"]:
        sx = int(rng.integers(w // 10, w - w // 10))
        sw = int(rng.integers(1, 4))
        x[:, sx:sx + sw] -= float(rng.uniform(8, 20))
        p["streak_x"] = sx
    arr, data = _jpeg_roundtrip(x, p["jpeg_q"])
    # pt -> px: scale, then counter-clockwise rotation by `skew` about the image centre (y down)
    s = SCAN_DPI / 72.0
    c, sn = math.cos(math.radians(skew)), math.sin(math.radians(skew))
    cx, cy = w / 2.0, h / 2.0
    rot_m = np.array([[c, sn, cx - c * cx - sn * cy], [-sn, c, cy + sn * cx - c * cy], [0, 0, 1.0]])
    hom = rot_m @ np.diag([s, s, 1.0])
    return arr, data, p, hom


def render_scan(native_path: str, out_path: str, seed: int, rot90: int | None = None) -> dict[str, Any]:
    """Image-only PDF at 150 dpi grayscale with scan noise; ``rot90`` in {+90, -90, None} rotates the page images
    (counter-clockwise for +90) onto a landscape page with no text layer."""
    if rot90 not in (None, 90, -90):
        raise ValueError("rot90 must be None, 90 or -90")
    out = fitz.open()
    pages_meta = []
    for n, (img, (wpt, hpt)) in enumerate(_rasterise(native_path, SCAN_DPI, gray=True), start=1):
        arr, data, p, hom = _scan_page(img, _rng(seed, n))
        h_px, w_px = arr.shape
        if rot90:
            im = Image.fromarray(arr).transpose(Image.ROTATE_90 if rot90 == 90 else Image.ROTATE_270)
            if rot90 == 90:  # (x, y) -> (y, W - x)
                t = np.array([[0, 1, 0], [-1, 0, w_px], [0, 0, 1.0]])
            else:  # (x, y) -> (H - y, x)
                t = np.array([[0, -1, h_px], [1, 0, 0], [0, 0, 1.0]])
            hom = t @ hom
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=p["jpeg_q"], optimize=False)  # lossless-ish re-encode of rotation
            data = buf.getvalue()
            size_pt = (hpt, wpt)
            size_px = [h_px, w_px]
        else:
            size_pt = (wpt, hpt)
            size_px = [w_px, h_px]
        page = out.new_page(width=size_pt[0], height=size_pt[1])
        page.insert_image(page.rect, stream=data)
        pages_meta.append({"page": n, "size_px": size_px, "size_pt": [size_pt[0], size_pt[1]],
                           "dpi": SCAN_DPI, "rot90": rot90 or 0, "homography": _hom_list(hom), "params": p})
    out.set_metadata({"producer": "vaidya-eval", "creator": "", "title": "", "author": "", "subject": "",
                      "keywords": "", "creationDate": _FIXED_DATE, "modDate": _FIXED_DATE})
    out.save(out_path, no_new_id=True, garbage=0, deflate=False)
    out.close()
    return render_meta("rot90" if rot90 else "scan", seed, pages_meta)


# ---------------------------------------------------------------- photo

def _smooth_noise(rng: np.random.Generator, h: int, w: int, cells: int) -> np.ndarray:
    small = rng.random((max(2, cells * h // max(h, w)), max(2, cells * w // max(h, w)))).astype(np.float32)
    im = Image.fromarray((small * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC)
    return np.asarray(im, dtype=np.float32) / 255.0


def _desk(rng: np.random.Generator, h: int, w: int) -> np.ndarray:
    base_rgb = np.array([rng.uniform(95, 150), rng.uniform(70, 110), rng.uniform(45, 80)], dtype=np.float32)
    tex = 0.6 * _smooth_noise(rng, h, w, 6) + 0.4 * _smooth_noise(rng, h, w, 24)
    grain = rng.normal(0, 4.0, (h, w)).astype(np.float32)
    out = base_rgb[None, None, :] * (0.75 + 0.5 * tex[..., None]) + grain[..., None]
    return out


def _rotation(axis: np.ndarray, ang: float) -> np.ndarray:
    ax = axis / np.linalg.norm(axis)
    k = np.array([[0, -ax[2], ax[1]], [ax[2], 0, -ax[0]], [-ax[1], ax[0], 0]])
    return np.eye(3) + math.sin(ang) * k + (1 - math.cos(ang)) * (k @ k)


def photo_tilt_deg(hom: Any, focal_px: float, size_px: tuple[int, int]) -> float:
    """Out-of-plane tilt (degrees) of the page plane recovered from a pt->pixel homography and the pinhole intrinsics."""
    kmat = np.array([[focal_px, 0, size_px[0] / 2.0], [0, focal_px, size_px[1] / 2.0], [0, 0, 1.0]])
    m = np.linalg.inv(kmat) @ np.asarray(hom, dtype=float)
    r1, r2 = m[:, 0] / np.linalg.norm(m[:, 0]), m[:, 1] / np.linalg.norm(m[:, 1])
    n = np.cross(r1, r2)
    return float(math.degrees(math.acos(min(1.0, abs(n[2]) / np.linalg.norm(n)))))


def _photo_page(img: Image.Image, wpt: float, hpt: float, rng: np.random.Generator) -> tuple[np.ndarray, bytes, dict, np.ndarray]:
    # frame: same aspect as the page, long side 1800 px, page ~85% of the frame
    long_pt = max(wpt, hpt)
    fw = int(round(PHOTO_LONG_SIDE * wpt / long_pt))
    fh = int(round(PHOTO_LONG_SIDE * hpt / long_pt))
    s = PHOTO_PAGE_FRACTION * fw / wpt  # pt -> final px at the plate centre
    src = img.resize((max(1, round(img.width * s * 72 / PHOTO_RASTER_DPI)),
                      max(1, round(img.height * s * 72 / PHOTO_RASTER_DPI))), Image.LANCZOS)
    s_src = src.width / wpt  # source px per pt (actual, after rounding)
    m = float(rng.uniform(0.0, MAX_TILT_DEG))
    phi = float(rng.uniform(0, 2 * math.pi))
    gamma = float(rng.uniform(-3.0, 3.0))
    fmul = float(rng.uniform(1.0, 1.3))
    f = fmul * fw
    rt = _rotation(np.array([math.cos(phi), math.sin(phi), 0.0]), math.radians(m))
    rz = _rotation(np.array([0, 0, 1.0]), math.radians(gamma))
    rm = rz @ rt
    kmat = np.array([[f, 0, fw / 2.0], [0, f, fh / 2.0], [0, 0, 1.0]])
    plane = np.column_stack([rm[:, 0], rm[:, 1], np.array([0, 0, f])])
    t_c = np.array([[1, 0, -src.width / 2.0], [0, 1, -src.height / 2.0], [0, 0, 1.0]])
    h_src = kmat @ plane @ t_c
    coeffs = _inv_coeffs(h_src)
    warped = np.asarray(src.convert("RGB").transform((fw, fh), Image.PERSPECTIVE, coeffs, Image.BICUBIC), dtype=np.float32)
    mask = np.asarray(Image.new("L", src.size, 255).transform((fw, fh), Image.PERSPECTIVE, coeffs, Image.BILINEAR),
                      dtype=np.float32)[..., None] / 255.0
    desk = _desk(rng, fh, fw)
    comp = warped * mask + desk * (1.0 - mask)
    # uneven light
    p = {"tilt_deg": m, "tilt_dir_deg": math.degrees(phi), "inplane_deg": gamma, "focal_mult": fmul,
         "gradient_min": float(rng.uniform(0.65, 0.9)), "vignette_min": float(rng.uniform(0.8, 1.0)),
         "shadow": bool(rng.random() < 0.30), "colour_cast": [float(v) for v in rng.uniform(-0.05, 0.05, 3)],
         "blur_sigma": float(rng.uniform(0.0, MAX_PHOTO_BLUR)), "noise_sigma": float(rng.uniform(1.0, 4.0)),
         "jpeg_q": int(rng.integers(PHOTO_JPEG_Q[0], PHOTO_JPEG_Q[1] + 1))}
    ang = float(rng.uniform(0, 2 * math.pi))
    yy, xx = np.mgrid[0:fh, 0:fw].astype(np.float32)
    proj = ((xx - fw / 2) * math.cos(ang) + (yy - fh / 2) * math.sin(ang)) / (0.5 * math.hypot(fw, fh))
    grad = 1.0 - (1.0 - p["gradient_min"]) * (proj + 1.0) / 2.0
    rr = np.hypot((xx - fw / 2) / (fw / 2), (yy - fh / 2) / (fh / 2)) / math.sqrt(2)
    vig = 1.0 - (1.0 - p["vignette_min"]) * rr ** 2
    light = grad * vig
    if p["shadow"]:
        c0 = float(rng.uniform(-0.5, 0.5))
        wd = float(rng.uniform(0.08, 0.2))
        band = np.exp(-(((proj - c0) / wd) ** 2))
        light = light * (1.0 - float(rng.uniform(0.12, 0.3)) * band)
    comp = comp * light[..., None] * (1.0 + np.array(p["colour_cast"], dtype=np.float32))[None, None, :]
    img2 = Image.fromarray(np.clip(np.rint(comp), 0, 255).astype(np.uint8))
    if p["blur_sigma"] > 0.05:
        img2 = img2.filter(ImageFilter.GaussianBlur(p["blur_sigma"]))
    x = np.asarray(img2, dtype=np.float32) + rng.normal(0, p["noise_sigma"], (fh, fw, 3)).astype(np.float32)
    arr, _ = _jpeg_roundtrip(x, p["jpeg_q"])
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG", compress_level=6, optimize=False)
    hom = h_src @ np.diag([s_src, s_src, 1.0])
    p.update({"focal_px": f, "size_px": [fw, fh]})
    return arr, buf.getvalue(), p, hom


def render_photo(native_path: str, out_stem: str, seed: int) -> dict[str, Any]:
    """One phone-photo PNG per page, written to ``{out_stem}.p{n}.png``; returns the meta (paths in ``files``)."""
    pages_meta, files = [], []
    for n, (img, (wpt, hpt)) in enumerate(_rasterise(native_path, PHOTO_RASTER_DPI, gray=False), start=1):
        _arr, png, p, hom = _photo_page(img, wpt, hpt, _rng(seed, n))
        if len(png) >= PHOTO_MAX_BYTES:
            raise ValueError(f"photo PNG {len(png)} bytes exceeds the 10 MB limit")
        path = f"{out_stem}.p{n}.png"
        with open(path, "wb") as fh:
            fh.write(png)
        files.append(path)
        pages_meta.append({"page": n, "size_px": p["size_px"], "size_pt": [wpt, hpt], "png_bytes": len(png),
                           "homography": _hom_list(hom), "params": p})
    return render_meta("photo", seed, pages_meta, files=files)


# ---------------------------------------------------------------- rot90 selection

def choose_rot90(items: list[tuple[str, str]], seed: int, fraction: float = ROT90_FRACTION) -> dict[str, int]:
    """Pick exactly ``round(fraction * n)`` reports (stratified round-robin over families in seeded order) and a
    seeded direction (+90 or -90) for each. ``items`` are ``(report_id, family)`` of the non-CSV reports."""
    rng = random.Random(seed)
    k = int(len(items) * fraction + 0.5)
    by_fam: dict[str, list[str]] = {}
    for rid, fam in sorted(items):
        by_fam.setdefault(fam, []).append(rid)
    fams = sorted(by_fam)
    rng.shuffle(fams)
    for f in fams:
        rng.shuffle(by_fam[f])
    chosen: list[str] = []
    while len(chosen) < k:
        progressed = False
        for f in fams:
            if by_fam[f] and len(chosen) < k:
                chosen.append(by_fam[f].pop())
                progressed = True
        if not progressed:
            break
    return {rid: rng.choice((90, -90)) for rid in sorted(chosen)}


__all__ = ["choose_rot90", "map_bbox", "map_points", "native_pdf", "photo_tilt_deg", "render_meta", "render_photo",
           "render_scan"]
