"""Disk memo of ``app.services.ocr_service.ocr_image`` for the eval harness (P1 plan section 6.12).

Adapter-side module: it never imports ``app`` at module level. It wraps whatever ``ocr_image`` callable it is
given (normally the product function, installed by ``PipelineV0Adapter`` around each job), so everything around
Paddle (CLAHE + denoise in ``preprocess_image``, ``lines_from_boxes``, the native-versus-OCR choice) still runs live
and the cache never hides a product change.

Key: SHA-256 over the fingerprint, the identity of the wrapped callable, and the preprocessed image (dtype, shape,
bytes). The fingerprint covers SHA-256 of ``data/paddleocr/**`` (the weights), the paddleocr and paddlepaddle
versions, SHA-256 of ``app/services/ocr_service.py`` and a schema salt, so a model or OCR-code change misses.
Including the wrapped callable's qualified name keeps the key space of a test stub apart from the real engine's,
so an in-process stub can never poison the real cache.

Value: the list of ``{text, confidence, bbox}`` dicts, as JSON, at ``<root>/<key[:2]>/<key>.json``, written
atomically (temp file + rename) so parallel workers never read a partial entry. A miss returns the same
JSON-normalised value a later hit returns, so a cold and a warm run see identical OCR output.
"""

from __future__ import annotations

import functools
import hashlib
import importlib.metadata
import json
import os
import pathlib
import tempfile
from collections.abc import Callable
from typing import Any

from tools.eval import config as cfg

CACHE_SCHEMA = "vaidya-eval-ocr-cache/1"
DEFAULT_DIR = cfg.EVAL_DIR / ".cache" / "ocr"
MODEL_ROOT = cfg.REPO_ROOT / "data" / "paddleocr"
OCR_SERVICE_FILE = cfg.REPO_ROOT / "app" / "services" / "ocr_service.py"
_VERSIONED = ("paddleocr", "paddlepaddle")


def _sha256_file(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_digest(root: pathlib.Path) -> str:
    """SHA-256 over (relative path, file SHA-256) of every file under ``root``, in sorted order."""
    h = hashlib.sha256()
    root = pathlib.Path(root)
    files = sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(_sha256_file(p).encode())
        h.update(b"\n")
    return h.hexdigest()


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "absent"


def fingerprint_parts(
    model_root: str | os.PathLike = MODEL_ROOT,
    service_file: str | os.PathLike = OCR_SERVICE_FILE,
    salt: str = CACHE_SCHEMA,
) -> dict[str, str]:
    service = pathlib.Path(service_file)
    return {
        "models": tree_digest(pathlib.Path(model_root)),
        "ocr_service": _sha256_file(service) if service.exists() else "absent",
        **{f"version:{n}": package_version(n) for n in _VERSIONED},
        "salt": salt,
    }


def compute_fingerprint(
    model_root: str | os.PathLike = MODEL_ROOT,
    service_file: str | os.PathLike = OCR_SERVICE_FILE,
    salt: str = CACHE_SCHEMA,
) -> str:
    """The cache fingerprint (hex SHA-256). Any change to the weights, the OCR packages or ocr_service.py moves it."""
    parts = fingerprint_parts(model_root, service_file, salt)
    blob = json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


def image_digest(image: Any) -> str:
    """SHA-256 of a numpy image: dtype, shape and C-contiguous bytes."""
    import numpy as np  # local: keeps this module importable without numpy for the registry

    arr = np.ascontiguousarray(image)
    h = hashlib.sha256()
    h.update(arr.dtype.str.encode())
    h.update(repr(tuple(arr.shape)).encode())
    h.update(arr.tobytes())
    return h.hexdigest()


def callable_id(fn: Callable[..., Any]) -> str:
    return f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', getattr(fn, '__name__', '?'))}"


def _json_default(o: Any) -> Any:
    item = getattr(o, "item", None)  # numpy scalar
    if callable(item):
        return item()
    tolist = getattr(o, "tolist", None)  # numpy array
    if callable(tolist):
        return tolist()
    raise TypeError(f"OCR result value of type {type(o).__name__} is not JSON-serialisable")


def normalise(lines: Any) -> list[dict[str, Any]]:
    """The JSON round trip of an OCR result (tuples become lists, numpy scalars become floats)."""
    return json.loads(json.dumps(lines, default=_json_default))


class OcrCache:
    """Content-addressed OCR memo. ``wrap(fn)`` returns a drop-in replacement for ``ocr_image``."""

    def __init__(self, root: str | os.PathLike = DEFAULT_DIR, *, fingerprint: str | None = None) -> None:
        self.root = pathlib.Path(root)
        self._fingerprint = fingerprint
        self.hits = 0
        self.misses = 0

    @property
    def fingerprint(self) -> str:
        if self._fingerprint is None:
            self._fingerprint = compute_fingerprint()
        return self._fingerprint

    def key(self, image: Any, fn_id: str) -> str:
        h = hashlib.sha256()
        for part in (CACHE_SCHEMA, self.fingerprint, fn_id, image_digest(image)):
            h.update(part.encode())
            h.update(b"|")
        return h.hexdigest()

    def path_for(self, key: str) -> pathlib.Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> list[dict[str, Any]] | None:
        path = self.path_for(key)
        try:
            with open(path, encoding="utf-8") as fh:
                entry = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None
        if not isinstance(entry, dict) or entry.get("schema") != CACHE_SCHEMA or entry.get("key") != key:
            return None
        lines = entry.get("lines")
        return lines if isinstance(lines, list) else None

    def put(self, key: str, lines: list[dict[str, Any]]) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {"schema": CACHE_SCHEMA, "key": key, "fingerprint": self.fingerprint, "lines": lines}
        fd, tmp = tempfile.mkstemp(prefix=".tmp-", suffix=".json", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(entry, fh, sort_keys=True, separators=(",", ":"))
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def wrap(self, fn: Callable[[Any], Any]) -> Callable[[Any], list[dict[str, Any]]]:
        fn_id = callable_id(fn)

        @functools.wraps(fn)
        def cached_ocr_image(image: Any) -> list[dict[str, Any]]:
            key = self.key(image, fn_id)
            hit = self.get(key)
            if hit is not None:
                self.hits += 1
                return hit
            self.misses += 1
            lines = normalise(fn(image))  # an exception propagates and nothing is stored
            self.put(key, lines)
            return normalise(lines)

        cached_ocr_image.__wrapped_cache__ = self  # type: ignore[attr-defined]
        return cached_ocr_image

    def stats(self) -> dict[str, int]:
        return {"hits": self.hits, "misses": self.misses}


__all__ = [
    "CACHE_SCHEMA", "DEFAULT_DIR", "OcrCache", "callable_id", "compute_fingerprint", "fingerprint_parts",
    "image_digest", "normalise", "tree_digest",
]
