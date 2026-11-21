"""Heldout hygiene guard (P1 plan 6.11).

Judge-side module: imports nothing from ``app`` or ``tools.eval.adapters`` (GN-11).

* ``guard_per_report_output(split)`` is the one choke point for anything per-report (failure dumps, job records
  on disk, debug JSON, shard job records); it raises ``HygieneError`` for heldout.
* ``redact_heldout(cell)`` keeps only a whitelist of aggregate keys and raises on any other key.
* ``quiet()`` silences loggers, stdout and stderr (Python level and file-descriptor level) during a heldout job.
* ``crash_class(exc)`` is the only thing ever recorded about an exception in heldout: its class name.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
from typing import Any, Iterator, Mapping

HELDOUT = "heldout"
METRIC_KEYS = ("M1", "M2", "M3", "M4", "M5", "M6", "M7")
RATE_KEYS = frozenset({"num", "den", "value", "ci_low", "ci_high", "target", "pass"})
COMPONENT_KEYS = frozenset({"wrong", "invented", "omitted"})
CELL_KEYS = frozenset(METRIC_KEYS) | {"components", "legibility", "jobs", "crashes"}
_CLASS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,80}$")


class HygieneError(RuntimeError):
    """A heldout rule was violated (exit code 3)."""


def is_heldout(split: str) -> bool:
    return split == HELDOUT


def guard_per_report_output(split: str) -> None:
    """Call before writing or printing anything per report. Raises for heldout."""
    if is_heldout(split):
        raise HygieneError("per-report output is refused for the heldout split (aggregate metrics only)")


def check_cli(splits: list[str], dump_failures: str | None) -> None:
    """The CLI rejects ``--dump-failures`` with heldout before doing any work."""
    if dump_failures and any(is_heldout(s) for s in splits):
        raise HygieneError("--dump-failures is refused when the heldout split is selected")


def crash_class(exc: BaseException) -> str:
    """Exception class name only: no message, no traceback, no arguments."""
    name = type(exc).__name__
    return name if _CLASS_NAME.match(name) else "Exception"


def _check_keys(where: str, got: Any, allowed: frozenset[str] | set[str]) -> None:
    extra = sorted(set(got) - set(allowed))
    if extra:
        raise HygieneError(f"heldout {where}: key(s) not in the whitelist: {', '.join(map(str, extra))}")


def redact_heldout(cell: Mapping[str, Any]) -> dict[str, Any]:
    """Whitelist one heldout rendering cell: M1-M7 ``{num, den, value, ci_low, ci_high, target, pass}``, the M1
    component counts, legibility, the job count and the crash counts by class name. Any other key raises."""
    _check_keys("cell", cell, CELL_KEYS)
    out: dict[str, Any] = {}
    for m in METRIC_KEYS:
        if m in cell:
            _check_keys(m, cell[m], RATE_KEYS)
            out[m] = {k: cell[m][k] for k in sorted(cell[m])}
    if "legibility" in cell:
        _check_keys("legibility", cell["legibility"], RATE_KEYS)
        out["legibility"] = {k: cell["legibility"][k] for k in sorted(cell["legibility"])}
    if "components" in cell:
        comp = cell["components"]
        _check_keys("components", comp, {"M1"})
        out["components"] = {}
        if "M1" in comp:
            _check_keys("components.M1", comp["M1"], COMPONENT_KEYS)
            out["components"]["M1"] = {k: int(comp["M1"][k]) for k in sorted(comp["M1"])}
    if "jobs" in cell:
        out["jobs"] = int(cell["jobs"])
    if "crashes" in cell:
        crashes = {}
        for name, n in cell["crashes"].items():
            if not _CLASS_NAME.match(str(name)):
                raise HygieneError("heldout crashes: key is not an exception class name")
            crashes[str(name)] = int(n)
        out["crashes"] = dict(sorted(crashes.items()))
    return out


def redact_heldout_results(results: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """``{rendering: cell}`` of the heldout split, every cell whitelisted."""
    return {r: redact_heldout(c) for r, c in sorted(results.items())}


def scan_for_leaks(text: str, forbidden: list[str]) -> list[int]:
    """Indices of ``forbidden`` strings found (case-insensitive) in ``text``, plus -1 if a heldout report id
    (``H`` and three digits) appears. Never returns the text itself."""
    low = text.lower()
    hits = [i for i, f in enumerate(forbidden) if f and f.lower() in low]
    if re.search(r"\bH\d{3}\b", text):
        hits.append(-1)
    return hits


@contextlib.contextmanager
def quiet() -> Iterator[None]:
    """Disable logging and send stdout and stderr to ``os.devnull`` (Python level and fd level) for the block."""
    prev_level = logging.root.manager.disable
    logging.disable(logging.CRITICAL)
    saved_fds: list[tuple[int, int]] = []
    devnull = open(os.devnull, "w")  # noqa: SIM115 - closed in finally
    try:
        for stream in (sys.stdout, sys.stderr):
            with contextlib.suppress(Exception):
                stream.flush()
        for fd in (1, 2):
            try:
                saved_fds.append((fd, os.dup(fd)))
                os.dup2(devnull.fileno(), fd)
            except OSError:
                pass
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            yield
    finally:
        for fd, saved in saved_fds:
            with contextlib.suppress(OSError):
                os.dup2(saved, fd)
                os.close(saved)
        devnull.close()
        logging.disable(prev_level)


__all__ = [
    "CELL_KEYS", "HELDOUT", "HygieneError", "check_cli", "crash_class", "guard_per_report_output", "is_heldout",
    "quiet", "redact_heldout", "redact_heldout_results", "scan_for_leaks",
]
